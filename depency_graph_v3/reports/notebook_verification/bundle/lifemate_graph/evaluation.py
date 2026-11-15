"""Explicitly non-official call metrics; optional post-run independent adjudication."""
from __future__ import annotations
from collections import Counter
import math
import statistics
from .common import canonical,norm


def f1(pred, gold):
    p, g = Counter(pred), Counter(gold)
    matched = sum((p & g).values())
    return 2 * matched / (sum(p.values()) + sum(g.values())) if p or g else 1.0


def call_metrics(calls, gold):
    exact = [canonical(c) for c in calls]
    expected = [canonical(c) for c in gold]
    names = [c["name"] for c in calls]
    gold_names = [c["name"] for c in gold]
    return {"exact_call_f1": f1(exact, expected), "tool_name_f1": f1(names, gold_names),
            "exact_argument_call_matches": sum((Counter(exact) & Counter(expected)).values()),
            "gold_call_count": len(gold), "predicted_call_count": len(calls)}


def arguments_match_proxy(pred,gold,schema):
    """Spec-oriented proxy; official tokenizer/optional/array rules are unavailable.

    Only x-match=fuzzy leaves use normalized whitespace-token F1 >0.6.
    Identifiers and non-fuzzy strings remain exact; lists remain ordered.
    """
    if type(pred) is not type(gold):return False
    if isinstance(gold,str):
        return f1(norm(pred).split(),norm(gold).split())>.6 if schema.get('x-match')=='fuzzy' else pred==gold
    if isinstance(gold,dict):
        return set(pred)==set(gold) and all(arguments_match_proxy(pred[k],gold[k],schema.get('properties',{}).get(k,{})) for k in gold)
    if isinstance(gold,list):
        return len(pred)==len(gold) and all(arguments_match_proxy(a,b,schema.get('items',{})) for a,b in zip(pred,gold))
    return pred==gold


def fuzzy_call_metrics(calls,gold,tools):
    adjacency=[[j for j,g in enumerate(gold) if p['name']==g['name'] and arguments_match_proxy(
        p.get('arguments',{}),g.get('arguments',{}),tools.get(p['name'],{}).get('parameters',{}))] for p in calls]
    assigned={}
    def augment(i,seen):
        for j in adjacency[i]:
            if j in seen:continue
            seen.add(j)
            if j not in assigned or augment(assigned[j],seen):assigned[j]=i;return True
        return False
    for i in range(len(calls)):augment(i,set())
    pairs=[{'predicted_index':i,'gold_index':j} for j,i in sorted(assigned.items())]
    return {'fuzzy_call_f1_proxy':2*len(pairs)/(len(calls)+len(gold)) if calls or gold else 1.,
            'fuzzy_matched_calls_proxy':len(pairs),'matching_pairs_proxy':pairs,
            'missed_tool_calls_proxy':len(gold)-len(pairs),
            'unmatched_write_calls_proxy':sum(i not in assigned.values() and tools.get(c['name'],{}).get('kind')=='write' for i,c in enumerate(calls)),
            'fuzzy_metric_scope':'NON_OFFICIAL_SPEC_PROXY_WHITESPACE_TOKEN_F1_GT_0_6_ORDERED_ARRAYS_STRICT_KEYS'}


def repeated_reads(calls, tools):
    seen, count = set(), 0
    for call in calls:
        kind = tools[call["name"]]["kind"]
        if kind == "write": seen.clear()
        if kind == "read":
            sig = canonical(call)
            count += sig in seen
            seen.add(sig)
    return count


def distribution(values):
    values = sorted(values)
    if not values: return {"mean": None, "p50": None, "p95": None}
    return {"mean": statistics.mean(values), "p50": values[max(0, math.ceil(len(values)*.5)-1)],
            "p95": values[max(0, math.ceil(len(values)*.95)-1)]}


def aggregate(rows):
    arms = {}
    for arm in sorted({r["arm"] for r in rows}):
        subset = [r for r in rows if r["arm"] == arm]
        ds = [r["result"]["diagnostics"] for r in subset]
        arms[arm] = {"samples": len(subset),
            "exact_call_f1_mean": statistics.mean(d["exact_call_f1"] for d in ds),
            "tool_name_f1_mean": statistics.mean(r["metrics"]["tool_name_f1"] for r in subset),
            "latency_seconds": distribution([d["seconds"] for d in ds]),
            **{key: distribution([d.get(key, 0) for d in ds]) for key in ["planner_seconds", "policy_seconds", "input_tokens",
                "output_tokens", "graph_reads", "graph_resolution_ms", "graph_overhead_ms", "tool_seconds", "llm_seconds", "actual_call_count", "decision_count", "qwen_calls"]},
            'samples_exceeding_30s':sum(d['seconds']>30 for d in ds),
            'percentage_exceeding_30s':100*sum(d['seconds']>30 for d in ds)/len(ds),
            'fuzzy_call_f1_proxy_mean':statistics.mean(r['metrics']['fuzzy_call_f1_proxy'] for r in subset) if all('fuzzy_call_f1_proxy' in r['metrics'] for r in subset) else None,
            'cases_with_graph_reads':sum(d.get('graph_reads',0)>0 for d in ds),
            'graph_read_count':sum(d.get('graph_reads',0) for d in ds),
            'prerequisite_read_successes':sum(r['metrics'].get('graph_read_successes',0) for r in subset),
            'prerequisite_read_failures':sum(r['metrics'].get('graph_read_failures',0) for r in subset),
            'evidence_reuse_resolutions':sum(r['metrics'].get('evidence_reuse_resolutions',0) for r in subset),
            'resolver_abstain_reasons':dict(Counter(reason for r in subset for reason in r['metrics'].get('resolver_abstain_reasons',[]))),
            "repeated_read_count": sum(r["metrics"]["repeated_read_count"] for r in subset),
            "repair_retry_count": sum(sum(bool(s.get("error_type")) for s in r["result"]["private_debug"]["steps"]) for r in subset),
            "goal_candidate_completion_rate": statistics.mean(d["candidate_completed_goals"]/d["planned_goals"] if d["planned_goals"] else 1 for d in ds),
            "state_accuracy": statistics.mean(r['metrics']['state_match'] for r in subset) if all(r['metrics'].get('state_match') is not None for r in subset) else None,
            "official_score": None, "goal_completion": None,
            "argument_correctness_official": None, "correct_subject_binding": None, "prerequisite_ordering": None,
            "unsafe_write_count": None, "wrong_subject_count": None,
            "dependency_resolution_success_rate": None, "unnecessary_prerequisite_rate": None,
            "correct_abstain_rate": None, "false_dependency_rate": None,
            "unmeasured_reason": "Requires official evaluator or independent post-execution semantic adjudication; no hidden state inspected"}
    rollback = "INSUFFICIENT_EVIDENCE"
    if "GRAPH_DISABLED" in arms and "GRAPH_ASSISTED_SAFE_READ" in arms:
        rollback = ("ROLLBACK_RECOMMENDED" if arms["GRAPH_ASSISTED_SAFE_READ"]["exact_call_f1_mean"] < arms["GRAPH_DISABLED"]["exact_call_f1_mean"]
                    else "KEEP_SHADOW_UNTIL_INDEPENDENT_SAFETY_EVALUATION")
    return {"arms": arms, "rollout_decision": rollback, "metric_scope": "train-development; exact debug and non-official fuzzy proxy; state accuracy only with independently supplied reference hashes"}


def execution_metrics(result,tools):
    steps=result['private_debug'].get('steps',[]);events=result['private_debug'].get('graph_events',[])
    graph_steps=[s for s in steps if s.get('origin')=='b043_graph_read']
    return {'graph_read_successes':sum(isinstance(s.get('tool_result'),dict) and s['tool_result'].get('ok') is True for s in graph_steps),
        'graph_read_failures':sum('tool_result' not in s or s['tool_result'].get('ok') is not True for s in graph_steps),
        'evidence_reuse_resolutions':sum(e['status']=='SATISFIED' and any(p.get('source')=='tool_observation' for p in e.get('proofs',{}).values()) for e in events),
        'resolver_abstain_reasons':[e['reason'] for e in events if e['status'] in {'NEED_MODEL_DECISION','AMBIGUOUS','UNRESOLVED','BLOCKED_UNSAFE'}],
        'repeated_read_count':repeated_reads(result['public']['tool_calls'],tools),
        'unnecessary_reads':None,'wrong_write_count':None,'wrong_subject_count':None,
        'semantic_adjudication_reason':'Requires independent semantic/official adjudication; an unmatched call is not proof of an unsafe WRITE'}
