"""Offline compilation, explicit approval overlay, and verified runtime loading."""
from __future__ import annotations
import copy
from pathlib import Path
from .common import fields, object_sha, read_json, sha256, write_json


def compile_graph(graph, approvals=None):
    graph_hash = object_sha(graph)
    entries = (approvals or {}).get("decisions", [])
    scope = (approvals or {}).get("scope", "PRODUCTION")
    if approvals:
        if str(approvals.get('proposal_status', '')).startswith('AWAITING'):
            raise ValueError('Draft proposals cannot authorize compilation')
        if approvals.get("graph_content_sha256") != graph_hash: raise ValueError("Approval graph digest mismatch")
        if not approvals.get("reviewer"): raise ValueError("Approval requires reviewer identity")
        if scope not in {"PRODUCTION", "SYNTHETIC_ONLY"}: raise ValueError("Invalid approval scope")
    by_id = {e["edge_id"]: e for e in graph["dependencies"]}
    if len(by_id) != len(graph["dependencies"]): raise ValueError("Duplicate dependency ID")
    decisions, approved, report = {}, [], []
    for d in entries:
        id_ = d["edge_id"]
        if id_ in decisions or id_ not in by_id: raise ValueError("Duplicate or unknown approval edge: " + id_)
        decisions[id_] = d
        if d.get("decision") not in {"APPROVED", "REJECTED", "PENDING"}: raise ValueError("Unknown operator decision")
    for id_, original in by_id.items():
        d = decisions.get(id_, {})
        if d.get("decision") != "APPROVED":
            if d: report.append({"edge_id": id_, "decision": d["decision"], "scope": scope})
            continue
        e = copy.deepcopy(original)
        if e["validation_status"] != "VERIFIED": raise ValueError("Cannot approve technically unverified edge: " + id_)
        if scope=='PRODUCTION':
            from .validation import validate_edge
            status,_=validate_edge(e,graph['tools'])
            if status!='VERIFIED':raise ValueError('Approved edge failed current technical validation: '+id_)
        if graph["tools"][e["producer_tool"]]["kind"] != "read": raise ValueError("Prerequisite must be READ")
        if e["target_argument_path"] not in {f["path"] for f in fields(graph["tools"][e["consumer_tool"]]["parameters"])}:
            raise ValueError("Approval target is not in schema")
        target_spec = next(f for f in fields(graph['tools'][e['consumer_tool']]['parameters'])
                           if f['path'] == e['target_argument_path'])
        if target_spec['type'] in {'array','object'}:
            raise ValueError('Collection construction is not supported for runtime approval')
        context_edge = e.get('dependency_type') == 'CONTEXT_PREREQUISITE'
        if context_edge and e.get('context_requirement') not in {'chat_referenced_track','chat_group_quantity'}:
            raise ValueError('Unsupported context prerequisite')
        known_conditions = {"slot_unresolved_or_ungrounded", "required_argument", "optional_requires_goal_intent",
                            "alternative_must_be_unfulfilled", "explicit_user_value_allowed"}
        if set(e["required_when"]) - known_conditions: raise ValueError("Unsupported runtime condition requires resolver implementation")
        if d.get("source_output_path") != e["source_output_path"]: raise ValueError("Approval must confirm exact source path")
        selectors = d.get("selector_keys", [])
        allowed = e["subject_binding_rule"]["allowed_selector_keys"]
        if not selectors or not set(selectors) <= set(allowed): raise ValueError("Approval must confirm valid selector keys")
        required_selectors = d.get('required_selector_keys', [])
        if not set(required_selectors) <= set(selectors): raise ValueError('Required selector keys must be approved')
        duplicate_policy = d.get('duplicate_subject_policy')
        if duplicate_policy not in {None, 'ABSTAIN'}: raise ValueError('Unsupported duplicate subject policy')
        consumer_inputs = d.get('consumer_input_requirements', [])
        consumer_fields = {f['path'] for f in fields(graph['tools'][e['consumer_tool']]['parameters'])}
        if not set(consumer_inputs) <= consumer_fields: raise ValueError('Unknown required consumer input')
        if d.get("read_cost") not in {"LOW", "NORMAL", "HIGH"}: raise ValueError("Approval must review read cost")
        if not d.get("reason"): raise ValueError("Approval requires review rationale")
        e["operator_decision"], e["runtime_enabled"] = "APPROVED", True
        e["runtime_scope"] = scope
        e["subject_binding_rule"]["allowed_selector_keys"] = selectors
        if required_selectors: e['subject_binding_rule']['required_selector_keys'] = required_selectors
        if duplicate_policy: e['subject_binding_rule']['duplicate_subject_policy'] = duplicate_policy
        if consumer_inputs: e['consumer_input_requirements'] = consumer_inputs
        if d.get('record_filters'):
            for key, values in d['record_filters'].items():
                if key not in allowed or not isinstance(values, list) or not values or any(isinstance(v, (dict,list)) for v in values):
                    raise ValueError('Invalid record predicate')
            e['record_filters'] = d['record_filters']
        e["producer_argument_bindings"] = d.get("producer_argument_bindings", {})
        props = graph["tools"][e["producer_tool"]]["parameters"].get("properties", {})
        for arg, binding in e["producer_argument_bindings"].items():
            if arg not in props or set(binding) != {"subject_key"} or binding["subject_key"] not in selectors:
                raise ValueError("Invalid producer argument selector binding")
        e["read_cost"] = d["read_cost"]
        e["approval"] = {"reviewer": approvals["reviewer"], "reason": d["reason"], "scope": scope}
        approved.append(e)
        report.append({"edge_id": id_, "decision": "APPROVED", "scope": scope})
    # Only operational metadata enters runtime: no train IDs, gold calls/results, sample values.
    operational_keys = ["edge_id", "producer_tool", "consumer_tool", "source_output_path", "target_argument_path",
                        "dependency_type", "required_when", "alternative_branches", "subject_type", "subject_binding_rule",
                        "selection_strategy", "evidence_tier", "validation_status", "operator_decision", "runtime_enabled",
                        "risk_level", "runtime_scope", "producer_argument_bindings", "read_cost", "approval"]
    edges = {e["edge_id"]: {k: e[k] for k in operational_keys} for e in approved}
    for e in approved:
        for key in ['consumer_input_requirements','record_filters','context_requirement']:
            if e.get(key): edges[e['edge_id']][key] = e[key]
    consumer, producer, arguments, contexts = {}, {}, {}, {}
    for id_, e in edges.items():
        consumer.setdefault(e["consumer_tool"], []).append(id_)
        producer.setdefault(e["producer_tool"], []).append(id_)
        if e.get('context_requirement'):
            contexts.setdefault(e['consumer_tool'], []).append(id_)
        else:
            arguments.setdefault(e["consumer_tool"], {}).setdefault(e["target_argument_path"], []).append(id_)
    tools = graph["tools"]
    payload = {"version": graph["version"], "schema_version": 3, "graph_content_sha256": graph_hash,
               "catalog_digest": graph["catalog_digest"], "runtime_scope": scope,
               "tools": {n: {"name": n, "kind": t["kind"], "parameters": t["parameters"]} for n,t in tools.items()},
               "edges": edges, "consumer_index": consumer, "producer_index": producer, "argument_dependencies": arguments,
               "context_dependencies": contexts,
               "alternative_branches": {n: t["parameters"].get("x-one-of", []) for n,t in tools.items()},
               "preconditions": {n: {"required": t["parameters"].get("required", []),
                    "freshness": "write_epoch", "subject_binding": "explicit_goal_bound_unique_record"} for n,t in tools.items()},
               "validated_source_paths": {id_: e["source_output_path"] for id_,e in edges.items()},
               "shadow_index": {n: [{"edge_id": e["edge_id"], "producer": e["producer_tool"],
                    "target": e["target_argument_path"], "validation_status": e["validation_status"],
                    "runtime_enabled": e["edge_id"] in edges} for e in graph["dependencies"] if e["consumer_tool"] == n]
                    for n in tools}}
    compiled = {**payload, "payload_sha256": object_sha(payload)}
    runtime_graph = {"version": graph["version"], "graph_content_sha256": graph_hash, "scope": scope,
                     "dependencies": approved, "approval_overlay": approvals,
                     "note": "Empty until an operator reviews explicit source path, subject keys, cost and argument bindings."}
    return compiled, runtime_graph, report


def verify_compiled(compiled, catalog_digest=None, scope="PRODUCTION"):
    payload = {k: v for k, v in compiled.items() if k != "payload_sha256"}
    if object_sha(payload) != compiled.get("payload_sha256"): raise ValueError("Compiled graph checksum mismatch")
    if compiled.get("schema_version") != 3: raise ValueError("Unsupported graph schema")
    if catalog_digest and compiled["catalog_digest"] != catalog_digest: raise ValueError("Runtime catalog digest mismatch")
    if compiled["runtime_scope"] != scope: raise ValueError("Synthetic graph cannot run in production")
    expected_consumer, expected_producer, expected_args, expected_context = {}, {}, {}, {}
    for id_, e in compiled["edges"].items():
        if id_ != e["edge_id"] or not e["runtime_enabled"] or e["operator_decision"] != "APPROVED" or e["validation_status"] != "VERIFIED":
            raise ValueError("Unauthorized runtime edge")
        if e["runtime_scope"] != scope or compiled["tools"][e["producer_tool"]]["kind"] != "read":
            raise ValueError("Unsafe prerequisite producer")
        if compiled["validated_source_paths"].get(id_) != e["source_output_path"]:
            raise ValueError("Validated source path index mismatch")
        expected_consumer.setdefault(e["consumer_tool"], []).append(id_)
        expected_producer.setdefault(e["producer_tool"], []).append(id_)
        if e.get('context_requirement'):
            expected_context.setdefault(e['consumer_tool'], []).append(id_)
        else:
            expected_args.setdefault(e["consumer_tool"], {}).setdefault(e["target_argument_path"], []).append(id_)
    if expected_consumer != compiled["consumer_index"] or expected_producer != compiled["producer_index"] or expected_args != compiled["argument_dependencies"]:
        raise ValueError("Compiled indexes do not match approved edges")
    if expected_context != compiled.get('context_dependencies', {}): raise ValueError('Context index mismatch')
    return compiled


def load_compiled(path, manifest_path=None, catalog_path=None, scope="PRODUCTION"):
    path = Path(path)
    if manifest_path:
        manifest = read_json(manifest_path)
        if manifest["artifacts_sha256"].get(path.name) != sha256(path): raise ValueError("Artifact manifest digest mismatch")
    return verify_compiled(read_json(path), sha256(catalog_path) if catalog_path else None, scope)


class VerifiedCompiledGraph:
    """Validated once per loaded harness, then shared read-only by request controllers.

    Owns a private copy so a caller cannot mutate its original dict after validation.
    Runtime code must not mutate payload; request state lives in EvidenceLedger/ExecutionState.
    """
    def __init__(self, compiled, scope="PRODUCTION"):
        self.scope = scope
        self._payload = copy.deepcopy(verify_compiled(compiled, scope=scope))


def compile_existing(graph_path, manifest_path, catalog_path, approvals_path, output):
    """Compile a frozen graph and explicit authorization; never mine/rebuild/train-read."""
    graph_path, manifest_path, output = Path(graph_path).resolve(), Path(manifest_path).resolve(), Path(output).resolve()
    if output == graph_path.parent or output in graph_path.parents:
        raise ValueError('Compile output must not overwrite the source graph or its parent')
    source_manifest = read_json(manifest_path)
    if source_manifest['artifacts_sha256'].get(graph_path.name) != sha256(graph_path):
        raise ValueError('Source graph file digest mismatch')
    graph = read_json(graph_path)
    if object_sha(graph) != source_manifest['graph_content_sha256']: raise ValueError('Source graph content mismatch')
    if graph['catalog_digest'] != sha256(catalog_path): raise ValueError('Catalog digest mismatch')
    approvals = read_json(approvals_path)
    compiled, approved, report = compile_graph(graph, approvals)
    if not compiled['edges']: raise ValueError('No authorized edges; approve concrete decisions before compile')
    verify_compiled(compiled, graph['catalog_digest'], approvals.get('scope', 'PRODUCTION'))
    if output.exists() and any(output.iterdir()): raise ValueError('Use a new/empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    write_json(output/'compiled_graph_v3.json', compiled)
    write_json(output/'approved_runtime_graph.json', approved)
    write_json(output/'operator_approvals.json', approvals)
    write_json(output/'compile_report.json', {'status':'PASS','graph_rebuilt':False,'decisions':report,
        'runtime_approved_edges':len(compiled['edges']),'source_graph_sha256':sha256(graph_path)})
    files = ['compiled_graph_v3.json','approved_runtime_graph.json','operator_approvals.json','compile_report.json']
    manifest = {'version':graph['version'], 'schema_version':3, 'graph_content_sha256':object_sha(graph),
        'catalog_digest':graph['catalog_digest'], 'source_graph_file_sha256':sha256(graph_path),
        'source_manifest_sha256':sha256(manifest_path), 'graph_rebuilt':False,
        'approval_overlay_sha256':sha256(approvals_path), 'compiled_payload_sha256':compiled['payload_sha256'],
        'runtime_enabled_coverage':{'dependencies':len(compiled['edges']),'consumer_tools':len(compiled['consumer_index'])},
        'implementation_sha256':{p.name:sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        'artifacts_sha256':{name:sha256(output/name) for name in files}}
    write_json(output/'graph_manifest.json', manifest)
    return manifest
