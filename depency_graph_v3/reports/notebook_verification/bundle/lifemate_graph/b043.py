"""B043 integration with the byte-preserved B042 notebook, without a second refactor.

Definitions are loaded without its benchmark entrypoint. A checked AST hook copies only
b042_run_one_sample and inserts three hooks. The original baseline function is retained.
"""
from __future__ import annotations
import ast
import copy
import json
import sys
import types
import time
import functools
from pathlib import Path
from .common import canonical, sha256, field_equal, path_entries
from .compiler import VerifiedCompiledGraph
from .runtime import (GoalContext, GroundedValue, GraphMode, GraphRuntimeController, Resolution, Status)
from .context import recover_context, supplement_goals, clause_span

B042_SHA256 = "1e80fe50cab2eb195b665094d8f6560dd5215c09ce5dc001d56fb9403a6e00de"


def runtime_item(item):
    """Allowlist the agent input; all offline labels/expected/future/hidden state stay out."""
    context = item.get("context", {})
    clean = {"id": item["id"], "audio_text": item.get("audio_text", ""),
             "context": {k: copy.deepcopy(context[k]) for k in
                         ["current_time", "timezone", "location", "user_profile", "installed_apps"] if k in context},
             "history": []}
    for h in item.get("history", []):
        if h.get("role") == "user":
            clean["history"].append({"role": "user", "audio_text": h.get("audio_text", h.get("text", ""))})
        elif h.get("role") == "assistant":
            clean["history"].append({k: copy.deepcopy(h[k]) for k in
                                     ["role", "text", "tool_calls", "tool_results"] if k in h})
    return clean


def load_b042(notebook, module_name="_lifemate_b042_preserved"):
    notebook = Path(notebook)
    if sha256(notebook) != B042_SHA256: raise ValueError("B042 source changed; re-review hook locations before loading")
    n = json.loads(notebook.read_text(encoding="utf-8"))
    module = types.ModuleType(module_name)
    module.__file__ = str(notebook)
    sys.modules[module_name] = module
    # The last cell launches the old 46-run experiment; never execute it here.
    for i in range(1, 7):
        source = "".join(n["cells"][i]["source"])
        exec(compile(source, str(notebook) + f":cell{i}", "exec"), module.__dict__)
    module._B043_ORIGINAL_MAKE_GOALS = module.make_goals
    return module


def goal_contexts(goals, request, tracker=None):
    result = []
    for g in goals:
        raw = g.get("graph_context", {})
        try:
            subjects = {slot: {key: GroundedValue.from_dict(v) for key,v in selectors.items()}
                        for slot, selectors in raw.get("subjects", {}).items()}
            bindings = {tool: {arg: GroundedValue.from_dict(v) for arg,v in values.items()}
                        for tool, values in raw.get("bindings", {}).items()}
            all_values = [v for s in subjects.values() for v in s.values()] + [v for b in bindings.values() for v in b.values()]
            if not all_values or any(not v.valid(request) for v in all_values): continue
            span = raw.get("request_span")
            if span is not None:
                span = (int(span["start"]), int(span["end"]))
                if not 0 <= span[0] < span[1] <= len(request): continue
                if any(not span[0] <= v.start < v.end <= span[1] for v in all_values): continue
            elif len(goals) > 1:
                continue  # Multi-goal assistance requires an explicit request clause scope.
            condition = "true"
            if g.get("conditional"):
                condition = tracker.condition_state if tracker else "unknown"
            result.append(GoalContext(g["goal_id"], g["completion_tool"], subjects, bindings,
                                      set(raw.get("required_optional", [])), raw.get("selected_alternatives", {}), condition, span))
        except (TypeError, KeyError, ValueError, AttributeError):
            continue  # Malformed planner extras never become authority.
    return result


def measured_graph(fn):
    @functools.wraps(fn)
    def call(self,*a,**kw):
        outer=self._timing_depth==0;start=time.perf_counter();external=self._execution_seconds
        self._timing_depth+=1
        try:return fn(self,*a,**kw)
        finally:
            self._timing_depth-=1
            if outer:self.graph_overhead_seconds+=max(0,time.perf_counter()-start-(self._execution_seconds-external))
    return call


class B043Bridge:
    def __init__(self, module, compiled, mode, scope="PRODUCTION"):
        self.module = module
        self.controller = GraphRuntimeController(compiled, mode, scope)
        self.request = ""
        self.names = []
        self.tracker = None
        self.active_goal_id = None
        self._timing_depth=0;self._execution_seconds=0.;self.graph_overhead_seconds=0.

    def attach(self, item, names, tracker):
        self.request, self.names, self.tracker = item.get("audio_text", ""), names, tracker
        self.item = item
        self.stop_repairs = 0
        if self.controller.mode == GraphMode.GRAPH_ASSISTED_SAFE_READ:
            supplement_goals(item,names,tracker)

    @property
    def owns_prerequisites(self):
        goal, resolution = self._preflight_plan()
        if not goal: return False
        return bool(goal.context_requirements or any(goal.selectors(goal.target_tool,path)
            for path in self.controller.resolver.graph['argument_dependencies'].get(goal.target_tool,{})) or
            any(k.startswith(goal.target_tool+':') for k in goal.subjects) or goal.bindings.get(goal.target_tool))

    @measured_graph
    def _preflight_plan(self):
        if self.controller.mode != GraphMode.GRAPH_ASSISTED_SAFE_READ or self.tracker is None: return None, None
        pending=[g for g in self.tracker.snapshot() if g['status'] in {'pending','waiting_condition'}]
        candidates=pending[:1]+[g for g in pending[1:] if g.get('explicit_intent_proven')]
        goals={g.goal_id:g for g in self.goals()}
        fallback=(None,None)
        for first in candidates:
            if first['completion_tool'] not in self.names or first['completion_tool'] not in self.controller.resolver.graph['consumer_index']:continue
            goal=goals.get(first['goal_id'])
            if goal is None:continue
            result=self.controller.resolver.resolve(self.request,[goal],goal.target_tool,{},self.names,
                self.controller.ledger,state=self.controller.state,goal=goal)
            if result.status in {Status.NEED_READ,Status.SATISFIED}:return goal,result
            if fallback[0] is None:fallback=(goal,result)
        return fallback

    @measured_graph
    def goals(self):
        original=goal_contexts(self.tracker.snapshot(), self.request, self.tracker)
        if self.controller.mode != GraphMode.GRAPH_ASSISTED_SAFE_READ: return original
        by_id={g.goal_id:g for g in original}
        item={**self.item,'_pending_goals':self.tracker.pending()}
        result=[]
        for raw in self.tracker.snapshot():
            existing=by_id.get(raw['goal_id'])
            if existing is None and len(self.tracker.snapshot())>1:
                span=clause_span(raw,self.request,self.names)
                if span is None and raw.get('explicit_intent_proven') and sum(bool(g.get('explicit_intent_proven')) for g in self.tracker.snapshot())==1:
                    span=(0,len(self.request))
                if span is None: continue
                existing=GoalContext(raw['goal_id'],raw['completion_tool'],condition='unknown' if raw.get('conditional') else 'true',request_span=span)
            g=recover_context(raw,self.request,self.controller.resolver.graph,self.controller.ledger,item,existing)
            if g is not None: result.append(g)
        return result

    def selected_goal(self, name, args=None):
        pending = {g["goal_id"] for g in self.tracker.snapshot() if g["status"] in {"pending", "waiting_condition"}}
        gs = [g for g in self.goals() if g.target_tool == name and g.goal_id in pending]
        if len(gs) > 1 and args:
            from .common import equal
            matched = []
            for g in gs:
                explicit = g.bindings.get(name, {})
                intersect = set(args) & set(explicit)
                if intersect and all(equal(args[k], explicit[k].value) for k in intersect): matched.append(g)
            gs = matched
        return gs[0] if len(gs) == 1 else None

    @measured_graph
    def observe(self, name, args, result):
        self.controller.observe(name, args, result, self.active_goal_id)

    def observation_goal(self, name, args):
        """Attribute Policy READs only when one pending goal/approved route fits."""
        if self.controller.mode != GraphMode.GRAPH_ASSISTED_SAFE_READ: return None
        pending = {g['goal_id'] for g in self.tracker.snapshot() if g['status'] == 'pending'}
        matches = set()
        for goal in self.goals():
            if goal.goal_id not in pending or goal.condition != 'true': continue
            for edge_id in self.controller.resolver.graph['producer_index'].get(name, []):
                edge = self.controller.resolver.graph['edges'][edge_id]
                if edge['consumer_tool'] != goal.target_tool: continue
                if edge.get('context_requirement') in goal.context_requirements:
                    check=self.controller.resolver._resolve(self.request,goal,name,dict(args),set(self.names),
                        self.controller.ledger,self.controller.state,(),True)
                    if check.status in {Status.SATISFIED,Status.NOT_APPLICABLE}: matches.add(goal.goal_id)
                    continue
                selectors = goal.selectors(goal.target_tool, edge['target_argument_path'])
                rule = edge['subject_binding_rule']
                if not selectors or not set(selectors) <= set(rule['allowed_selector_keys']): continue
                if not set(rule.get('required_selector_keys', [])) <= set(selectors): continue
                if any(not goal.valid_value(v, self.request, k, self.controller.ledger) for k,v in selectors.items()): continue
                if any(arg in args and (mapping['subject_key'] not in selectors or not
                       field_equal(mapping['subject_key'], args[arg], selectors[mapping['subject_key']].value))
                       for arg,mapping in edge['producer_argument_bindings'].items()): continue
                if any(arg not in args or not field_equal(arg, args[arg], v.value)
                       for arg,v in goal.bindings.get(name, {}).items()): continue
                matches.add(goal.goal_id)
        return next(iter(matches)) if len(matches) == 1 else None

    def _read(self, goal, resolution, invoke, idx, can_read, journal, duplicate_guard, progress_guard, ledger, item):
        m = self.module
        name, args = resolution.read_tool, resolution.read_arguments
        if not can_read() or name not in self.names or m.tool_kind(m._B043_TOOL_MAP[name]) != "read": return False
        if duplicate_guard.check(name, args, "read") or progress_guard.inspect(name, args, journal): return False
        problems, _ = m.grounding_check(name, args, m._B043_TOOL_MAP, ledger, self.names)
        extra, _ = m.additional_grounding_check(name, args, item, ledger, self.names)
        if name == "ride_hailing.get_quotes" and m.needs_calendar_location(item) and not m.weather_event_destination_is_grounded(args, ledger):
            extra.append("ungrounded ride quote")
        if problems or extra: return False
        self.active_goal_id = goal.goal_id
        self.controller.reserve_read(goal.goal_id, resolution)
        step = {"step_index": idx, "tool_name": name, "final_arguments": args, "origin": "b043_graph_read",
                "graph_resolution": resolution.as_dict(), "reason": resolution.reason}
        started=time.perf_counter()
        try:ok = invoke(name, args, idx, "b043_graph_read", step)  # ToolEnv.validate is inside original invoke.
        finally:self._execution_seconds+=time.perf_counter()-started
        if not ok: self.controller.observe(name, args, {"ok": False, "error": "validation_or_execution_failed"}, goal.goal_id)
        return ok

    @measured_graph
    def preflight(self, invoke, idx, can_read, journal, duplicate_guard, progress_guard, ledger, item):
        if not self.owns_prerequisites: return False
        goal, _ = self._preflight_plan()
        if goal and goal.condition == 'true':
            r = self.controller.inspect(self.request, [goal], goal.target_tool, {}, self.names, goal)
            if r.status == Status.NEED_READ:
                return self._read(goal, r, invoke, idx, can_read, journal, duplicate_guard, progress_guard, ledger, item)
        return False

    @measured_graph
    def proposal(self, name, args, invoke, idx, can_read, journal, duplicate_guard, progress_guard, ledger, item):
        if self.controller.mode==GraphMode.GRAPH_ASSISTED_SAFE_READ and self.module.B042_CONFIG.get('enabled') and self.module.B042_CONFIG.get('block_exact_successful_write_replay'):
            if self.controller.resolver.tools.get(name,{}).get('kind')=='write' and any(
                entry.get('call')=={'name':name,'arguments':args} and isinstance(entry.get('result'),dict) and entry['result'].get('ok') is True and entry['result'].get('done') is not False
                for entry in journal):
                # Let B042's existing successful-WRITE replay veto own this case.
                return args,None,None
        goal = self.selected_goal(name, args)
        if goal is None and self.controller.mode == GraphMode.GRAPH_ASSISTED_SAFE_READ and self.controller.resolver.tools.get(name,{}).get('kind')=='read':
            parent=self.observation_goal(name,args)
            if parent is not None:
                # A Policy prerequisite belongs to its pending parent, not a new
                # standalone goal. Existing validators still inspect this CALL.
                self.active_goal_id=parent
                return args,None,None
        if goal is None and self.controller.mode == GraphMode.GRAPH_ASSISTED_SAFE_READ and name in self.names:
            # Policy owns this proposed target. Recover only explicit subject spans;
            # no new automatic WRITE is introduced, and the tracker remains advisory.
            raw={'goal_id':'policy_'+name,'completion_tool':name,'description':self.request,
                 'status':'pending','conditional':bool(__import__('re').search(r'\bnếu\b',self.request,__import__('re').I))}
            if not any(g['completion_tool']==name for g in self.tracker.snapshot()):
                candidate=recover_context(raw,self.request,self.controller.resolver.graph,self.controller.ledger,item)
                if candidate:
                    self.tracker.goals.append(raw);goal=candidate
        self.active_goal_id = goal.goal_id if goal else self.observation_goal(name, args)
        if self.controller.mode == GraphMode.GRAPH_DISABLED: return args, None, None
        if name not in self.controller.resolver.graph["consumer_index"]:
            # Unapproved/missing graph edges cannot veto or repair baseline actions.
            if self.controller.mode == GraphMode.GRAPH_SHADOW:
                self.controller.events.append({"target_tool": name, "mode": self.controller.mode.value,
                    "status": Status.NOT_APPLICABLE.value, "hints": self.controller.hints(name)})
            return args, None, None
        if goal is None:
            if self.controller.mode == GraphMode.GRAPH_ASSISTED_SAFE_READ and self.controller.resolver.tools.get(name,{}).get('kind')=='write':
                from .runtime import explicit_literal
                incoming=self.controller.resolver.graph['argument_dependencies'].get(name,{})
                if name in {'phone.call','sms.send'} and 'phone_number' in args and not explicit_literal(self.request,args['phone_number'],'phone_number'):
                    return args,'b043_unbound_recipient','An observed phone number needs evidence that it belongs to the requested recipient; use a grounded contact lookup or ask.'
                if any(not explicit_literal(self.request,value,path) for path in incoming for value,_ in path_entries(args,path)):
                    return args,'b043_unbound_subject','An observed ID alone does not establish the requested subject. Supply a goal-scoped subject or ask for clarification.'
            return args, None, None
        for _ in range(self.controller.state.max_reads + 1):
            goal = self.selected_goal(name,args) or goal  # refresh observation-derived context after each READ
            r = self.controller.inspect(self.request, self.goals(), name, args, self.names, goal)
            if r.status == Status.NEED_READ:
                if self._read(goal, r, invoke, idx, can_read, journal, duplicate_guard, progress_guard, ledger, item): continue
                return args, "b043_read_blocked", "Graph prerequisite READ could not pass existing validation/guard/budget path."
            if r.status in {Status.SATISFIED, Status.NOT_APPLICABLE}:
                reason=self.recipient_alternative_guard(goal,name,r.arguments)
                if reason:
                    self.controller.events.append({'target_tool':name,'status':Status.BLOCKED_UNSAFE.value,'reason':reason,'arguments':r.arguments,'goal_id':goal.goal_id})
                    return args,'b043_wrong_recipient',reason
                return r.arguments, None, None
            return args, "b043_" + r.status.value.lower(), "Graph abstained: " + r.reason
        return args, "b043_read_budget", "Graph read budget reached; ask for clarification or choose another safe step."

    def recipient_alternative_guard(self,goal,name,args):
        """Validate a Policy phone alternative against the same matched contact.

        Never schedules a new producer or changes Policy's WRITE arguments.
        A direct goal-scoped user phone is a valid alternative per the catalog.
        """
        if self.controller.mode!=GraphMode.GRAPH_ASSISTED_SAFE_READ or name not in {'phone.call','sms.send'} or 'phone_number' not in args:return None
        from .runtime import explicit_literal
        scope=self.request[slice(*goal.request_span)] if goal.request_span else self.request
        if explicit_literal(scope,args['phone_number'],'phone_number'):return None
        selectors=goal.selectors(name,'contact_id')
        if not selectors or any(not goal.valid_value(v,self.request,k,self.controller.ledger) for k,v in selectors.items()):
            return 'Phone alternative lacks a goal-bound recipient; propose contact_id lookup instead or ask for clarification.'
        for id_ in self.controller.resolver.graph['argument_dependencies'].get(name,{}).get('contact_id',[]):
            edge=self.controller.resolver.graph['edges'][id_]
            found=self.controller.resolver._match(edge,selectors,self.controller.ledger,goal)
            if found and found[0]=='OK':
                o=self.controller.ledger.observations[found[2]['observation_sequence']]
                records=[record for value,record in path_entries(o.result,edge['source_output_path']) if value==found[1]]
                if len(records)==1 and field_equal('phone',records[0].get('phone'),args['phone_number']):
                    self.controller.events.append({'target_tool':name,'status':Status.SATISFIED.value,
                        'reason':'Phone alternative verified within the same unique contact record',
                        'proofs':{'recipient':found[2]},'goal_id':goal.goal_id})
                    return None
        return 'Observed phone does not uniquely belong to the current goal recipient. Use the verified contact_id route or ask; no WRITE executed.'

    @measured_graph
    def stop_feedback(self):
        if self.controller.mode != GraphMode.GRAPH_ASSISTED_SAFE_READ or self.stop_repairs >= 2: return None
        goal,r=self._preflight_plan()
        if goal is None or r.status not in {Status.SATISFIED,Status.NEED_READ}: return None
        self.stop_repairs+=1
        # Feedback is advisory. Only the next Policy CALL can perform any WRITE.
        self.controller.events.append({'target_tool':goal.target_tool,'status':'STOP_REPAIR',
            'goal_id':goal.goal_id,'arguments':r.arguments,'reason':'Pending goal has grounded actionable data'})
        return ('Pending goal '+goal.target_tool+' has graph-grounded arguments '+canonical(r.arguments)+
                '. Review observations and propose the next valid CALL, ask for missing information, or STOP if impossible. '
                'Graph has not executed this action; all guards still apply.')


def install_hooks(module, notebook):
    n = json.loads(Path(notebook).read_text(encoding="utf-8"))
    tree = ast.parse("".join(n["cells"][5]["source"]))
    runner = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "b042_run_one_sample")
    runner = copy.deepcopy(runner)
    runner.name = "b043_run_one_sample"
    counts = {"attach": 0, "observe": 0, "proposal": 0, "preflight": 0, "legacy_preflight": 0, "recovery": 0, 'stop':0}
    read_args = "invoke, idx, lambda: n_calls < CONFIG['max_executed_calls'] and time.perf_counter()-start < CONFIG['soft_deadline_seconds'], journal, duplicate_guard, progress_guard, ledger, item"
    class Hooks(ast.NodeTransformer):
        def visit_If(self,node):
            node=self.generic_visit(node)
            if ast.unparse(node.test) == "d['action'] == 'stop'":
                counts['stop']+=1
                node.body=[*ast.parse("graph_stop_feedback = _B043_BRIDGE.stop_feedback()\n"
                    "if graph_stop_feedback:\n"
                    "    feedback = graph_stop_feedback\n"
                    "    tracker.stop_reviews += 1\n"
                    "    step['outcome'] = 'b043_pending_goal_stop_repair'\n"
                    "    steps.append(step)\n"
                    "    continue").body,*node.body]
            return node
        def visit_Assign(self, node):
            node = self.generic_visit(node)
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if names == ["guarded_recovery_reads"]:
                counts["attach"] += 1
                return [node, *ast.parse("_B043_BRIDGE.attach(item, names, tracker)").body]
            if names == ["recovery"]:
                counts["recovery"] += 1
                node.value = ast.IfExp(ast.parse("_B043_BRIDGE.owns_prerequisites", mode="eval").body, ast.Constant(None), node.value)
            if names == ["prereq"]:
                counts["legacy_preflight"] += 1
                node.value = ast.IfExp(ast.parse("_B043_BRIDGE.owns_prerequisites", mode="eval").body, ast.Constant(None), node.value)
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript) and ast.unparse(node.targets[0]) == "step['tool_name']":
                counts["proposal"] += 1
                code = (f"args, graph_error, graph_feedback = _B043_BRIDGE.proposal(name, args, {read_args})\n"
                        "if graph_error:\n"
                        "    step.update({'error_type': graph_error, 'final_arguments': args})\n"
                        "    steps.append(step)\n"
                        "    failures += 1\n"
                        "    feedback = graph_feedback\n"
                        "    if failures >= CONFIG['max_repair_failures']:\n"
                        "        stop_reason = 'b043_unresolved_dependency'\n"
                        "        break\n"
                        "    continue")
                return [node, *ast.parse(code).body]
            # Insert before the original model policy step, after all coordinated preflight code.
            if names == ["step"] and isinstance(node.value, ast.Dict) and any(isinstance(v, ast.Constant) and v.value == "model_policy" for v in node.value.values):
                counts["preflight"] += 1
                return [*ast.parse(f"if _B043_BRIDGE.preflight({read_args}):\n    continue").body, node]
            return node
        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "ledger.observe":
                counts["observe"] += 1
                return [node, *ast.parse("_B043_BRIDGE.observe(name, args, result)").body]
            return node
    runner = Hooks().visit(runner)
    if counts != {k: 1 for k in counts}: raise ValueError("B042 integration hook mismatch: " + canonical(counts))
    ast.fix_missing_locations(runner)
    exec(compile(ast.Module(body=[runner], type_ignores=[]), "<B043_checked_hooks>", "exec"), module.__dict__)
    module._B043_HOOK_COUNTS = counts
    return ast.unparse(runner)


def graph_make_goals(module):
    def make(item, names, tool_map, model, tokenizer, torch):
        graph = module._B043_BRIDGE.controller.resolver.graph
        relevant = {n: [{"producer": graph['edges'][id_]['producer_tool'],
                        "target": graph['edges'][id_]['target_argument_path'],
                        "selector_keys": graph['edges'][id_]['subject_binding_rule']['allowed_selector_keys'],
                        "required_selector_keys": graph['edges'][id_]['subject_binding_rule'].get('required_selector_keys', []),
                        "producer_argument_bindings": graph['edges'][id_]['producer_argument_bindings'],
                        "consumer_input_requirements": graph['edges'][id_].get('consumer_input_requirements', [])}
                       for id_ in graph['consumer_index'][n]] for n in names if n in graph['consumer_index']}
        system = module.PLANNER_SYSTEM + """\nOptional graph_context on each goal:
{"request_span":{"start":0,"end":20},"subjects":{"consumer_tool:argument_path":{"record_field":{"value":"EXACT USER SUBSTRING","start":0,"end":3}}},
"bindings":{"tool.name":{"arg":{"value":"EXACT USER SUBSTRING","start":0,"end":3}}},
"required_optional":[],"selected_alternatives":{}}.
Offsets are zero-based Python Unicode character offsets in current request. Only copy exact explicit user spans.
request_span must cover only this goal's explicit request clause; all its selectors and bindings must lie inside it.
Subject fields must be known record selectors such as name/title/id, never guessed IDs or descriptions of a goal.
Use dependency_hints selector_keys exactly: email uses subject, tracks use title, contacts use name.
Copy explicitly supplied identifiers into target bindings. Select one x-one-of branch only when intent is clear.
Provide goal-scoped bindings for listed consumer_input_requirements; never take a value from another goal clause.
Omit graph_context when intent/subject is not explicit. Preserve each independent goal. Never infer condition=true.
"""
        payload = {"request": item.get("audio_text", ""), "history": module.safe_history(item.get("history")),
                   "context": {k:v for k,v in item.get("context", {}).items() if k != "installed_apps"},
                   "available_tool_names": names, "tools": [module.compact_schema(tool_map[n]) for n in names],
                   "dependency_hints": relevant}
        raw, ni, no, seconds = module.generate_once(model, tokenizer, torch,
            [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_new_tokens=max(512, module.CONFIG["max_plan_tokens"]))
        goals = module.parse_goals(raw, set(names))
        originals = module.parse_json_only(raw)["goals"]
        # parse_goals retains original list indices in g1/g2, even if an entry was rejected.
        for goal in goals:
            i = int(goal["goal_id"][1:]) - 1
            if isinstance(originals[i].get("graph_context"), dict): goal["graph_context"] = originals[i]["graph_context"]
        return goals, {"raw": raw, "input_tokens": ni, "output_tokens": no, "model_seconds": seconds, "parsed_goals": goals}
    return make


class B043Harness:
    def __init__(self, notebook, compiled, mode=GraphMode.GRAPH_SHADOW, scope="PRODUCTION"):
        self.module = load_b042(notebook, "_lifemate_b042_" + str(id(self)))
        self.modified_source = install_hooks(self.module, notebook)
        self.compiled, self.mode, self.scope = compiled, GraphMode(mode), scope
        self._verified_graph = VerifiedCompiledGraph(compiled, scope)

    def run(self, item, tool_map, ToolEnv, root, model=None, tokenizer=None, torch=None):
        m = self.module
        clean = runtime_item(item)
        bridge = B043Bridge(m, self._verified_graph, self.mode, self.scope)
        if self.mode == GraphMode.GRAPH_ASSISTED_SAFE_READ:
            for e in self.compiled["edges"].values():
                for name in [e["producer_tool"], e["consumer_tool"]]:
                    if name in tool_map and (tool_map[name]["kind"] != self.compiled["tools"][name]["kind"] or
                        tool_map[name]["parameters"] != self.compiled["tools"][name]["parameters"]):
                        raise ValueError("Approved runtime contract changed: " + name)
        m._B043_BRIDGE, m._B043_TOOL_MAP = bridge, tool_map
        original_make_goals = m.make_goals
        # The original compact Planner is sufficient: graph_context is optional.
        # Avoid a second full registry prompt and 512-token offset generation.
        try:
            runner = m.run_one_sample if self.mode == GraphMode.GRAPH_DISABLED else m.b043_run_one_sample
            result = runner(clean, tool_map, ToolEnv, root, model, tokenizer, torch)
        finally:
            m.make_goals = original_make_goals
        result["diagnostics"].update({"graph_mode": self.mode.value, "graph_reads": bridge.controller.state.graph_reads,
              "graph_resolution_ms": sum(e.get("elapsed_ms", 0) for e in bridge.controller.events),
              'graph_overhead_ms':bridge.graph_overhead_seconds*1000,
              "graph_status_counts": {s.value: sum(e.get("status") == s.value for e in bridge.controller.events) for s in Status},
              "graph_compiled_sha256": self.compiled["payload_sha256"]})
        result["private_debug"]["graph_events"] = bridge.controller.events
        if self.mode==GraphMode.GRAPH_ASSISTED_SAFE_READ:
            result['diagnostics']['planned_goals']=len(result['private_debug'].get('goals',[]))
            result['diagnostics']['deterministic_goal_supplements']=sum(g.get('source')=='explicit_request_intent' for g in result['private_debug'].get('goals',[]))
        # Scoring belongs AFTER execution; sanitized runtime inputs never carry labels.
        gold = item.get("label", {}).get("tool_calls", [])
        result["diagnostics"].update(m.call_matching(result["public"]["tool_calls"], gold))
        diff = m.argument_diff(result["public"]["tool_calls"], gold)
        result["diagnostics"].update({"gold_call_count": len(gold),
            "n_unmatched_gold_tool_names": len(diff["missing_tool_names"]),
            "n_extra_argument_keys": sum(len(p["extra_arg_keys"]) for p in diff["paired_call_diffs"]),
            "n_missing_argument_keys": sum(len(p["missing_arg_keys"]) for p in diff["paired_call_diffs"]),
            "n_different_argument_keys": sum(len(p["different_arg_keys"]) for p in diff["paired_call_diffs"])})
        result["private_debug"]["arg_diff_name_paired"] = diff
        result["private_debug"]["gold_calls"] = copy.deepcopy(gold)
        return result
