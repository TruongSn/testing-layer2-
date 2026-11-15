"""Save current-production CPU mechanism traces and an honest GPU-not-run report."""
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lifemate_graph.common import read_json,write_json,object_sha,sha256
from lifemate_graph.evaluation import call_metrics,execution_metrics
from tests.test_final_graph import final_graph,run_development,contract_case
from tests.ready_cases import run_ready_case

def main():
    output=ROOT/'reports/final';output.mkdir(exist_ok=True)
    graph=final_graph();rows=[]
    fixtures=read_json(ROOT/'tests/data/development_regressions.json')
    for id_,fixture in fixtures.items():
        for mode in ['GRAPH_DISABLED','GRAPH_ASSISTED_SAFE_READ']:
            result=run_development(id_,mode)
            rows.append({'id':id_,'arm':mode,'scope':result['fixture_scope'],'result':result,
                'post_execution_debug':call_metrics(result['public']['tool_calls'],fixture['evaluation_expected_calls'])})
    contracts=[]
    for fixture in read_json(ROOT/'tests/data/approved_contracts.json'):
        result=run_ready_case(contract_case(fixture,graph),graph)
        assert len(result['public']['tool_calls'])==2 and result['diagnostics']['graph_reads']==1
        contracts.append({'edge_id':fixture['edge_id'],'scope':'CPU_SCRIPTED_POLICY_RECORDED_OBSERVATION_CONTRACT_GATE','result':result})
    for name,data in [('cpu_paired_traces_PRIVATE.jsonl',rows),('approved_contract_traces_PRIVATE.jsonl',contracts)]:
        with (output/name).open('w',encoding='utf-8') as f:
            for row in data:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    with (output/'cpu_predictions.jsonl').open('w',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps({'arm':row['arm'],'scope':row['scope'],**row['result']['public']},ensure_ascii=False)+'\n')
    active=[r for r in rows if r['arm']=='GRAPH_ASSISTED_SAFE_READ']
    summary={'scope':'CPU_MECHANISM_ONLY_NOT_QWEN_ACCURACY_LATENCY_OR_OFFICIAL_TOOLENV',
        'production_runtime_edges':len(graph['edges']),'data_contract_closed_loops':len(contracts),'context_contract_closed_loops':2,
        'development_scenarios':len(active),'scenarios_with_graph_reads':sum(r['result']['diagnostics']['graph_reads']>0 for r in active),
        'graph_reads':sum(r['result']['diagnostics']['graph_reads'] for r in active),
        'per_case':[{'id':r['id'],'graph_reads':r['result']['diagnostics']['graph_reads'],
                     'executed_tools':[c['name'] for c in r['result']['public']['tool_calls']]} for r in active],
        'automatic_writes':sum(s.get('origin')=='b043_graph_read' and graph['tools'].get(s.get('tool_name'),{}).get('kind')=='write'
            for row in rows for s in row['result']['private_debug']['steps']),
        'compiled_payload_sha256':graph['payload_sha256'],'qwen_accuracy':None,'gpu_latency':None}
    assert summary['scenarios_with_graph_reads']==5 and summary['automatic_writes']==0
    write_json(output/'cpu_mechanism_report.json',summary)
    write_json(output/'benchmark_report.json',{'status':'NOT_RUN','gpu_executed':False,
        'reason':'Local torch/transformers/zaic_tools and Kaggle execution credentials are unavailable; supplied notebook runs the real paired GPU evaluation.',
        'arms':{'GRAPH_DISABLED':{'accuracy':None,'latency_seconds':None},'GRAPH_ASSISTED_SAFE_READ':{'accuracy':None,'latency_seconds':None}},
        'state_match':None,'official_challenge_score':None,'cpu_mechanism_report':'cpu_mechanism_report.json',
        'known_reported_gpu_failures':['train_0136','train_0463','train_0488','train_0557','train_0951'],
        'next_run':'Run All LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb: five-case graph smoke followed by representative paired 40 cases',
        'state_match_prerequisite':'Independent authoritative full-state reference hashes; train partial final_state is insufficient'})
    manifest=read_json(ROOT/'artifacts_final/graph_manifest.json')
    write_json(output/'source_graph_hashes.json',{'graph_content_sha256':graph['graph_content_sha256'],
        'full_graph_file_sha256':sha256(ROOT/'artifacts_completed/full_dependency_graph_v3.json'),
        'compiled_file_sha256':sha256(ROOT/'artifacts_final/compiled_graph_v3.json'),
        'compiled_payload_sha256':graph['payload_sha256'],'catalog_sha256':graph['catalog_digest'],
        'approval_sha256':manifest['approval_overlay_sha256'],'b042_sha256':sha256(ROOT/'inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb'),
        'source_aggregate_sha256':object_sha(manifest['implementation_sha256']),'source_sha256':manifest['implementation_sha256']})
    print(json.dumps(summary,ensure_ascii=False))
    return summary
if __name__=='__main__':main()
