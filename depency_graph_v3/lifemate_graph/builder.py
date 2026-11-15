"""Offline-only catalog audit and evidence mining. Never imported by resolver."""
from __future__ import annotations
import collections
import re
from pathlib import Path
from .common import (canonical, equal, fields, leaves, path_entries, read_csv, read_json,
                     schema_errors, sha256, tool_index, value_type, write_csv, write_json, object_sha)

VERSION = "lifemate-full-dependency-graph-v3.0.0"
TOOL_MENTION = re.compile(r"\b[a-z_]+\.[a-z_][a-z_0-9]*\b")


def input_inventory(root):
    root = Path(root)
    original = read_json(root / "manifest.json")
    records = []
    for entry in original:
        # The recovered archive name differs from this local folder name.
        relative = Path(*Path(entry["path"]).parts[1:])
        p = root / relative
        records.append({"path": relative.as_posix(), "exists": p.is_file(),
                        "sha256": sha256(p) if p.is_file() else None,
                        "expected_sha256": entry["sha256"],
                        "bytes_match": p.is_file() and p.stat().st_size == entry["bytes"]})
    for name in ["train_tools_catalog.json", "train.jsonl"]:
        p = root / "inputs" / name
        records.append({"path": "inputs/" + name, "exists": p.is_file(),
                        "sha256": sha256(p) if p.is_file() else None, "expected_sha256": None,
                        "bytes_match": None, "baseline": "first v3 build; not in recovered manifest"})
    bad = [r for r in records if not r["exists"] or
           (r["expected_sha256"] and (r["sha256"] != r["expected_sha256"] or not r["bytes_match"]))]
    if bad: raise ValueError("Missing or modified source files: " + canonical(bad))
    return records


def legacy_artifacts(root):
    p = Path(root) / "previous_graph"
    legacy = {f.name: read_json(f) if f.suffix == ".json" else read_csv(f) for f in sorted(p.iterdir())}
    counts = {"structural_edges": len(legacy["validated_graph.json"]["edges"]),
              "metadata_candidates": len(legacy["metadata_value_flow_evidence.csv"]),
              "empirical_candidates": len(legacy["empirical_enrichment_candidates.csv"]),
              "manual_review_relationships": len(legacy["manual_review_queue_v2.csv"]),
              "curated_edges": len(legacy["curated_graph_v21.json"]["edges"]),
              "legacy_schema_fields": len(legacy["schema_fields.csv"]),
              "legacy_alternative_branches": len(legacy["one_of_alternatives.json"])}
    return legacy, counts


def scan_train(path, tools):
    """Read actual JSONL, audit ALL rows, mine only aggregate offline observations."""
    observations = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    record_keys = collections.defaultdict(set)
    flow = collections.defaultdict(set)
    pair_order = collections.defaultdict(set)
    stats = collections.Counter()
    errors = []
    seen = set()
    # Detect by content and parse line by line. A .jsonl extension is never converted.
    with Path(path).open(encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip(): continue
            import json
            row = json.loads(line)
            if not isinstance(row, dict): raise ValueError(f"Line {line_no}: expected JSON object")
            sid = row.get("id")
            if not isinstance(sid, str) or sid in seen: raise ValueError(f"Invalid/duplicate id at line {line_no}")
            seen.add(sid)
            stats["samples"] += 1
            turns = [("history", h) for h in row.get("history", []) if h.get("role") == "assistant"]
            turns.append(("label", row.get("label", {})))
            for origin, turn in turns:
                calls, results = turn.get("tool_calls", []), turn.get("tool_results", [])
                if len(calls) != len(results):
                    errors.append({"sample": sid, "origin": origin, "error": "unaligned calls/results"})
                    continue
                prior_reads = []
                for call, result in zip(calls, results):
                    name = call.get("name")
                    stats[origin + "_calls"] += 1
                    if name not in tools:
                        errors.append({"sample": sid, "error": "unknown tool", "tool": name})
                        continue
                    errs = schema_errors(call.get("arguments", {}), tools[name]["parameters"])
                    if errs: errors.append({"sample": sid, "origin": origin, "tool": name, "errors": errs})
                    stats[origin + "_failed_results"] += bool(not isinstance(result, dict) or result.get("ok") is not True)
                    if origin == "label" and name != "sys.ask_missing_info":
                        allowed = {n if "." in n else app + "." + n
                                   for app, x in row.get("context", {}).get("installed_apps", {}).items()
                                   for n in x.get("tools", [])}
                        if name not in allowed: errors.append({"sample": sid, "error": "not installed", "tool": name})
                    for producer, p_leaves in prior_reads:
                        if origin == "label": pair_order[(producer, name)].add(sid)
                        for target, val in leaves(call.get("arguments", {})):
                            if val is None or isinstance(val, bool): continue
                            for source, pv in p_leaves:
                                # Strict typed equality, NOT fuzzy name equality.
                                if type(val) is type(pv) and val == pv:
                                    flow[(producer, name, target, source)].add(sid)
                    if isinstance(result, dict) and result.get("ok") is True:
                        ls = list(leaves(result))
                        for source, val in ls:
                            observations[name][source][value_type(val)] += 1
                            for _, rec in path_entries(result, source):
                                if isinstance(rec, dict): record_keys[(name, source)].update(rec)
                        if tools[name]["kind"] == "read": prior_reads.append((name, ls))
    return observations, record_keys, flow, pair_order, dict(stats), errors


def build(root, out=None, approvals=None):
    from .validation import validate_graph
    from .compiler import compile_graph
    root = Path(root).resolve()
    out = Path(out) if out else root / "artifacts"
    out = out.resolve()
    if out == root or any(out == root / d or root / d in out.parents for d in ["inputs", "previous_graph", "reference_notebooks"]):
        raise ValueError("Output must not overwrite original sources")
    out.mkdir(parents=True, exist_ok=True)
    inventory = input_inventory(root)
    catalog = read_json(root / "inputs/train_tools_catalog.json")
    tools = tool_index(catalog)
    legacy, old_counts = legacy_artifacts(root)
    obs, record_keys, flow, order, stats, data_errors = scan_train(root / "inputs/train.jsonl", tools)
    digests = {r["path"]: r["sha256"] for r in inventory}
    digests["README_RECOVERED.md"] = sha256(root / "README_RECOVERED.md")
    digests["manifest.json"] = sha256(root / "manifest.json")
    for name in ["validated_graph.json", "curated_graph_v21.json"]:
        old = legacy[name]["source_digests"]
        if old.get("train_sha256") != digests["inputs/train.jsonl"] or old.get("catalog_sha256") != digests["inputs/train_tools_catalog.json"]:
            raise ValueError("Legacy source digest mismatch: " + name)

    nodes, relations, candidates = {}, [], {}
    def node(id_, type_, **kw):
        if "type" in kw: kw["value_type"] = kw.pop("type")
        nodes[id_] = {"id": id_, "type": type_, **kw}
        return id_
    def relation(a, b, type_, **kw):
        relations.append({"from": a, "to": b, "type": type_, **kw})
    def candidate(p, c, target, origin, record, tier="UNVERIFIED", path=None):
        key = (p, c, target)
        if key not in candidates:
            candidates[key] = {"producer_tool": p, "consumer_tool": c, "target_argument_path": target,
                               "evidence_sources": [], "source_output_path": None,
                               "contract_tier": "UNVERIFIED", "path_hints": []}
        e = candidates[key]
        e["evidence_sources"].append({"file": origin, "sha256": digests.get(origin), "record": record})
        if tier == "EXPLICIT_CONTRACT" or (e["contract_tier"] == "UNVERIFIED" and tier == "RESOURCE_RETURN_CANDIDATE"):
            e["contract_tier"] = tier
        if path and path not in e["path_hints"]: e["path_hints"].append(path)

    audits = []
    for name, t in tools.items():
        tid = node("tool:" + name, "TOOL", name=name, app=t["app"], kind=t["kind"].upper(), description=t["description"])
        fs = list(fields(t["parameters"]))
        for f in fs:
            if "[]" in f["path"] and (f["path"].endswith("_id") or f["path"].endswith("_id[]")):
                policy = "ROW_AND_PARENT_ENTITY_PROVENANCE_REQUIRED"
            elif f["x_ref"]:
                policy = "EXPLICIT_USER_IDENTIFIER_OR_UNIQUE_SUBJECT_MATCHED_OBSERVATION"
            elif TOOL_MENTION.search(f["description"]):
                policy = "EXPLICIT_USER_VALUE_OR_CONTRACT_SOURCE_WITH_SUBJECT_BINDING"
            elif f["path"] in {"phone", "phone_number"}:
                policy = "GOAL_SCOPED_USER_PHONE_OR_MATCHED_CONTACT"
            elif f["path"] == "amount":
                policy = "GOAL_SCOPED_USER_AMOUNT_OR_MATCHED_FINANCIAL_RECORD"
            elif f["schema"].get("enum"):
                policy = "USER_INTENT_SELECTS_CATALOG_ENUM_NO_PRODUCER_ASSUMED"
            elif f["schema"].get("format") in {"date", "date-time"}:
                policy = "USER_OR_SAMPLE_CONTEXT_TIME_WITH_EXPLICIT_DERIVATION"
            else:
                policy = "USER_INTENT_OR_OBSERVATION_REQUIRES_VALIDATED_PROVENANCE_WHEN_GROUNDED"
            f["provenance_policy"] = policy
            f["contract_named_sources"] = sorted(set(TOOL_MENTION.findall(f["description"])) & set(tools))
        resources = sorted({f["x_ref"] for f in fs if f["x_ref"]})
        for f in fs:
            aid = node("arg:" + name + ":" + f["path"], "ARGUMENT", tool=name, **f)
            relation(tid, aid, "CONSUMES")
            if f["x_ref"]:
                rid = node("resource:" + f["x_ref"], "RESOURCE", name=f["x_ref"])
                relation(aid, rid, "REFERENCES_RESOURCE")
                node("entity:" + f["x_ref"], "ENTITY_TYPE", resource=f["x_ref"])
            # A source named in a description is contract evidence even without x-ref.
            mentions = set(TOOL_MENTION.findall(f["description"])) & set(tools)
            for p in sorted(mentions):
                candidate(p, name, f["path"], "inputs/train_tools_catalog.json",
                          {"description": f["description"], "schema": f["schema"]}, "EXPLICIT_CONTRACT")
            if f["x_ref"]:
                app, resource = f["x_ref"].split(".", 1)
                for p, pt in tools.items():
                    if pt["kind"] == "read" and pt["app"] == app and resource in re.findall(r"[a-z_]+", str(pt.get("returns", ""))):
                        candidate(p, name, f["path"], "inputs/train_tools_catalog.json",
                                  {"x_ref": f["x_ref"], "returns": pt.get("returns")}, "RESOURCE_RETURN_CANDIDATE")
        for path, types in sorted(obs[name].items()):
            oid = node("output:" + name + ":" + path, "OUTPUT_FIELD", tool=name, path=path,
                       observed_types=dict(types), evidence_tier="TRAIN_OBSERVATION_ONLY")
            relation(tid, oid, "PRODUCES")
        for i, branch in enumerate(t["parameters"].get("x-one-of", [])):
            bid = node(f"precondition:{name}:alternative:{i}", "PRECONDITION", fields=branch)
            relation(tid, bid, "ALTERNATIVE_INPUT", semantics="at_least_one_branch; preserve ToolEnv validation")
        # A deliberately conservative runtime freshness policy, not a causal app relation.
        if t["kind"] == "write":
            node("precondition:write_epoch", "PRECONDITION", rule="all pre-write read evidence stale")
            relation(tid, "precondition:write_epoch", "INVALIDATES_EVIDENCE", evidence_tier="RUNTIME_SAFETY_POLICY")
        audits.append({"name": name, "app": t["app"], "description": t["description"], "kind": t["kind"].upper(),
                       "input_schema": t["parameters"], "output_contract": t.get("returns"),
                       "output_schema": t.get("output_schema"), "fields": fs,
                       "observed_outputs": {p: dict(types) for p, types in sorted(obs[name].items())},
                       "x_one_of": t["parameters"].get("x-one-of", []), "referenced_resources": resources,
                       "read_resources": t.get("returns", "") if t["kind"] == "read" else [],
                       "write_resources": resources if t["kind"] == "write" else [],
                       "resource_scope_limit": "x-ref identifies referenced entities, not a complete mutation/side-effect set",
                       "user_supplied_fields": [f["path"] for f in fs],
                       "observation_required_fields": [],
                       "observation_preferred_fields": [f["path"] for f in fs if f["x_ref"] or TOOL_MENTION.search(f["description"])],
                       "user_input_caveat": "Explicit IDs may come from the user; values are not inferred from field names. Contract enums and formats still apply.",
                       "preconditions": {"schema_required": t["parameters"].get("required", []),
                                         "subject": "explicit request-bound selectors for evidence-derived entity values",
                                         "execution": "available tool + ToolEnv.validate + existing guards",
                                         "semantic_description": t["description"]},
                       "audit_status": "AUDITED_CONTRACT_AND_ALL_AVAILABLE_OBSERVATIONS"})

    # Preserve raw rows and decisions, including duplicates, rather than treating a union as truth.
    for fname in ["metadata_value_flow_evidence.csv", "manual_review_queue_v2.csv", "empirical_enrichment_candidates.csv"]:
        for row in legacy[fname]:
            p, c = row.get("producer", row.get("reader")), row.get("consumer")
            target = row.get("consumer_field", row.get("consumer_field_candidate", ""))
            candidate(p, c, target, "previous_graph/" + fname, row)
    for row in legacy["validated_graph.json"]["enriched_dependencies"]:
        candidate(row["producer"], row["consumer"], row["consumer_field"], "previous_graph/validated_graph.json", row)
    curated = {}
    for row in legacy["curated_graph_v21.json"]["edges"]:
        key = (row["producer"], row["consumer"], row["consumer_field"])
        curated[key] = row
        candidate(*key, "previous_graph/curated_graph_v21.json", row, path=row.get("source_output_path"))

    # Discover newly observed flows, including READ -> READ and nested array input fields.
    for (p, c, target, path), samples in sorted(flow.items()):
        if len(samples) < 2 and (p, c, target) not in candidates: continue
        candidate(p, c, target, "inputs/train.jsonl",
                  {"kind": "OFFLINE_TYPED_EXACT_FLOW", "source_path": path, "sample_support": len(samples),
                   "sample_ids": sorted(samples)}, path=path)
    # Explicit semantic contracts that mention source in a tool description, not its field.
    semantic_contracts = [
        ("tv.guide", "tv.tune", "channel", "channel must be a member of channels; guide before tune"),
        ("food_delivery.list_restaurants", "ewallet.pay_qr", "merchant_id", "merchant_id of restaurant, not restaurant id"),
    ]
    for p, c, target, note in semantic_contracts:
        if p in tools and c in tools:
            candidate(p, c, target, "inputs/train_tools_catalog.json", {"contract": note,
                      "tool_description": tools[c]["description"]}, "EXPLICIT_CONTRACT")
    if "food_delivery.get_menu" in tools and "food_delivery.place_order" in tools:
        candidate("food_delivery.get_menu", "food_delivery.place_order", "items[].item_id", "inputs/Spec.md",
                  {"section": "Nested ID lacking x-ref", "contract": "food_delivery.items[].item_id must come from menu"}, "EXPLICIT_CONTRACT")

    dependencies = []
    for (p, c, target), e in sorted(candidates.items()):
        f = next((x for x in fields(tools.get(c, {}).get("parameters", {})) if x["path"] == target), None)
        path_candidates = {path for pp, cc, tt, path in flow if (pp, cc, tt) == (p, c, target)} | set(e["path_hints"])
        if e["contract_tier"] == "EXPLICIT_CONTRACT":
            # A source path may be proven by observations without ever being copied in gold.
            path_candidates.update(path for path in obs[p] if path != "ok")
        path_candidates = sorted(path_candidates)
        path_scores = [{"path": path, "exact_flow_samples": len(flow.get((p, c, target, path), [])),
                        "observed_types": dict(obs[p].get(path, {})),
                        "record_keys": sorted(record_keys.get((p, path), []))} for path in path_candidates]
        # Semantic narrowing is explicit, never merely choosing the most frequent field.
        contract_paths = []
        if f:
            resource = f["x_ref"].split(".", 1)[-1] if f["x_ref"] else None
            for x in path_scores:
                leaf = x["path"].split(".")[-1]
                is_identifier = target.endswith("_id") or target.endswith("_id[]")
                if is_identifier and resource and x["path"] == resource + "[].id":
                    contract_paths.append(x["path"])
                elif leaf == target.split(".")[-1] and not resource and leaf in f["description"]:
                    contract_paths.append(x["path"])
                elif target == "items[].item_id" and p == "food_delivery.get_menu" and leaf == "id":
                    contract_paths.append(x["path"])
                elif (f["type"] == "array" and f["schema"].get("items", {}).get("type") == "string") or target.endswith("[]"):
                    if ("email" in f["description"].casefold() and leaf == "email") or ("ID" in f["description"] and leaf == "id"):
                        contract_paths.append(x["path"])
        # Reviewed semantic distinctions: membership is not the resource's current selected value.
        semantic_paths = {
            ("appliances.list", "appliances.start_cycle", "cycle"): "appliances[].cycles[]",
            ("household_chores.list_chores", "household_chores.assign", "assignee"): "members[]",
            ("tv.guide", "tv.tune", "channel"): "channels[]",
            ("robot_vacuum.get_status", "robot_vacuum.start", "rooms"): "rooms[]",
            ("robot_vacuum.get_status", "robot_vacuum.start", "rooms[]"): "rooms[]",
            ("ride_hailing.get_ride", "ride_hailing.cancel", "ride_id"): "ride.id",
        }
        membership = (p,c,target) in semantic_paths and target != "ride_id"
        if (p,c,target) in semantic_paths:
            contract_paths = [semantic_paths[(p,c,target)]]
        old = curated.get((p, c, target))
        if old and old.get("source_output_path"): contract_paths = [old["source_output_path"]]
        selected = contract_paths[0] if len(set(contract_paths)) == 1 else None
        branches = tools.get(c, {}).get("parameters", {}).get("x-one-of", [])
        id_ = "dep:" + object_sha([p, c, target])[:20]
        subject = f["x_ref"] if f and f["x_ref"] else (old or {}).get("subject_type", "explicit_goal_subject")
        binding = {"strategy": "unique_record_matching_all_explicit_selectors", "require_goal_id": True,
                   "require_current_write_epoch": True, "selector_comparison": "NFC_casefold_exact_preserve_roles",
                   "allowed_selector_keys": sorted(set(record_keys.get((p, selected), [])) -
                       {"ok", "amount", "balance", "total", "total_due", "due_amount", "power", "brightness", "speed", "locked", "done", "status"}) if selected else [],
                   "require_producer_argument_consistency": True, "array_target": "model_decision_unless_reviewed_singleton"}
        dependencies.append({**e, "edge_id": id_, "source_output_path": selected,
            "dependency_type": "RESOURCE_STATE_PRECONDITION" if membership else "CONDITIONAL_DEPENDENCY" if target else "UNVERIFIED_RELATIONSHIP",
            "required_when": {"slot_unresolved_or_ungrounded": target, "required_argument": bool(f and f["required"]),
                              "optional_requires_goal_intent": bool(f and not f["required"]),
                              "alternative_must_be_unfulfilled": bool(any(target in b for b in branches)),
                              "explicit_user_value_allowed": True},
            "alternative_branches": branches, "subject_type": subject, "subject_binding_rule": binding,
            "selection_strategy": binding["strategy"], "candidate_output_paths": path_scores,
            "evidence_tier": e["contract_tier"], "validation_status": "INSUFFICIENT_EVIDENCE",
            "operator_decision": (old or {}).get("operator_decision", "PENDING"), "runtime_enabled": False,
            "risk_level": "HIGH" if tools.get(c, {}).get("kind") == "write" else "LOW",
            "producer_argument_bindings": {}, "read_cost": "UNREVIEWED",
            "notes": ["Exact value flow is not proof of semantic identity or a mandatory prerequisite.",
                      "All new and legacy PENDING edges remain runtime disabled; technical verification grants no permission."],
            "legacy_curated": old})

    graph = {"version": VERSION, "schema_version": 3, "source_digests": digests,
             "catalog_digest": digests["inputs/train_tools_catalog.json"], "train_digest": digests["inputs/train.jsonl"],
             "nodes": sorted(nodes.values(), key=lambda n: n["id"]), "relations": relations,
             "dependencies": dependencies, "tools": tools,
             "legacy_counts": old_counts, "data_audit": {"format": "JSONL", **stats, "schema_errors": data_errors},
             "provenance_archive": {"path": "legacy_provenance_v3.json", "all_original_rows_preserved": True},
             "runtime_policy": "explicit operator decision required for each dependency; graph never executes WRITE"}
    validation = validate_graph(graph)
    for e in dependencies:
        source = "output:" + e["producer_tool"] + ":" + str(e["source_output_path"])
        target_node = "arg:" + e["consumer_tool"] + ":" + e["target_argument_path"]
        if source in nodes and target_node in nodes:
            relation(source, target_node, "REQUIRES_DATA_FROM", edge_id=e["edge_id"],
                     validation_status=e["validation_status"], required_when=e["required_when"], runtime_enabled=False)
            cid = node("precondition:" + e["edge_id"], "PRECONDITION", required_when=e["required_when"])
            relation("tool:" + e["consumer_tool"], cid, "CONDITIONAL_DEPENDENCY", edge_id=e["edge_id"], runtime_enabled=False)
    graph["nodes"] = sorted(nodes.values(), key=lambda n: n["id"])
    validation["structural_relations"] = len(relations)
    compiled, approved, approval_report = compile_graph(graph, read_json(approvals) if approvals else None)
    validation["approval_report"] = approval_report
    audit = {"version": VERSION, "catalog_schema_version": catalog.get("schema_version"),
             "app_count": len(catalog["apps"]), "tool_count": len(tools), "tools": audits,
             "limitations": ["Catalog returns are free-text, not formal output schemas.",
                             "Observed schemas cover train/history outputs, not all possible live outputs.",
                             "User literals are possible for IDs; no field is automatically forced to a producer."]}
    write_json(out / "full_dependency_graph_v3.json", graph)
    write_json(out / "tool_registry_audit.json", audit)
    write_json(out / "legacy_provenance_v3.json", legacy)
    write_json(out / "graph_validation_report.json", validation)
    write_json(out / "approved_runtime_graph.json", approved)
    write_json(out / "compiled_graph_v3.json", compiled)
    write_json(out / "source_integrity_report.json", {"files": inventory, "source_digests": digests,
               "readme_missing_files_note_is_stale": True, "legacy_source_digests_match": True})
    coverage = []
    for t in audits:
        incoming = [e for e in dependencies if e["consumer_tool"] == t["name"]]
        outgoing = [e for e in dependencies if e["producer_tool"] == t["name"]]
        coverage.append({"tool": t["name"], "app": t["app"], "kind": t["kind"], "audited": True,
                         "argument_paths": len(t["fields"]), "incoming_candidates": len(incoming),
                         "outgoing_candidates": len(outgoing), "verified": sum(e["validation_status"] == "VERIFIED" for e in incoming),
                         "runtime_enabled": sum(e["edge_id"] in compiled["edges"] for e in incoming),
                         "no_dependency_explanation": "No contract/observed dependency established; standalone policy remains available" if not incoming and not outgoing else ""})
    write_csv(out / "tool_coverage.csv", coverage, list(coverage[0]))
    review = [{k: e.get(k) for k in ["edge_id", "producer_tool", "consumer_tool", "source_output_path", "target_argument_path",
               "contract_tier", "validation_status", "validation_reasons", "operator_decision", "runtime_enabled", "risk_level",
               "subject_binding_rule", "candidate_output_paths", "required_when", "alternative_branches"]} for e in dependencies]
    write_csv(out / "graph_review_queue_v3.csv", review, list(review[0]))
    output_hashes = {f.name: sha256(f) for f in sorted(out.iterdir()) if f.is_file() and f.name != "graph_manifest.json"}
    manifest = {"version": VERSION, "source_digests": digests, "artifacts_sha256": output_hashes,
                "implementation_sha256": {f.name: sha256(f) for f in sorted((root / "lifemate_graph").glob("*.py"))},
                "graph_content_sha256": object_sha(graph), "compiled_payload_sha256": compiled["payload_sha256"],
                "graph_discovery_coverage": {"apps": len(catalog["apps"]), "audited_tools": len(tools), "total_tools": len(tools),
                    "candidate_dependencies": len(dependencies), "statuses": validation["status_counts"]},
                "runtime_enabled_coverage": {"dependencies": len(approved["dependencies"]),
                    "consumer_tools": len(compiled["consumer_index"]), "operator_approval_required": True},
                "legacy_counts": old_counts, "execution": {"gpu_tested": False, "toolenv_available_locally": False}}
    write_json(out / "graph_manifest.json", manifest)
    return manifest
