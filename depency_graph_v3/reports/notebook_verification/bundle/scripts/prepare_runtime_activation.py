"""Prepare the final nineteen-edge proposal and CPU evidence without authorizing it."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lifemate_graph.common import canonical, object_sha, read_csv, read_json, sha256, write_csv, write_json
from tests.ready_cases import READY, CONSUMER_INPUTS, proposed_decisions, ready_case, run_ready_case, synthetic_profile


def check_closed_loops(profile, output):
    graph=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')
    by_key={(e['producer_tool'],e['consumer_tool'],e['target_argument_path']):e for e in graph['dependencies']}
    traces=[]
    for key in READY:
        if by_key[key]['edge_id'] not in profile['edges']:continue
        case=ready_case(key)
        r=run_ready_case(case,profile)
        calls=r['public']['tool_calls']
        assert [c['name'] for c in calls]==[key[0],key[1]],(key,calls)
        for slot,value in case['expected'].items():assert calls[-1]['arguments'][slot]==value,(key,slot)
        assert r['diagnostics']['graph_reads']==1
        assert len(r['validation_audit'])==2
        steps=r['private_debug']['steps']
        if profile['tools'][key[1]]['kind']=='write':
            assert all(s.get('origin')=='model_policy' for s in steps if s.get('tool_name')==key[1] and 'tool_result' in s)
        traces.append({'edge_id':by_key[key]['edge_id'],'producer':key[0],'consumer':key[1],
            'request':case['request'],'actual_calls':calls,'graph_reads':r['diagnostics']['graph_reads'],
            'graph_events':r['private_debug']['graph_events'],'steps':steps,'validation_audit':r['validation_audit']})
    report={'status':'PASS','authorization_scope':profile['runtime_scope'],'fake_toolenv':True,'scripted_policy':True,
        'qwen_gpu_tested':False,'closed_loop_cases':len(traces),'compiled_payload_sha256':profile['payload_sha256'],
        'write_execution':'Only explicit scripted Policy CALL proposals pass through original B042 guards and invoke',
        'traces':traces}
    write_json(output,report)
    return report


def main():
    source=ROOT/'artifacts'
    original=read_json(source/'graph_manifest.json')
    for name,digest in original['artifacts_sha256'].items():assert sha256(source/name)==digest,name
    graph=read_json(source/'full_dependency_graph_v3.json')
    assert object_sha(graph)==original['graph_content_sha256']
    decisions=proposed_decisions(graph)
    assert all(d['decision']=='PENDING' for d in decisions)
    draft={'graph_content_sha256':object_sha(graph),'reviewer':None,'scope':'PRODUCTION','decisions':decisions,
           'proposal_status':'AWAITING_SINGLE_USER_APPROVAL','proposed_edge_count':len(decisions),
           'catalog_sha256':graph['catalog_digest'],
           'instructions':'Pending final proposal. No operator authority. After explicit approval create operator_approvals.json for exactly the authorized edges; compile-only, no discovery.'}
    write_json(ROOT/'operator_approvals.proposed.json',draft)
    by_id={e['edge_id']:e for e in graph['dependencies']}
    rows=[]
    for d in decisions:
        e=by_id[d['edge_id']];c=e['consumer_tool']
        conditions=['exact current-request selector within the goal clause','unique record after byte-identical duplicate removal',
                    'both tools installed; condition=true; fresh same-goal evidence','no rank/alias/fuzzy guessing',
                    'graph schedules READ only; Policy proposes every target CALL; existing validators/guards still run']
        if c=='sms.read_recent':conditions.append('contact_id required_optional intent or a proposed contact_id; otherwise no contact lookup')
        if c=='music.play':conditions.append('exactly one alternative has explicit subject/binding, or selected_alternatives is explicit; otherwise abstain')
        if c in CONSUMER_INPUTS:conditions.append('required user provenance: '+', '.join(CONSUMER_INPUTS[c]))
        rows.append({'edge_id':d['edge_id'],'producer_tool':e['producer_tool'],'consumer_tool':c,
          'source_output_path':d['source_output_path'],'target_argument_path':e['target_argument_path'],
          'consumer_kind':graph['tools'][c]['kind'].upper(),'risk_level':e['risk_level'],'read_cost':d['read_cost'],
          'selector_keys':d['selector_keys'],'producer_argument_bindings':d['producer_argument_bindings'],
          'consumer_input_requirements':d.get('consumer_input_requirements',[]),'conditions':conditions,
          'reason':d['reason'],'decision':'PENDING','proposed_activation':'FINAL_BATCH_19_AWAITING_USER'})
    write_csv(ROOT/'runtime_activation_final.csv',rows,list(rows[0]))
    # Keep the original 72-edge audit; only its activation proposal changes.
    audit=read_csv(ROOT/'runtime_approval_review.csv')
    for r in audit:
        if r['edge_id'] in {d['edge_id'] for d in decisions}:r['proposed_activation']='FINAL_BATCH_19_AWAITING_USER'
    write_csv(ROOT/'runtime_approval_review.csv',audit,list(audit[0]))
    _,profile,_=synthetic_profile()
    report=check_closed_loops(profile,ROOT/'reports/ready19_closed_loop.json')
    assert report['closed_loop_cases']==19 and profile['runtime_scope']=='SYNTHETIC_ONLY'
    m={'status':'READY_FOR_SINGLE_USER_APPROVAL','graph_rebuilt':False,'graph_content_sha256':object_sha(graph),
       'source_artifacts_sha256':original['artifacts_sha256'],'catalog_sha256':graph['catalog_digest'],
       'production_approved_edges':len(read_json(source/'compiled_graph_v3.json')['edges']),
       'proposal_count':19,'test_scope':'SYNTHETIC_ONLY / FakeToolEnv / real B043 loop',
       'runtime_code_sha256':{p.name:sha256(p) for p in sorted((ROOT/'lifemate_graph').glob('*.py'))},
       'files_sha256':{n:sha256(ROOT/n) for n in ['operator_approvals.proposed.json','runtime_activation_final.csv',
          'runtime_approval_review.csv','reports/ready19_closed_loop.json']}}
    write_json(ROOT/'runtime_review/activation_manifest.json',m)
    print(canonical({k:m[k] for k in ['status','graph_rebuilt','proposal_count','production_approved_edges','test_scope']}))
    return m


if __name__=='__main__':main()
