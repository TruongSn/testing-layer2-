"""Technical validation is deliberately independent from runtime authorization."""
from __future__ import annotations
from collections import Counter, defaultdict
from .common import fields


def graph_cycles(dependencies):
    # Strongly connected components; report candidate cycles, including disabled ones.
    adjacency = defaultdict(set)
    for e in dependencies:
        adjacency[e["consumer_tool"]].add(e["producer_tool"])
    index, low, stack, active, components = {}, {}, [], set(), []
    def visit(v):
        index[v] = low[v] = len(index)
        stack.append(v); active.add(v)
        for w in sorted(adjacency.get(v, [])):
            if w not in index:
                visit(w); low[v] = min(low[v], low[w])
            elif w in active: low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group = []
            while True:
                w = stack.pop(); active.remove(w); group.append(w)
                if w == v: break
            if len(group) > 1 or v in adjacency[v]: components.append(sorted(group))
    for v in sorted(list(adjacency)):
        if v not in index: visit(v)
    return components


def validate_edge(e, tools):
    p, c, target = e["producer_tool"], e["consumer_tool"], e["target_argument_path"]
    reasons = []
    if p not in tools or c not in tools: return "REJECTED", ["tool_not_in_catalog"]
    if p == c: return "REJECTED", ["self_dependency_cannot_bootstrap_itself"]
    fs = {f["path"]: f for f in fields(tools[c]["parameters"])}
    if target and target not in fs: return "REJECTED", ["target_argument_path_not_in_schema"]
    if not target: return "NEEDS_REVIEW", ["order_only_does_not_establish_causality_or_predicate"]
    if tools[p]["kind"] != "read": return "REJECTED", ["automatic_prerequisite_must_be_read"]
    path = e.get("source_output_path")
    if not path: return "INSUFFICIENT_EVIDENCE", ["no_unique_contract_supported_output_path"]
    info = next((x for x in e["candidate_output_paths"] if x["path"] == path), {})
    types = set(info.get("observed_types", {})) - {"null"}
    if not types: return "INSUFFICIENT_EVIDENCE", ["source_output_path_not_observed"]
    if e.get('dependency_type')=='CONTEXT_PREREQUISITE':
        contracts={
            'chat_referenced_track':('chat_ott.read_chat','music.add_to_playlist','messages[].content','track_id'),
            'chat_group_quantity':('chat_ott.list_chats','food_delivery.place_order','chats[].members[]','items[].quantity')}
        if contracts.get(e.get('context_requirement'))!=(p,c,path,target) or e.get('contract_tier')!='EXPLICIT_TASK_CONDITION' or types!={'string'}:
            return 'NEEDS_REVIEW',['unsupported_or_unproven_context_predicate']
        return 'VERIFIED',['conditional_reference_contract_not_cooccurrence','observed_source_path_and_executable_reference_validator','no_direct_value_copy_to_target']
    expected = fs[target]["type"]
    if expected == "array": expected = fs[target]["schema"].get("items", {}).get("type")
    if not types <= ({expected, "integer"} if expected == "number" else {expected}):
        return "REJECTED", ["observed_source_type_incompatible_with_target"]
    if e["contract_tier"] != "EXPLICIT_CONTRACT": reasons.append("resource_token_or_exact_value_is_not_semantic_proof")
    if e["dependency_type"] == "RESOURCE_STATE_PRECONDITION": reasons.append("membership_predicate_requires_parent_subject_review_not_value_copy")
    if not info.get("record_keys"): reasons.append("subject_binding_has_no_observed_record_shape")
    if "[]" in target and target not in {'to[]','attendees[]','items[].item_id'}:
        reasons.append("nested_array_row_and_parent_entity_binding_require_review")
    if reasons: return "NEEDS_REVIEW", reasons
    return "VERIFIED", ["explicit_catalog_contract_and_observed_typed_path",
                        "runtime_requires_unique_goal_bound_selection_and_explicit_operator_approval"]


def validate_graph(graph):
    tools = graph["tools"]
    for e in graph["dependencies"]:
        e["validation_status"], e["validation_reasons"] = validate_edge(e, tools)
        # A validator never authorizes deployment.
        e["runtime_enabled"] = False
    cycles = graph_cycles(graph["dependencies"])
    return {"version": graph["version"], "status_counts": dict(Counter(e["validation_status"] for e in graph["dependencies"])),
            "candidate_cycles": cycles, "cycle_policy": "Runtime recursion stack + maximum depth; self edges rejected",
            "tool_coverage": {"audited": len(tools), "total": len(tools)},
            "structural_relations": len(graph["relations"]), "dependency_candidates": len(graph["dependencies"]),
            "data_schema_audit": graph["data_audit"],
            "technical_verified_is_runtime_approved": False,
            "limitations": ["No hidden state, GPU execution, or official ToolEnv validation was performed locally.",
                            "Resource state requirements without an explicit predicate remain review candidates.",
                            "Semantic identity must be checked again against current goal at runtime."]}
