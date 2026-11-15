"""Entirely synthetic contracts and approval grants. Never production approvals."""
from lifemate_graph.common import object_sha
from lifemate_graph.compiler import compile_graph
from lifemate_graph.runtime import GroundedValue, GoalContext


def tool(name, kind="read", props=None, required=None, alternatives=None):
    schema = {"type": "object", "properties": props or {}, "required": required or []}
    if alternatives: schema["x-one-of"] = alternatives
    return {"name": name, "kind": kind, "parameters": schema}


def edge(p="demo.people", c="demo.send", target="contact_id", path="people[].id", keys=None, bindings=None, cost="LOW"):
    return {"edge_id": "fixture:" + object_sha([p,c,target,path])[:12], "producer_tool": p, "consumer_tool": c,
            "target_argument_path": target, "source_output_path": path, "dependency_type": "CONDITIONAL_DEPENDENCY",
            "required_when": {"slot_unresolved_or_ungrounded": target}, "alternative_branches": [],
            "subject_type": "synthetic_person", "subject_binding_rule": {"allowed_selector_keys": keys or ["id", "name"]},
            "selection_strategy": "unique_record_matching_all_explicit_selectors", "evidence_tier": "SYNTHETIC_CONTRACT",
            "validation_status": "VERIFIED", "operator_decision": "PENDING", "runtime_enabled": False,
            "risk_level": "TEST_ONLY", "_bindings": bindings or {}, "_cost": cost}


def compiled(tools=None, edges=None):
    tools = tools or {
        "demo.people": tool("demo.people", props={"query": {"type": "string"}}),
        "demo.send": tool("demo.send", "write", {"contact_id": {"type": "string", "x-ref": "demo.people"},
                                                 "content": {"type": "string"}}, ["contact_id", "content"]),
    }
    edges = edges if edges is not None else [edge(bindings={"query": {"subject_key": "name"}})]
    g = {"version": "SYNTHETIC_FIXTURE_V3", "catalog_digest": object_sha(tools), "tools": tools, "dependencies": edges}
    approval = {"graph_content_sha256": object_sha(g), "reviewer": "automated synthetic fixture author",
                "scope": "SYNTHETIC_ONLY", "decisions": [
                    {"edge_id": e["edge_id"], "decision": "APPROVED", "source_output_path": e["source_output_path"],
                     "selector_keys": e["subject_binding_rule"]["allowed_selector_keys"], "read_cost": e["_cost"],
                     "producer_argument_bindings": e["_bindings"], "reason": "Synthetic fixture only; cannot run against live ToolEnv"}
                    for e in edges]}
    return compile_graph(g, approval)[0]


def literal(request, value):
    start = request.index(str(value))
    return GroundedValue(value, start, start + len(str(value)))


def goal(request="Nhắn Mai hello", id_="g1", target="demo.send", slot="contact_id", name="Mai"):
    return GoalContext(id_, target, {slot: {"name": literal(request, name)}})


class FakeToolEnv:
    """Strict schema validation and a visible call journal, no hidden state APIs."""
    tools = {}
    responses = {}
    last = None
    instances = 0
    def __init__(self, *args, **kwargs):
        type(self).last = self; type(self).instances += 1
        self.calls, self.validations = [], []
    def validate(self, name, args):
        from lifemate_graph.common import schema_errors
        errors = schema_errors(args, self.tools[name]["parameters"])
        self.validations.append({"name": name, "arguments": args.copy(), "errors": errors})
        return {"ok": not errors, "errors": errors}
    def call(self, name, args):
        import copy
        self.calls.append({"name": name, "arguments": copy.deepcopy(args)})
        response = self.responses.get(name, {"ok": True, "done": True})
        return copy.deepcopy(response(args) if callable(response) else response)
    def get_tool_calls(self): return self.calls
    def get_state_hash(self): return "synthetic-state-" + str(len(self.calls))
