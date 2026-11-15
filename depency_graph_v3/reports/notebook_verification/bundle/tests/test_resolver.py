import copy
import unittest
from lifemate_graph.runtime import (BackwardDependencyResolver, EvidenceLedger, ExecutionState, GoalContext,
                                    GraphRuntimeController, GraphMode, Status)
from lifemate_graph.compiler import verify_compiled
from lifemate_graph.common import object_sha
from tests.fixtures import compiled, edge, goal, literal, tool


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.graph = compiled()
        self.resolver = BackwardDependencyResolver(self.graph, "SYNTHETIC_ONLY")
        self.ledger = EvidenceLedger()
        self.state = ExecutionState()
        self.request = "Nhắn Mai hello"
        self.goal = goal(self.request)
    def resolve(self, args=None, **kw):
        return self.resolver.resolve(self.request, [self.goal], "demo.send", args or {"content": "hello"},
              kw.get("available", self.graph["tools"]), self.ledger, state=self.state, goal=self.goal)
    def observe(self, people=None, id_="g1", result=None, args=None):
        self.ledger.observe("demo.people", args or {"query": "Mai"}, result if result is not None else
            {"ok": True, "people": people if people is not None else [{"id": "p_mai", "name": "Mai"}]}, id_)
    def test_missing_argument_schedules_read(self):
        r=self.resolve(); self.assertEqual(r.status, Status.NEED_READ)
        self.assertEqual((r.read_tool,r.read_arguments), ("demo.people",{"query":"Mai"}))
    def test_existing_evidence_reused_without_read(self):
        self.observe(); r=self.resolve(); self.assertEqual(r.status,Status.SATISFIED)
        self.assertEqual(r.arguments["contact_id"],"p_mai"); self.assertEqual(r.proofs["contact_id"]["goal_id"],"g1")
    def test_all_args_grounded_no_extra_read(self):
        self.observe(); self.assertEqual(self.resolve({"contact_id":"p_mai","content":"hello"}).status,Status.SATISFIED)
    def test_literal_id_no_read(self):
        self.request="Nhắn p_mai hello"; self.goal.subjects={}
        self.assertEqual(self.resolve({"contact_id":"p_mai","content":"hello"}).status,Status.SATISFIED)
    def test_goal_other_evidence_not_used(self):
        self.observe(id_="g2"); self.assertEqual(self.resolve().status,Status.NEED_READ)
    def test_cross_goal_user_span_is_rejected(self):
        self.request="Nhắn An hello; nhắn Mai goodbye"
        self.goal=goal(self.request)
        self.goal.request_span=(0,13)
        self.assertEqual(self.resolve().status,Status.BLOCKED_UNSAFE)
    def test_wrong_subject_rejected(self):
        self.observe(); self.assertEqual(self.resolve({"contact_id":"p_other","content":"hello"}).status,Status.BLOCKED_UNSAFE)
    def test_literal_id_for_another_goal_is_not_subject_proof(self):
        self.request="Nhắn Mai hello; nhắn An p_other"
        self.goal=goal(self.request)
        self.observe()
        self.assertEqual(self.resolve({"contact_id":"p_other","content":"hello"}).status,Status.BLOCKED_UNSAFE)
    def test_ambiguous_same_name_abstains(self):
        self.observe([{"id":"p1","name":"Mai"},{"id":"p2","name":"Mai"}]); self.assertEqual(self.resolve().status,Status.AMBIGUOUS)
    def test_identical_rows_represent_one_entity(self):
        self.observe([{"id":"p1","name":"Mai"}]*2); self.assertEqual(self.resolve().status,Status.SATISFIED)
    def test_role_names_not_collapsed(self):
        self.observe([{"id":"p1","name":"Sếp Mai"}]); self.assertEqual(self.resolve().status,Status.UNRESOLVED)
    def test_failed_read_does_not_retry(self):
        self.observe(result={"ok":False}); self.assertEqual(self.resolve().status,Status.UNRESOLVED)
    def test_empty_read_does_not_retry(self):
        self.observe([]); self.assertEqual(self.resolve().status,Status.UNRESOLVED)
    def test_reserved_read_does_not_retry(self):
        r=self.resolve();self.state.mark_read_attempt("g1",r.read_tool,r.read_arguments,self.ledger.epoch)
        self.assertEqual(self.resolve().status,Status.UNRESOLVED)
    def test_uninstalled_producer(self):
        self.assertNotEqual(self.resolve(available={"demo.send"}).status,Status.NEED_READ)
    def test_write_invalidates_evidence(self):
        self.observe();self.ledger.observe("demo.write",{}, {"ok":True},"g2","write")
        self.assertEqual(self.resolve().status,Status.NEED_READ)
    def test_failed_write_preserves_evidence(self):
        self.observe();self.ledger.observe("demo.write",{}, {"ok":False},"g2","write")
        self.assertEqual(self.resolve().status,Status.SATISFIED)
    def test_noop_write_preserves_evidence(self):
        self.observe();self.ledger.observe("demo.write",{}, {"ok":True,"done":False},"g2","write")
        self.assertEqual(self.resolve().status,Status.SATISFIED)
    def test_condition_unknown_or_false_blocks(self):
        for condition in ["unknown","false"]:
            self.goal.condition=condition; self.assertEqual(self.resolve().status,Status.BLOCKED_UNSAFE)
    def test_fake_user_span_cannot_ground_selector(self):
        self.goal.subjects["contact_id"]["name"]=literal("Anh Mai hello","Mai")
        self.assertEqual(self.resolve().status,Status.BLOCKED_UNSAFE)
    def test_missing_subject_does_not_list_everyone(self):
        self.goal.subjects={};self.assertEqual(self.resolve().status,Status.NEED_MODEL_DECISION)
    def test_unrelated_query_does_not_supply_evidence(self):
        self.observe(args={"query":"Other"});self.assertEqual(self.resolve().status,Status.NEED_READ)
    def test_observed_output_type_checked_again(self):
        self.observe([{"id":42,"name":"Mai"}]);self.assertEqual(self.resolve().status,Status.BLOCKED_UNSAFE)
    def test_max_reads(self):
        self.state.max_reads=0;self.assertEqual(self.resolve().status,Status.UNRESOLVED)
    def test_max_depth(self):
        self.state.max_depth=1;self.assertEqual(self.resolve().status,Status.UNRESOLVED)
    def test_expensive_read_abstains(self):
        g=compiled(edges=[edge(cost="HIGH")]);self.resolver=BackwardDependencyResolver(g,"SYNTHETIC_ONLY")
        self.assertEqual(self.resolve().status,Status.NEED_MODEL_DECISION)
    def test_shadow_never_changes_proposal(self):
        c=GraphRuntimeController(self.graph,GraphMode.GRAPH_SHADOW,"SYNTHETIC_ONLY")
        r=c.inspect(self.request,[self.goal],"demo.send",{"content":"hello"},self.graph["tools"],self.goal)
        self.assertEqual(r.status,Status.NOT_APPLICABLE);self.assertEqual(c.events[0]["status"],"NEED_READ")
    def test_no_edge_tool_is_baseline(self):
        g=compiled(edges=[]);r=BackwardDependencyResolver(g,"SYNTHETIC_ONLY").resolve(self.request,[self.goal],"demo.send",
             {"content":"hello","contact_id":"unknown"},g["tools"],self.ledger,goal=self.goal)
        self.assertEqual(r.status,Status.NOT_APPLICABLE)
    def test_two_producers_need_model(self):
        ts=copy.deepcopy(self.graph["tools"]);ts["demo.other"]=tool("demo.other")
        es=[edge(),edge("demo.other",path="people[].id")]
        self.resolver=BackwardDependencyResolver(compiled(ts,es),"SYNTHETIC_ONLY")
        self.assertEqual(self.resolve(available=ts).status,Status.NEED_MODEL_DECISION)
    def test_alternative_phone_avoids_contact_read(self):
        ts=copy.deepcopy(self.graph["tools"])
        ts["demo.send"]["parameters"]["properties"]["phone"]={"type":"string"}
        ts["demo.send"]["parameters"]["required"]=["content"]
        ts["demo.send"]["parameters"]["x-one-of"]=[["contact_id"],["phone"]]
        self.resolver=BackwardDependencyResolver(compiled(ts),"SYNTHETIC_ONLY")
        self.request="Nhắn 0901234567 hello"
        r=self.resolve({"phone":"0901234567","content":"hello"});self.assertNotEqual(r.status,Status.NEED_READ)
    def test_multihop_read_order(self):
        ts=copy.deepcopy(self.graph["tools"])
        ts["demo.people"]["parameters"]["properties"]["region_id"]={"type":"string"}
        ts["demo.people"]["parameters"]["required"]=["region_id"]
        ts["demo.regions"]=tool("demo.regions")
        es=[edge(bindings={"query":{"subject_key":"name"}}),edge("demo.regions","demo.people","region_id","regions[].id")]
        self.graph=compiled(ts,es);self.resolver=BackwardDependencyResolver(self.graph,"SYNTHETIC_ONLY")
        self.request="Nhắn Mai ở North hello";self.goal=goal(self.request)
        self.goal.subjects["demo.people:region_id"]={"name":literal(self.request,"North")}
        self.assertEqual(self.resolve().read_tool,"demo.regions")
        self.ledger.observe("demo.regions",{}, {"ok":True,"regions":[{"id":"r1","name":"North"}]},"g1")
        r=self.resolve();self.assertEqual(r.read_tool,"demo.people");self.assertEqual(r.read_arguments,{"region_id":"r1","query":"Mai"})
        self.observe(args=r.read_arguments);self.assertEqual(self.resolve().status,Status.SATISFIED)
    def test_cycle_stops_safely(self):
        ts=copy.deepcopy(self.graph["tools"])
        ts["demo.people"]["parameters"]={"type":"object","properties":{"root_id":{"type":"string"}},"required":["root_id"]}
        ts["demo.root"]=tool("demo.root",props={"contact_id":{"type":"string"}},required=["contact_id"])
        es=[edge(),edge("demo.root","demo.people","root_id","roots[].id"),edge("demo.people","demo.root","contact_id")]
        self.resolver=BackwardDependencyResolver(compiled(ts,es),"SYNTHETIC_ONLY")
        self.goal.subjects["root_id"]={"name":literal(self.request,"Mai")}
        self.assertEqual(self.resolve(available=ts).status,Status.UNRESOLVED)
    def test_compiled_integrity_and_scope(self):
        with self.assertRaises(ValueError):verify_compiled(self.graph)
        changed=copy.deepcopy(self.graph);changed["edges"].clear()
        with self.assertRaises(ValueError):verify_compiled(changed,scope="SYNTHETIC_ONLY")
    def test_index_tamper_even_after_rehash_rejected(self):
        changed=copy.deepcopy(self.graph);changed["consumer_index"].clear()
        changed["payload_sha256"]=object_sha({k:v for k,v in changed.items() if k!="payload_sha256"})
        with self.assertRaises(ValueError):verify_compiled(changed,scope="SYNTHETIC_ONLY")


if __name__ == "__main__": unittest.main()
