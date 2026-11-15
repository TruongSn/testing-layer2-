"""Generic, bounded backward resolver. No model, train reader, or executor here."""
from __future__ import annotations
import copy
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from .common import canonical, equal, field_equal, opaque_field, fields, get_path, norm, path_entries, schema_errors, set_path, value_type, schema_path, leaves
from .compiler import verify_compiled, VerifiedCompiledGraph


class Status(str, Enum):
    SATISFIED = "SATISFIED"
    NEED_READ = "NEED_READ"
    NEED_MODEL_DECISION = "NEED_MODEL_DECISION"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GraphMode(str, Enum):
    GRAPH_DISABLED = "GRAPH_DISABLED"
    GRAPH_SHADOW = "GRAPH_SHADOW"
    GRAPH_ASSISTED_SAFE_READ = "GRAPH_ASSISTED_SAFE_READ"


@dataclass(frozen=True)
class GroundedValue:
    value: object
    start: int
    end: int
    # Planner JSON cannot forge observed authority: from_dict only accepts user spans.
    observation_sequence: int | None = None
    observation_path: str | None = None
    observation_text: str | None = None

    def valid(self, request):
        if not (0 <= self.start < self.end <= len(request)): return False
        literal = request[self.start:self.end]
        if isinstance(self.value, str): return norm(literal) == norm(self.value)
        if type(self.value) in {int, float}: return literal == str(self.value)
        if isinstance(self.value, bool): return literal.casefold() == str(self.value).casefold()
        return False

    @classmethod
    def from_dict(cls, d):
        return cls(d["value"], int(d["start"]), int(d["end"]))


@dataclass
class GoalContext:
    goal_id: str
    target_tool: str
    # Keys may be tool:path, allowing different entity types within multi-hop chains.
    subjects: dict[str, dict[str, GroundedValue]] = field(default_factory=dict)
    bindings: dict[str, dict[str, GroundedValue]] = field(default_factory=dict)
    required_optional: set[str] = field(default_factory=set)
    selected_alternatives: dict[str, list[str]] = field(default_factory=dict)
    condition: str = "true"  # unknown/false are explicit abstentions
    request_span: tuple[int, int] | None = None
    context_requirements: dict = field(default_factory=dict)

    def selectors(self, tool, path):
        return self.subjects.get(tool + ":" + path, self.subjects.get(path, {}))

    def valid_value(self, value, request, path='', ledger=None):
        if value.observation_sequence is not None:
            if ledger is None or not 0 <= value.observation_sequence < len(ledger.observations): return False
            o = ledger.observations[value.observation_sequence]
            if o.epoch != ledger.epoch or o.goal_id != self.goal_id or not isinstance(o.result, dict) or o.result.get('ok') is not True: return False
            texts = [text for text, _ in path_entries(o.result, value.observation_path or '')]
            text = value.observation_text
            return (isinstance(text, str) and text in texts and 0 <= value.start < value.end <= len(text)
                    and field_equal(path, text[value.start:value.end], value.value))
        return value.valid(request) and (not opaque_field(path) or request[value.start:value.end] == value.value) and (self.request_span is None or
               0 <= self.request_span[0] <= value.start < value.end <= self.request_span[1] <= len(request))


@dataclass
class Observation:
    tool: str
    arguments: dict
    result: object
    goal_id: str | None
    epoch: int
    sequence: int


class EvidenceLedger:
    """Stores only observed results with provenance and a conservative write epoch."""
    def __init__(self):
        self.epoch = 0
        self.observations: list[Observation] = []

    def observe(self, tool, arguments, result, goal_id, kind="read"):
        success = isinstance(result, dict) and result.get("ok") is True and result.get("done") is not False
        if kind == "write" and success: self.epoch += 1
        self.observations.append(Observation(tool, copy.deepcopy(arguments), copy.deepcopy(result), goal_id,
                                             self.epoch, len(self.observations)))

    def current(self, tool, goal_id, include_unscoped=False):
        return [o for o in self.observations if o.tool == tool and o.epoch == self.epoch and
                (o.goal_id == goal_id or include_unscoped and o.goal_id is None)]


@dataclass
class ExecutionState:
    graph_reads: int = 0
    attempted: set = field(default_factory=set)
    max_depth: int = 5
    max_reads: int = 4
    max_read_cost: str = "NORMAL"

    def key(self, goal_id, tool, arguments, epoch):
        return goal_id, tool, canonical(arguments), epoch

    def mark_read_attempt(self, goal_id, tool, arguments, epoch):
        self.attempted.add(self.key(goal_id, tool, arguments, epoch))
        self.graph_reads += 1


@dataclass
class Resolution:
    status: Status
    arguments: dict
    reason: str
    read_tool: str | None = None
    read_arguments: dict = field(default_factory=dict)
    proofs: dict = field(default_factory=dict)
    edge_ids: list[str] = field(default_factory=list)
    unresolved_slots: list[str] = field(default_factory=list)
    elapsed_ms: float = 0

    def as_dict(self):
        from dataclasses import asdict
        d = asdict(self); d["status"] = self.status.value
        return d


def explicit_literal(request, value, path=''):
    if isinstance(value, str) and value:
        return bool(re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", request, 0 if opaque_field(path) else re.I))
    return type(value) in {int, float} and bool(re.search(r"(?<!\w)" + re.escape(str(value)) + r"(?!\w)", request))


class BackwardDependencyResolver:
    def __init__(self, compiled, scope="PRODUCTION"):
        if isinstance(compiled, VerifiedCompiledGraph):
            if compiled.scope != scope: raise ValueError("Compiled graph scope mismatch")
            self.graph = compiled._payload
        else:
            self.graph = verify_compiled(compiled, scope=scope)
        self.tools = self.graph["tools"]

    def resolve(self, request, planned_goals, target_tool, proposed_arguments, available_tools,
                ledger, observations=None, state=None, goal=None):
        """observations must already be ingested into ledger; never consume future observations."""
        started = time.perf_counter()
        state = state or ExecutionState()
        proposed_arguments=copy.deepcopy(proposed_arguments)
        context_proofs={}
        if observations:
            raise ValueError("Ingest actual tool observations through EvidenceLedger.observe with goal/epoch provenance")
        if goal is None:
            matching = [g for g in planned_goals if g.target_tool == target_tool]
            if len(matching) != 1:
                return Resolution(Status.NEED_MODEL_DECISION, dict(proposed_arguments), "Need one explicitly bound goal")
            goal = matching[0]
        if goal.target_tool != target_tool:
            return Resolution(Status.BLOCKED_UNSAFE, dict(proposed_arguments), "Goal does not authorize this target")
        if goal.condition != "true":
            return Resolution(Status.BLOCKED_UNSAFE, dict(proposed_arguments), "Conditional goal is not enabled")
        for requirement, data in goal.context_requirements.items():
            if data.get('status') == 'satisfied':
                sequence = data.get('observation_sequence',-1)
                if not 0 <= sequence < len(ledger.observations) or ledger.observations[sequence].epoch != ledger.epoch or ledger.observations[sequence].goal_id != goal.goal_id:
                    return Resolution(Status.BLOCKED_UNSAFE, dict(proposed_arguments), 'Stale or cross-goal context evidence')
                for path,value in data.get('expected_arguments',{}).items():
                    old=get_path(proposed_arguments,path)
                    if old is not None and not field_equal(path,old,value):
                        return Resolution(Status.BLOCKED_UNSAFE,proposed_arguments,'Proposed quantity conflicts with observed group membership')
                    set_path(proposed_arguments,path,value)
                    context_proofs[path]={'source':'derived_observation','goal_id':goal.goal_id,'epoch':ledger.epoch,
                                          'observation_sequence':sequence,'derivation':requirement}
                continue
            if data.get('status') in {'ambiguous','unsupported'}:
                return Resolution(Status.AMBIGUOUS if data['status']=='ambiguous' else Status.NEED_MODEL_DECISION,
                                  dict(proposed_arguments),data['reason'])
            edges = [self.graph['edges'][id_] for id_ in self.graph.get('context_dependencies',{}).get(target_tool,[])
                     if self.graph['edges'][id_].get('context_requirement') == requirement]
            if len(edges)!=1:
                return Resolution(Status.BLOCKED_UNSAFE,dict(proposed_arguments),'Referenced subject lacks an approved context route')
            edge=edges[0];p=edge['producer_tool']
            sub=self._resolve(request,goal,p,{},set(available_tools),ledger,state,(target_tool,),True)
            if sub.status not in {Status.SATISFIED,Status.NOT_APPLICABLE}:
                sub.arguments=dict(proposed_arguments);return sub
            args=sub.arguments
            if state.graph_reads>=state.max_reads or state.key(goal.goal_id,p,args,ledger.epoch) in state.attempted or any(canonical(o.arguments)==canonical(args) for o in ledger.current(p,goal.goal_id)):
                return Resolution(Status.UNRESOLVED,dict(proposed_arguments),'Context READ exhausted without one proven subject')
            return Resolution(Status.NEED_READ,dict(proposed_arguments),'Approved referenced-subject prerequisite',p,args,
                              sub.proofs,[edge['edge_id']])
        result = self._resolve(request, goal, target_tool, copy.deepcopy(proposed_arguments), set(available_tools),
                               ledger, state, (), False)
        result.proofs.update(context_proofs)
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result

    def _resolve(self, request, goal, tool, args, available, ledger, state, stack, producer_mode):
        if tool not in available or tool not in self.tools:
            return Resolution(Status.UNRESOLVED, args, "Tool is not installed/available")
        if len(stack) >= state.max_depth:
            return Resolution(Status.UNRESOLVED, args, "Maximum dependency depth")
        if tool in stack:
            return Resolution(Status.UNRESOLVED, args, "Dependency cycle detected")
        t = self.tools[tool]
        if producer_mode and t["kind"] != "read":
            return Resolution(Status.BLOCKED_UNSAFE, args, "Graph cannot schedule a WRITE")
        errors = schema_errors(args, t["parameters"], partial=True)
        if errors: return Resolution(Status.NEED_MODEL_DECISION, args, "; ".join(errors))
        fdict = {f["path"]: f for f in fields(t["parameters"])}
        proofs = {}
        # Explicit, typed argument bindings are grounded by offsets in CURRENT request.
        for path, binding in goal.bindings.get(tool, {}).items():
            if schema_path(path) not in fdict or not goal.valid_value(binding, request, path, ledger):
                return Resolution(Status.BLOCKED_UNSAFE, args, "Invalid user argument provenance")
            old = get_path(args, path)
            if old is not None and not field_equal(path, old, binding.value):
                return Resolution(Status.BLOCKED_UNSAFE, args, "Proposed value conflicts with goal-specific user binding")
            if "[]" in path:
                return Resolution(Status.NEED_MODEL_DECISION, args, "Array rows need an explicit model proposal")
            set_path(args, path, binding.value)
            proofs[path] = {"source": "current_user" if binding.observation_sequence is None else 'tool_observation',
                            "span": [binding.start, binding.end], 'observation_sequence':binding.observation_sequence}
        raw_incoming = self.graph["argument_dependencies"].get(tool, {})
        incoming = {path:ids for path,ids in raw_incoming.items() if '[]' not in path}
        # Bind explicit rows only. Never broadcast one entity to all array elements.
        row_paths = set()
        def collect(value, prefix=''):
            if isinstance(value,dict):
                for k,v in value.items(): collect(v,prefix+('.' if prefix else '')+k)
            elif isinstance(value,list):
                for i,v in enumerate(value): collect(v,f'{prefix}[{i}]')
            elif prefix: row_paths.add(prefix)
        collect(args)
        row_paths.update(k.removeprefix(tool+':') for k in goal.subjects if k.startswith(tool+':'))
        row_paths.update(k for k in goal.subjects if ':' not in k and '[' in k)
        row_paths.update(goal.bindings.get(tool,{}))
        for path in row_paths:
            if '[' in path and schema_path(path) in raw_incoming:
                incoming[path]=raw_incoming[schema_path(path)]
        # An explicit identifier is already data, even before Policy supplies CALL args.
        # Never turn a human name into an ID or ignore additional subject constraints.
        for path in incoming:
            selectors = goal.selectors(tool, path)
            if get_path(args, path) is None and len(selectors) == 1:
                key, value = next(iter(selectors.items()))
                if key in {'id', path} and opaque_field(path) and goal.valid_value(value, request, key, ledger):
                    set_path(args, path, value.value)
                    proofs[path] = {'source': 'current_user_identifier', 'span': [value.start, value.end]}
        slots = [k for k in t["parameters"].get("required", []) if get_path(args, k) is None]
        slots = [k for k in slots if not any(p.startswith(k+'[') for p in incoming)]
        slots.extend(k for k in incoming if '[' in k and get_path(args,k) is None)
        slots.extend(k for k in goal.required_optional if tool == goal.target_tool and k in fdict and get_path(args, k) is None and k not in slots)
        branches = self.graph["alternative_branches"].get(tool, [])
        if branches and not any(all(get_path(args, k) not in (None, "", []) for k in b) for b in branches):
            chosen = goal.selected_alternatives.get(tool)
            possible = [b for b in branches if all(k in incoming or get_path(args, k) is not None for k in b)]
            explicit_branches = [b for b in possible if all(get_path(args, k) is not None or
                bool(goal.selectors(tool, k)) for k in b)]
            if chosen is None and len(explicit_branches) == 1: chosen = explicit_branches[0]
            if chosen is None and len(possible) == 1: chosen = possible[0]
            if chosen not in branches:
                return Resolution(Status.NEED_MODEL_DECISION, args, "Alternative branch requires semantic intent")
            slots.extend(k for k in chosen if get_path(args, k) is None and k not in slots)
        # Validate provided data for all approved graph slots, including optional slots.
        slots.extend(k for k in incoming if get_path(args, k) is not None and k not in slots)
        blocked = []
        used_edges = []
        for slot in slots:
            proposed = get_path(args, slot)
            scoped_selectors = goal.selectors(tool, slot)
            if any(not goal.valid_value(v, request, key, ledger) for key,v in scoped_selectors.items()):
                return Resolution(Status.BLOCKED_UNSAFE, args, 'Subject selector lacks current-user provenance')
            literal_bound_to_goal = (not scoped_selectors or any(k in {"id", slot} and field_equal(slot, v.value, proposed)
                                      for k, v in scoped_selectors.items()) or slot in proofs)
            scope_text = request[slice(*goal.request_span)] if goal.request_span else request
            if proposed is not None and explicit_literal(scope_text, proposed, slot) and literal_bound_to_goal:
                proofs[slot] = {"source": "current_user_literal"}; continue
            edge_ids = incoming.get(slot, [])
            if not edge_ids:
                if proposed is None: blocked.append(slot)
                continue
            if "[]" in slot or fdict[schema_path(slot)]["type"] in {"array", "object"}:
                return Resolution(Status.NEED_MODEL_DECISION, args, "Nested/collection subject binding requires model row selection", unresolved_slots=[slot])
            alternatives = []
            for edge_id in edge_ids:
                e = self.graph["edges"][edge_id]
                selectors = goal.selectors(tool, slot)
                keys = e["subject_binding_rule"]["allowed_selector_keys"]
                if not selectors or not set(selectors) <= set(keys): continue
                if not set(e['subject_binding_rule'].get('required_selector_keys', [])) <= set(selectors): continue
                if any(not goal.valid_value(s, request, key, ledger) for key, s in selectors.items()):
                    return Resolution(Status.BLOCKED_UNSAFE, args, "Subject selector lacks current-user provenance")
                # Reuse newest matching observations. A later contradictory read supersedes older data.
                found = self._match(e, selectors, ledger, goal)
                if found is not None:
                    if found[0] == "OK":
                        source_observation = ledger.observations[found[2]["observation_sequence"]]
                        ptool = e["producer_tool"]
                        if self.graph["argument_dependencies"].get(ptool) or goal.bindings.get(ptool):
                            upstream = self._resolve(request, goal, ptool, copy.deepcopy(source_observation.arguments),
                                available, ledger, state, stack + (tool,), True)
                            if upstream.status not in {Status.SATISFIED, Status.NOT_APPLICABLE}:
                                upstream.arguments = args
                                return upstream
                    alternatives.append(("evidence", e, found, selectors))
                elif e["producer_tool"] in available:
                    alternatives.append(("read", e, None, selectors))
            observed = [x for x in alternatives if x[0] == "evidence"]
            if observed:
                values = [x[2] for x in observed]
                if any(v[0] == "AMBIGUOUS" for v in values):
                    return Resolution(Status.AMBIGUOUS, args, "Multiple records match explicit subject", unresolved_slots=[slot])
                if any(v[0] == "MISMATCH" for v in values):
                    return Resolution(Status.BLOCKED_UNSAFE, args, "Producer observation contradicts subject constraints")
                unique = {canonical(v[1]) for v in values}
                if len(unique) != 1: return Resolution(Status.AMBIGUOUS, args, "Conflicting producer evidence")
                _, e, (_, value, proof), _ = observed[0]
                if proposed is not None and not field_equal(slot, proposed, value):
                    return Resolution(Status.BLOCKED_UNSAFE, args, "Wrong subject/value in proposed argument", unresolved_slots=[slot])
                set_path(args, slot, value); proofs[slot] = proof; used_edges.append(e["edge_id"])
                continue
            # Stable sorting is not sufficient proof when two different producers are eligible.
            reads = [x for x in alternatives if x[0] == "read"]
            if len(reads) > 1:
                return Resolution(Status.NEED_MODEL_DECISION, args, "Multiple eligible producer routes; abstaining")
            if not reads:
                return Resolution(Status.NEED_MODEL_DECISION, args, "No approved route with a grounded subject", unresolved_slots=[slot])
            _, e, _, selectors = reads[0]
            costs = {"LOW": 0, "NORMAL": 1, "HIGH": 2}
            if costs.get(e.get("read_cost"), 99) > costs[state.max_read_cost]:
                return Resolution(Status.NEED_MODEL_DECISION, args, "Prerequisite read exceeds cost budget")
            p = e["producer_tool"]
            pargs = {}
            for arg, mapping in e["producer_argument_bindings"].items():
                key = mapping["subject_key"]
                if key not in selectors:
                    return Resolution(Status.NEED_MODEL_DECISION, args, "Producer binding selector is absent")
                pargs[arg] = selectors[key].value
            # These mapping values are compiler-reviewed and user-span grounded.
            sub = self._resolve(request, goal, p, pargs, available, ledger, state, stack + (tool,), True)
            if sub.status not in {Status.SATISFIED, Status.NOT_APPLICABLE}:
                sub.arguments = args
                return sub
            pargs = sub.arguments
            if schema_errors(pargs, self.tools[p]["parameters"]):
                return Resolution(Status.UNRESOLVED, args, "Producer arguments cannot satisfy schema")
            if state.graph_reads >= state.max_reads:
                return Resolution(Status.UNRESOLVED, args, "Maximum graph-assisted reads reached")
            key = state.key(goal.goal_id, p, pargs, ledger.epoch)
            seen = any(canonical(o.arguments) == canonical(pargs) for o in ledger.current(p, goal.goal_id, include_unscoped=True))
            if key in state.attempted or seen:
                return Resolution(Status.UNRESOLVED, args, "Read already attempted; failed, empty, or no matching subject")
            return Resolution(Status.NEED_READ, args, "Approved missing prerequisite", read_tool=p, read_arguments=pargs,
                              proofs={**proofs, **sub.proofs}, edge_ids=[e["edge_id"]], unresolved_slots=[slot])
        if blocked: return Resolution(Status.UNRESOLVED, args, "Missing slots with no approved graph route", unresolved_slots=blocked)
        errors = schema_errors(args, t["parameters"])
        if errors: return Resolution(Status.NEED_MODEL_DECISION, args, "; ".join(errors))
        required_inputs = {p for id_ in self.graph['consumer_index'].get(tool, [])
                           for p in self.graph['edges'][id_].get('consumer_input_requirements', [])}
        for path in required_inputs:
            value = get_path(args, path)
            scope_text = request[slice(*goal.request_span)] if goal.request_span else request
            if path not in proofs and not explicit_literal(scope_text, value, path):
                return Resolution(Status.BLOCKED_UNSAFE, args, 'Consumer input lacks goal-scoped user provenance: ' + path)
        if producer_mode:
            # Every producer arg must have a provenance proof, not an inherited LLM guess.
            for path, value in args.items():
                mapped = any(path in self.graph["edges"][id_]["producer_argument_bindings"]
                             for id_ in self.graph["producer_index"].get(tool, []))
                if path not in proofs and not mapped and not explicit_literal(request, value):
                    return Resolution(Status.BLOCKED_UNSAFE, args, "Ungrounded producer argument")
        status = Status.SATISFIED if incoming or proofs or producer_mode else Status.NOT_APPLICABLE
        return Resolution(status, args, "Schema/approved dependencies satisfied; validators must still run", proofs=proofs, edge_ids=used_edges)

    def _match(self, edge, selectors, ledger, goal):
        p, path = edge["producer_tool"], edge["source_output_path"]
        for o in reversed(ledger.current(p, goal.goal_id, include_unscoped=True)):
            # A scoped query must not disagree with the exact subject that it supposedly established.
            unrelated = False
            for arg, mapping in edge["producer_argument_bindings"].items():
                if arg in o.arguments and mapping["subject_key"] in selectors:
                    if not field_equal(mapping['subject_key'], o.arguments[arg], selectors[mapping["subject_key"]].value):
                        unrelated = True
            for arg, value in goal.bindings.get(p, {}).items():
                if arg not in o.arguments or not field_equal(arg, o.arguments[arg], value.value): unrelated = True
            if unrelated: continue
            # A later failed/empty response for this lookup supersedes earlier evidence.
            if not isinstance(o.result, dict) or o.result.get('ok') is not True: return None
            matches = []
            seen_records = set()
            for value, record in path_entries(o.result, path):
                if any(get_path(record,k) not in values for k,values in edge.get('record_filters',{}).items()): continue
                if all(get_path(record,k) is not None and field_equal(k, get_path(record,k), v.value) for k, v in selectors.items()):
                    signature = canonical([value, record])
                    if signature in seen_records:
                        if edge['subject_binding_rule'].get('duplicate_subject_policy') == 'ABSTAIN':
                            return 'AMBIGUOUS', None, None
                        continue
                    seen_records.add(signature)
                    matches.append((value, record))
            if len(matches) > 1: return "AMBIGUOUS", None, None
            if not matches: return None
            value, record = matches[0]
            spec = next((f["schema"] for f in fields(self.tools[edge["consumer_tool"]]["parameters"])
                         if f["path"] == edge["target_argument_path"]), {})
            if schema_errors(value, spec): return "MISMATCH", None, None
            return "OK", value, {"source": "tool_observation", "tool": p, "path": path,
                                  "goal_id": goal.goal_id, "epoch": o.epoch, "observation_sequence": o.sequence,
                                  "observed_goal_id": o.goal_id,
                                  "selectors": {k: v.value for k, v in selectors.items()}, "edge_id": edge["edge_id"]}
        return None


class GraphRuntimeController:
    """Proposes READs or repaired arguments; the existing harness owns ALL execution."""
    def __init__(self, compiled, mode=GraphMode.GRAPH_SHADOW, scope="PRODUCTION", state=None):
        self.mode = GraphMode(mode)
        self.resolver = BackwardDependencyResolver(compiled, scope)
        self.ledger = EvidenceLedger()
        self.state = state or ExecutionState()
        self.events = []

    def inspect(self, request, goals, target, args, available, goal=None):
        if self.mode == GraphMode.GRAPH_DISABLED:
            return Resolution(Status.NOT_APPLICABLE, dict(args), "Graph disabled")
        result = self.resolver.resolve(request, goals, target, args, available, self.ledger, state=self.state, goal=goal)
        self.events.append({"target_tool": target, "mode": self.mode.value, **result.as_dict()})
        if self.mode == GraphMode.GRAPH_SHADOW:
            return Resolution(Status.NOT_APPLICABLE, dict(args), "Shadow observation only")
        return result

    def observe(self, name, args, result, goal_id):
        self.ledger.observe(name, args, result, goal_id, self.resolver.tools.get(name, {}).get("kind", "read"))

    def reserve_read(self, goal_id, resolution):
        if self.mode != GraphMode.GRAPH_ASSISTED_SAFE_READ or resolution.status != Status.NEED_READ:
            raise ValueError("Only approved READ plans can be reserved")
        if self.resolver.tools[resolution.read_tool]["kind"] != "read": raise ValueError("WRITE cannot be graph executed")
        self.state.mark_read_attempt(goal_id, resolution.read_tool, resolution.read_arguments, self.ledger.epoch)

    def hints(self, tool, limit=8):
        # No tool filtering; available full schemas remain intact.
        if self.mode == GraphMode.GRAPH_DISABLED: return []
        return self.resolver.graph["shadow_index"].get(tool, [])[:limit]
