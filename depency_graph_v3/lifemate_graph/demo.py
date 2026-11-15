"""CPU closed-loop demo through the actual B042 loop and its checked B043 hooks."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from .b043 import B043Harness
from .runtime import GraphMode
from .common import write_json


def run_scenario(mode=GraphMode.GRAPH_ASSISTED_SAFE_READ, root=None, people=None, responses=None, proposals=None,
                 graph=None, request="Nhắn Mai hello", plan=None, reject_tools=()):
    # Test grants are intentionally SYNTHETIC_ONLY and never touch production artifacts.
    from tests.fixtures import compiled, FakeToolEnv
    root = Path(root or Path(__file__).resolve().parents[1])
    graph = graph or compiled()
    harness = B043Harness(root / "inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb", graph, mode, "SYNTHETIC_ONLY")
    m = harness.module
    m.CONFIG["max_stop_reviews"] = 0
    item = {"id": "synthetic_b043", "audio_text": request, "history": [],
            "context": {"installed_apps": {"demo": {"tools": list(graph["tools"])}}}}
    plan = plan or [{"goal_id": "g1", "completion_tool": "demo.send", "description": item["audio_text"],
             "conditional": False, "status": "pending", "observed_step": None,
             "graph_context": {"subjects": {"contact_id": {"name": {"value": "Mai", "start": 5, "end": 8}}}}}]
    def make(*args, **kw):
        return copy.deepcopy(plan), {"input_tokens": 10, "output_tokens": 10, "model_seconds": 0.001}
    m.make_goals = make
    sequence = iter(proposals or [{"action": "call", "name": "demo.send", "arguments": {"content": "hello"}}, {"action": "stop"}])
    def generate(*args, **kw): return json.dumps(next(sequence, {"action": "stop"}), ensure_ascii=False), 10, 10, 0.001
    m.generate_once = generate
    class Env(FakeToolEnv):
        def validate(self, name, args):
            if name in reject_tools:
                self.validations.append({"name": name, "arguments": args.copy(), "errors": ["synthetic ToolEnv rejection"]})
                return {"ok": False, "error": "synthetic ToolEnv rejection"}
            return super().validate(name, args)
    Env.tools = graph["tools"]
    Env.responses = responses or {"demo.people": {"ok": True, "people": people if people is not None else [{"id": "p_mai", "name": "Mai"}]}}
    result = harness.run(item, graph["tools"], Env, root)
    result["validation_audit"] = Env.last.validations
    result["synthetic_only"] = True
    return result


def run_demo(root=None, output=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    results = {"baseline": run_scenario(GraphMode.GRAPH_DISABLED, root),
               "shadow": run_scenario(GraphMode.GRAPH_SHADOW, root),
               "assisted": run_scenario(GraphMode.GRAPH_ASSISTED_SAFE_READ, root),
               "ambiguous": run_scenario(root=root, people=[{"id": "p1", "name": "Mai"}, {"id": "p2", "name": "Mai"}]),
               "empty": run_scenario(root=root, people=[]),
               "failed": run_scenario(root=root, responses={"demo.people": {"ok": False, "error": "synthetic failure"}})}
    calls = results["assisted"]["public"]["tool_calls"]
    assert [c["name"] for c in calls] == ["demo.people", "demo.send"]
    assert calls[-1]["arguments"]["contact_id"] == "p_mai"
    assert len(results["assisted"]["validation_audit"]) == 2
    for case in ["ambiguous", "empty", "failed"]:
        assert not any(c["name"] == "demo.send" for c in results[case]["public"]["tool_calls"])
        assert len(results[case]["public"]["tool_calls"]) <= 1
    assert results["shadow"]["public"]["tool_calls"] == results["baseline"]["public"]["tool_calls"]
    report = {"status": "PASS", "environment": "CPU scripted policy / FakeToolEnv / real B042 loop with B043 hooks",
              "production_edges_approved": 0, "synthetic_only": True,
              "scenarios": {k: {"calls": r["public"]["tool_calls"], "stop_reason": r["diagnostics"]["stop_reason"],
                                 "graph_reads": r["diagnostics"]["graph_reads"], "graph_events": r["private_debug"]["graph_events"]}
                            for k,r in results.items()},
              "claims": {"graph_generated_read": True, "evidence_reused_after_read": True,
                         "gpu_accuracy_improvement_measured": False, "real_toolenv_tested": False}}
    write_json(output or root / "reports/closed_loop_demo.json", report)
    return report


if __name__ == "__main__": print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
