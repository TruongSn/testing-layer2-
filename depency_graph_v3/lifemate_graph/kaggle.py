"""Paired B042/B043 launcher. Gold is used only for post-execution diagnostics."""
from __future__ import annotations
import importlib.metadata
import json
import platform
import random
import time
import re
from collections import Counter
from pathlib import Path
from .b043 import B043Harness
from .common import object_sha, read_json, sha256, write_json
from .compiler import load_compiled
from .evaluation import aggregate, call_metrics, fuzzy_call_metrics, execution_metrics
from .inference import bounded_generate
from .runtime import GraphMode


def code_fingerprint(root):
    return {str(p.relative_to(root)).replace("\\", "/"): sha256(p) for p in sorted((Path(root)/"lifemate_graph").glob("*.py"))}


def representative_cases(samples,graph,seed=2026,limit=40):
    """Evaluation-only stratification; labels never enter runtime_item/Policy."""
    rng=random.Random(seed);buckets={k:[] for k in ['single_tool','multi_tool','multi_app','multi_hop','ambiguous','missing_information','read_write','no_op']}
    for id_,s in sorted(samples.items()):
        calls=s.get('label',{}).get('tool_calls',[]);names=[c['name'] for c in calls]
        kinds=[graph['tools'].get(n,{}).get('kind') for n in names]
        if len(calls)==1:buckets['single_tool'].append(id_)
        if len(calls)>1:buckets['multi_tool'].append(id_)
        if len({n.split('.')[0] for n in names})>1:buckets['multi_app'].append(id_)
        if sum(k=='read' for k in kinds)>=2 and any(graph['producer_index'].get(n) and graph['consumer_index'].get(n) for n in names):buckets['multi_hop'].append(id_)
        if re.search(r'\b(?:hoặc|hay là|nào cũng được|trùng tên)\b',s.get('audio_text',''),re.I):buckets['ambiguous'].append(id_)
        if 'sys.ask_missing_info' in names:buckets['missing_information'].append(id_)
        if 'read' in kinds and 'write' in kinds:buckets['read_write'].append(id_)
        if not calls:buckets['no_op'].append(id_)
    selected=[i for i in ['train_0136','train_0463','train_0488','train_0557','train_0951'] if i in samples]
    for ids in buckets.values():rng.shuffle(ids)
    tags={}
    for group,ids in buckets.items():
        for i in ids[:4]:
            if i not in selected:selected.append(i)
    remainder=sorted(set(samples)-set(selected));rng.shuffle(remainder)
    selected=(selected+remainder)[:limit]
    for i in selected:tags[i]=[name for name,ids in buckets.items() if i in ids]
    return selected,tags


def run_benchmark(root, output, case_ids=None, modes=None, graph_dir=None, seed=2026, adjudicator=None,
                  reference_hashes=None,sample_budget_seconds=30.,limit=40,require_graph_smoke=True):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    notebook = root / "inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb"
    graph_dir = Path(graph_dir or root / "artifacts_final")
    modes = modes or [GraphMode.GRAPH_DISABLED.value,GraphMode.GRAPH_ASSISTED_SAFE_READ.value]
    if sample_budget_seconds<=0 or sample_budget_seconds>30:raise ValueError('Sample budget must be in (0,30] seconds')
    # Dataset discovery is the preserved B042 implementation. It prunes *_environment.
    bootstrap_graph = load_compiled(graph_dir / "compiled_graph_v3.json", graph_dir / "graph_manifest.json")
    if modes == [GraphMode.GRAPH_ASSISTED_SAFE_READ.value] and not bootstrap_graph['edges']:
        raise ValueError('Active-only run needs an operator-approved compiled graph via B043_GRAPH_DIR; current graph has zero grants')
    harnesses = {mode: B043Harness(notebook, bootstrap_graph, mode) for mode in modes}
    setup = next(iter(harnesses.values())).module
    env_root, samples, tools, ToolEnv, train_path, catalog_path = setup.configure_dataset()
    load_compiled(graph_dir / "compiled_graph_v3.json", graph_dir / "graph_manifest.json", catalog_path)
    tags={}
    if case_ids is None:case_ids,tags=representative_cases(samples,bootstrap_graph,seed,limit)
    if not case_ids or len(case_ids)!=len(set(case_ids)):raise ValueError('Provide unique evaluation IDs')
    if not set(case_ids) <= set(samples): raise ValueError("Unknown evaluation IDs")
    for h in harnesses.values():h.module.CONFIG['soft_deadline_seconds']=sample_budget_seconds
    torch = setup.ensure_packages()
    setup.authenticate_hf()
    model, tokenizer = setup.load_policy_model(torch)
    package_versions = {}
    for name in ["torch", "transformers", "accelerate", "bitsandbytes", "huggingface-hub"]:
        try: package_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: package_versions[name] = None
    manifest = {"run_version": "B043-paired-v3", "partition": "TRAIN_DEVELOPMENT_NOT_INDEPENDENT", "case_ids": case_ids,
         "modes": modes, "seed": seed, "source_code_sha256": code_fingerprint(root), "b042_sha256": sha256(notebook),
         "train_sha256": sha256(train_path), "catalog_sha256": sha256(catalog_path),
         "compiled_sha256": sha256(graph_dir/"compiled_graph_v3.json"), "model_id": setup.CONFIG["model_id"],
         "model_revision": setup.CONFIG["model_revision"], "base_config": setup.CONFIG,
         "runtime_edges": len(bootstrap_graph["edges"]), "packages": package_versions, "python": platform.python_version(),
         'sample_budget_seconds':sample_budget_seconds,'context_window_cap':16384,'selection_tags':tags,
         'reference_hashes_digest':object_sha(reference_hashes) if reference_hashes is not None else None,
         "gpu": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
         "adjudicator": getattr(adjudicator, "__qualname__", None)}
    fingerprint = object_sha(manifest)
    write_json(output / "run_manifest.json", {"fingerprint": fingerprint, **manifest})
    rows, starts = [], {}
    for case_id in case_ids:
        # Counterbalance first-arm warmup effects without changing inference settings.
        arm_order=modes if case_ids.index(case_id)%2==0 else list(reversed(modes))
        for mode in arm_order:
            checkpoint = output / "checkpoints" / f"{case_id}_{mode}.json"
            cached = read_json(checkpoint) if checkpoint.is_file() else None
            if cached and cached.get("fingerprint") != fingerprint: raise ValueError("Stale checkpoint; use a new output directory")
            if cached:
                row = cached
            else:
                h = harnesses[mode]; m = h.module
                random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
                m.B041_FIELD_AUDIT.clear(); m.B042_PROGRESS_AUDIT.clear()
                environments = []
                deadline=time.perf_counter()+sample_budget_seconds
                class BoundedEnv:
                    def __init__(self,env):self.env=env
                    def validate(self,name,args):
                        if time.perf_counter()>=deadline-.1:raise TimeoutError('Tool validation denied after sample deadline')
                        return self.env.validate(name,args)
                    def call(self,name,args):
                        if time.perf_counter()>=deadline-.1:raise TimeoutError('Tool call denied after sample deadline')
                        return self.env.call(name,args)
                    def get_tool_calls(self):return self.env.get_tool_calls()
                    def get_state_hash(self):return self.env.get_state_hash()
                def factory(*args, **kwargs):
                    env = ToolEnv(*args, **kwargs)
                    environments.append((env, env.get_state_hash()))
                    if len(environments) > 1: raise AssertionError("Exactly one ToolEnv per trajectory")
                    return BoundedEnv(env)
                qwen_calls = 0
                generation_attempts=0
                original_generate = m.generate_once
                def count_generate():
                    nonlocal qwen_calls
                    qwen_calls+=1
                timed_generate=bounded_generate(m,deadline,count_generate)
                def generate(*args, **kwargs):
                    nonlocal generation_attempts
                    generation_attempts += 1
                    return timed_generate(*args, **kwargs)
                m.generate_once = generate
                started=time.perf_counter()
                try:
                    result = h.run(samples[case_id], tools, factory, env_root, model, tokenizer, torch)
                finally:
                    m.generate_once = original_generate
                if len(environments) != 1: raise AssertionError("Exactly one ToolEnv per trajectory")
                result["diagnostics"]["qwen_calls"] = qwen_calls
                result['diagnostics']['generation_attempts']=generation_attempts
                d=result['diagnostics'];d['seconds']=time.perf_counter()-started
                d['tool_seconds']=sum(s.get('tool_seconds',0) for s in result['private_debug']['steps'])
                d['llm_seconds']=d.get('planner_seconds',0)+d.get('policy_seconds',0)
                metrics = call_metrics(result["public"]["tool_calls"], samples[case_id].get("label", {}).get("tool_calls", []))
                metrics.update(fuzzy_call_metrics(result['public']['tool_calls'],samples[case_id].get('label',{}).get('tool_calls',[]),tools))
                metrics.update(execution_metrics(result,tools))
                expected=(reference_hashes or {}).get(case_id)
                metrics['state_match']=result['public']['state_hash']==expected if expected is not None else None
                metrics['state_match_reason']='Independent supplied reference hash' if expected is not None else 'Train labels contain partial final_state, no authoritative full-state hash; no hidden state or second gold-rollout Env is used'
                row = {"fingerprint": fingerprint, "id": case_id, "arm": mode,
                       "initial_state_hash": environments[0][1], "result": result, "metrics": metrics}
                if adjudicator:
                    # An external evaluator may judge after execution. It is never passed into policy/resolver.
                    row["independent_adjudication"] = adjudicator(samples[case_id], result)
                write_json(checkpoint, row)
            if case_id in starts and starts[case_id] != row["initial_state_hash"]:
                raise AssertionError("Paired arms did not begin from equal ToolEnv state hashes")
            starts[case_id] = row["initial_state_hash"]
            rows.append(row)
            print(case_id, mode, "exact_call_f1=", round(row["metrics"]["exact_call_f1"], 4), flush=True)
        # The first five contain the reported failures plus the no-context smoke.
        # Stop before a larger run if active Graph never participates. Save outputs first.
        if require_graph_smoke and case_ids.index(case_id)==min(4,len(case_ids)-1) and GraphMode.GRAPH_ASSISTED_SAFE_READ.value in modes:
            smoke=[r for r in rows if r['arm']==GraphMode.GRAPH_ASSISTED_SAFE_READ.value]
            if not any(r['result']['diagnostics'].get('graph_reads',0)>0 for r in smoke):
                write_json(output/'graph_smoke_failure.json',{'status':'FAIL','reason':'No graph-generated READ in initial smoke','case_ids':[r['id'] for r in smoke]})
                break
    report = aggregate(rows)
    failure_modes=Counter()
    for row in rows:
        result=row['result'];d=result['diagnostics'];metrics=row['metrics']
        if d.get('pending_goals') and d.get('stop_reason')=='model_stop':failure_modes['premature_stop_with_pending_goals']+=1
        for step in result['private_debug']['steps']:
            if step.get('error_type'):failure_modes[step['error_type']]+=1
        if metrics.get('tool_name_f1',1)<1:failure_modes['missing_or_extra_tools']+=1
        elif metrics.get('exact_call_f1',1)<1:failure_modes['argument_mismatch']+=1
    report['top_failure_modes']=[{'failure_mode':name,'count':count} for name,count in failure_modes.most_common(5)]
    report.update({"fingerprint": fingerprint, "n_runs": len(rows), "gpu_executed": True,
         "graph_runtime_approved_edges": len(bootstrap_graph["edges"]),
         "graph_assistance_possible": bool(bootstrap_graph["edges"]),
         'graph_smoke_passed':any(r['result']['diagnostics'].get('graph_reads',0)>0 for r in rows),
         'status':'COMPLETE' if len(rows)==len(case_ids)*len(modes) else 'SMOKE_FAILED',
         'requested_runs':len(case_ids)*len(modes),'budget_is_hard_pre_call_gate_not_kernel_preemption':True,
         "limitations": ["Fuzzy-call F1 is a Spec proxy; official tokenizer/array/optional rules are unverified.",
             'State Match requires independent reference hashes; official competition score remains null without its evaluator.',
             '30s budget gates generation and new tool calls, but cannot preempt an in-progress CUDA pass or ToolEnv call.',
             "Training data helped discover graph paths; this is train-development evaluation.",
             "Without operator-approved edges, assisted arm preserves baseline scheduling.",
             "Planner/policy token and latency deltas are measured, never assumed to improve.",
             "Graph does not certify completion or executed-write semantic safety."]})
    write_json(output / "benchmark_report.json", report)
    with (output / "traces_PRIVATE.jsonl").open("w", encoding="utf-8") as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (output / "predictions.jsonl").open("w", encoding="utf-8") as f:
        for row in rows: f.write(json.dumps({"arm": row["arm"], **row["result"]["public"]}, ensure_ascii=False) + "\n")
    return report
