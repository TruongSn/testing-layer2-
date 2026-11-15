"""Current PRODUCTION graph gates; scripted Policy/recorded CPU observations.

Development labels are fixture/evaluation data only, never passed to the Harness.
These tests prove mechanisms, not Qwen accuracy or real ToolEnv/GPU performance.
"""
from __future__ import annotations
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from lifemate_graph.b043 import B043Harness,B043Bridge,goal_contexts
from lifemate_graph.common import read_json,get_path,set_path,object_sha,path_entries,field_equal
from lifemate_graph.compiler import load_compiled
from lifemate_graph.context import recover_context
from lifemate_graph.runtime import BackwardDependencyResolver,EvidenceLedger,GoalContext,GroundedValue,Status
from tests.fixtures import FakeToolEnv
from tests.ready_cases import ready_case,run_ready_case,READY,span

ROOT=Path(__file__).resolve().parents[1]
NOTEBOOK=ROOT/'inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb'

def final_graph():
    return load_compiled(ROOT/'artifacts_final/compiled_graph_v3.json',ROOT/'artifacts_final/graph_manifest.json',ROOT/'inputs/train_tools_catalog.json')

def contract_case(fixture,graph):
    e=graph['edges'][fixture['edge_id']];request='';subjects={};bindings={}
    def append(value):
        nonlocal request
        text=str(value).lower() if isinstance(value,bool) else str(value)
        start=len(request);request+=text+' | '
        return span(request,value) if not isinstance(value,bool) else {'value':value,'start':start,'end':start+len(text)}
    subjects[e['consumer_tool']+':'+fixture['target_path']]={k:append(v) for k,v in fixture['selectors'].items()}
    bindings[e['producer_tool']]={k:append(v) for k,v in fixture['producer_arguments'].items() if not isinstance(v,(dict,list))}
    def concrete(value,prefix=''):
        if isinstance(value,dict):
            for k,v in value.items():yield from concrete(v,prefix+('.' if prefix else '')+k)
        elif isinstance(value,list):
            for i,v in enumerate(value):yield from concrete(v,f'{prefix}[{i}]')
        else:yield prefix,value
    bindings[e['consumer_tool']]={k:append(v) for k,v in concrete(fixture['consumer_arguments'])}
    branches=graph['alternative_branches'].get(e['consumer_tool'],[])
    chosen=[fixture['target_path']] if [fixture['target_path']] in branches else (branches[0] if branches else [])
    raw={'goal_id':'g1','completion_tool':e['consumer_tool'],'description':request,'conditional':False,'status':'pending','graph_context':{
        'subjects':subjects,'bindings':bindings,'required_optional':[fixture['target_path']],
        'selected_alternatives':{e['consumer_tool']:chosen}}}
    return {'key':(e['producer_tool'],e['consumer_tool'],e['target_argument_path']), 'request':request,'goal':raw,
        'args':fixture['consumer_arguments'],'responses':{e['producer_tool']:fixture['observation']}}

def run_development(id_,mode='GRAPH_ASSISTED_SAFE_READ',decisions=None,mutate=None,planner_goals=None):
    fixture=read_json(ROOT/'tests/data/development_regressions.json')[id_]
    item=copy.deepcopy(fixture['input'])
    item.pop('label',None)
    graph=final_graph();h=B043Harness(NOTEBOOK,graph,mode);m=h.module
    m.CONFIG['soft_deadline_seconds']=30;m.CONFIG['max_stop_reviews']=2
    target=fixture['evaluation_expected_calls'][-1]['name']
    goals=[] if planner_goals is None else copy.deepcopy(planner_goals)
    m.make_goals=lambda *a,**kw:(copy.deepcopy(goals),{'input_tokens':5,'output_tokens':5,'model_seconds':.001})
    choices=iter(decisions if decisions is not None else [{'action':'call','name':target,'arguments':{}},{'action':'stop'}])
    m.generate_once=lambda *a,**kw:(json.dumps(next(choices,{'action':'stop'}),ensure_ascii=False),5,5,.001)
    class Env(FakeToolEnv):pass
    Env.tools=graph['tools'];Env.responses=copy.deepcopy(fixture['recorded_tool_responses'])
    if mutate:mutate(h,Env,item)
    result=h.run(item,graph['tools'],Env,ROOT)
    result['validation_audit']=Env.last.validations
    result['fixture_scope']='CPU_SCRIPTED_POLICY_RECORDED_OBSERVATIONS_NOT_MODEL_OR_OFFICIAL_TOOLENV'
    return result


@unittest.skipUnless((ROOT/'artifacts_final/compiled_graph_v3.json').exists(),'Final production graph not present')
class FinalGraphTests(unittest.TestCase):
    def test_phone_alternative_requires_same_contact_not_merely_existing_number(self):
        graph=final_graph()
        for variant in ['wrong','matched','direct_user']:
            with self.subTest(variant=variant):
                request='Nhắn cho Mai: hello.' if variant!='direct_user' else 'Nhắn cho Mai số 0901234567: hello.'
                case={'key':('contacts.search','sms.send','contact_id'),'request':request,
                    'goal':{'goal_id':'g1','completion_tool':'sms.send','description':request,'status':'pending','conditional':False},
                    'args':{'phone_number':'0912345678' if variant=='wrong' else '0901234567','content':'hello'},
                    'responses':{'contacts.search':{'ok':True,'contacts':[
                        {'id':'mai','name':'Mai','phone':'0901234567'},
                        {'id':'other','name':'Mai khách hàng','phone':'0912345678'}]}}}
                r=run_ready_case(case,graph);calls=r['public']['tool_calls']
                if variant=='wrong':
                    self.assertFalse(any(c['name']=='sms.send' for c in calls))
                    self.assertTrue(any(e['status']=='BLOCKED_UNSAFE' for e in r['private_debug']['graph_events']))
                else:self.assertEqual(calls[-1]['name'],'sms.send')
                if variant=='direct_user':self.assertEqual(r['diagnostics']['graph_reads'],0)
    def test_all_approved_dependencies_participate_in_real_loop(self):
        graph=final_graph()
        for fixture in read_json(ROOT/'tests/data/approved_contracts.json'):
            with self.subTest(edge=fixture['edge_id']):
                case=contract_case(fixture,graph);r=run_ready_case(case,graph)
                self.assertEqual([c['name'] for c in r['public']['tool_calls']],[case['key'][0],case['key'][1]])
                self.assertEqual(r['diagnostics']['graph_reads'],1)
                self.assertEqual(len(r['validation_audit']),2)
                self.assertEqual(get_path(r['public']['tool_calls'][-1]['arguments'],fixture['target_path']),fixture['expected_value'])
                self.assertTrue(all(s['origin']=='model_policy' for s in r['private_debug']['steps'] if s.get('tool_name')==case['key'][1] and 'tool_result' in s))
        # The two context edges execute in the no-context development scenarios.
        for id_ in ['train_0136','train_0951']:
            r=run_development(id_)
            self.assertTrue(any(e.get('edge_ids') and any(graph['edges'][i].get('context_requirement') for i in e['edge_ids']) for e in r['private_debug']['graph_events']))

    def test_every_approved_data_edge_read_plan_and_evidence_binding(self):
        graph=final_graph();fixtures=read_json(ROOT/'tests/data/approved_contracts.json')
        self.assertEqual({f['edge_id'] for f in fixtures},{i for i,e in graph['edges'].items() if not e.get('context_requirement')})
        for fixture in fixtures:
            with self.subTest(edge=fixture['edge_id']):
                e=graph['edges'][fixture['edge_id']];request='';subjects={};bindings={}
                def append(value):
                    nonlocal request
                    text=str(value).lower() if isinstance(value,bool) else str(value)
                    start=len(request);request+=text+' | '
                    return GroundedValue(value,start,start+len(text))
                subjects[fixture['target_path']]={k:append(v) for k,v in fixture['selectors'].items()}
                bindings[e['producer_tool']]={k:append(v) for k,v in fixture['producer_arguments'].items() if not isinstance(v,(dict,list))}
                def concrete(value,prefix=''):
                    if isinstance(value,dict):
                        for k,v in value.items():yield from concrete(v,prefix+('.' if prefix else '')+k)
                    elif isinstance(value,list):
                        for i,v in enumerate(value):yield from concrete(v,f'{prefix}[{i}]')
                    else:yield prefix,value
                bindings[e['consumer_tool']]={k:append(v) for k,v in concrete(fixture['consumer_arguments'])}
                branches=graph['alternative_branches'].get(e['consumer_tool'],[])
                selected=[fixture['target_path']] if [fixture['target_path']] in branches else (branches[0] if branches else [])
                goal=GoalContext('gate',e['consumer_tool'],subjects,bindings,required_optional={fixture['target_path']},selected_alternatives={e['consumer_tool']:selected})
                ledger=EvidenceLedger();resolver=BackwardDependencyResolver(graph)
                plan=resolver.resolve(request,[goal],goal.target_tool,fixture['consumer_arguments'],graph['tools'],ledger,goal=goal)
                self.assertEqual(plan.status,Status.NEED_READ,plan.reason)
                self.assertEqual(plan.read_tool,e['producer_tool'])
                ledger.observe(e['producer_tool'],fixture['producer_arguments'],fixture['observation'],'gate')
                result=resolver.resolve(request,[goal],goal.target_tool,fixture['consumer_arguments'],graph['tools'],ledger,goal=goal)
                self.assertEqual(result.status,Status.SATISFIED,result.reason)
                self.assertEqual(get_path(result.arguments,fixture['target_path']),fixture['expected_value'])
                self.assertEqual(result.proofs[fixture['target_path']]['source'],'tool_observation')
                wrong=copy.deepcopy(result.arguments);set_path(wrong,fixture['target_path'],'wrong_subject_id')
                denied=resolver.resolve(request,[goal],goal.target_tool,wrong,graph['tools'],ledger,goal=goal)
                self.assertEqual(denied.status,Status.BLOCKED_UNSAFE)
                ledger.observations[0].goal_id='other_goal'
                scoped=resolver.resolve(request,[goal],goal.target_tool,fixture['consumer_arguments'],graph['tools'],ledger,goal=goal)
                self.assertNotEqual(scoped.status,Status.SATISFIED)
                ledger.observations[0].goal_id='gate'
                if '[]' in e['source_output_path']:
                    duplicate=copy.deepcopy(fixture['observation']);root=e['source_output_path'].split('[]')[0]
                    record=next(r for v,r in path_entries(duplicate,e['source_output_path']) if all(field_equal(k,get_path(r,k),s) for k,s in fixture['selectors'].items()))
                    get_path(duplicate,root).append(copy.deepcopy(record))
                    ledger.observations[0].result=duplicate
                    denied=resolver.resolve(request,[goal],goal.target_tool,fixture['consumer_arguments'],graph['tools'],ledger,goal=goal)
                    self.assertEqual(denied.status,Status.AMBIGUOUS)

    def test_all_candidates_have_explicit_completed_review(self):
        from lifemate_graph.validation import validate_edge
        g=read_json(ROOT/'artifacts_completed/full_dependency_graph_v3.json')
        old=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')
        self.assertEqual({e['edge_id'] for e in old['dependencies']},{e['edge_id'] for e in g['dependencies']})
        self.assertEqual(len(g['dependencies']),190)
        self.assertTrue(all(e['completion_review']['reason'] for e in g['dependencies']))
        rejected={e['edge_id'] for e in old['dependencies'] if e['validation_status']=='REJECTED'}
        self.assertFalse(rejected & final_graph()['edges'].keys())
        self.assertEqual(len(g['tools']),147)
        nodes={n['id'] for n in g['nodes']}
        self.assertTrue(all(r['from'] in nodes and r['to'] in nodes for r in g['relations']))
        self.assertTrue(all(validate_edge(e,g['tools'])[0]==e['validation_status'] for e in g['dependencies']))

    def test_no_context_and_empty_planner_all_five_development_scenarios(self):
        fixtures=read_json(ROOT/'tests/data/development_regressions.json')
        for id_,fixture in fixtures.items():
            with self.subTest(id=id_):
                result=run_development(id_)
                calls=result['public']['tool_calls']
                self.assertEqual(calls,fixture['evaluation_expected_calls'])
                self.assertGreater(result['diagnostics']['graph_reads'],0)
                self.assertTrue(all(v['errors']==[] for v in result['validation_audit']))
                writes=[s for s in result['private_debug']['steps'] if s.get('tool_result') and final_graph()['tools'].get(s.get('tool_name'),{}).get('kind')=='write']
                self.assertTrue(all(s['origin']=='model_policy' for s in writes))

    def test_wrong_track_even_if_present_in_library_cannot_bypass_chat(self):
        result=run_development('train_0951',decisions=[{'action':'call','name':'music.add_to_playlist','arguments':{'track_id':'trk_860','playlist_id':'pl_850'}},{'action':'stop'}])
        calls=result['public']['tool_calls']
        self.assertEqual([c['name'] for c in calls],['chat_ott.list_chats','chat_ott.read_chat','music.list_tracks'])
        self.assertTrue(any(e['status']=='BLOCKED_UNSAFE' for e in result['private_debug']['graph_events']))

    def test_message_sender_time_and_duplicate_occurrence_fail_closed(self):
        for variant in ['sender','time','duplicate']:
            def mutate(h,E,item):
                messages=E.responses['chat_ott.read_chat']['messages']
                if variant=='sender':messages[0]['from']='Someone else'
                if variant=='time':messages[0]['time']='2026-12-12T20:40:00+07:00'
                if variant=='duplicate':messages.append(copy.deepcopy(messages[0]))
            with self.subTest(variant=variant):
                result=run_development('train_0951',mutate=mutate)
                self.assertFalse(any(c['name']=='music.add_to_playlist' for c in result['public']['tool_calls']))
                self.assertEqual(sum(c['name']=='chat_ott.read_chat' for c in result['public']['tool_calls']),1)

    def test_address_word_is_not_a_global_identity_alias(self):
        def mutate(h,E,item):
            E.responses['chat_ott.list_chats']['chats'].append({'id':'other_lan','name':'Chị Lan','type':'direct'})
        r=run_development('train_0463',mutate=mutate)
        self.assertEqual([c['name'] for c in r['public']['tool_calls']],['chat_ott.list_chats'])
        self.assertTrue(any(e['status']=='AMBIGUOUS' for e in r['private_debug']['graph_events']))

    def test_unverified_condition_flag_does_not_authorize_action(self):
        def mutate(h,E,item):item['audio_text']='Nếu sách còn thì '+item['audio_text']
        r=run_development('train_0488',mutate=mutate,planner_goals=[{
            'goal_id':'g1','completion_tool':'library.reserve','description':'reserve','status':'pending','conditional':False}])
        self.assertFalse(any(c['name']=='library.reserve' for c in r['public']['tool_calls']))

    def test_wrong_planner_goal_does_not_starve_explicit_request_read(self):
        r=run_development('train_0463',planner_goals=[{'goal_id':'bad','completion_tool':'tasks.list',
            'description':'irrelevant','status':'pending','conditional':False}])
        self.assertEqual([c['name'] for c in r['public']['tool_calls']],['chat_ott.list_chats','chat_ott.read_chat'])

    def test_multiple_explicit_goals_without_context_keep_scopes_and_order(self):
        graph=final_graph();fixtures=read_json(ROOT/'tests/data/development_regressions.json')
        h=B043Harness(NOTEBOOK,graph,'GRAPH_ASSISTED_SAFE_READ');m=h.module
        request='Lấy cuốn Dòng sông mùa hạ; mở bài Sớm mai ngoài hiên.'
        goals=[{'goal_id':'g1','completion_tool':'library.reserve','description':'borrow','status':'pending','conditional':False},
               {'goal_id':'g2','completion_tool':'music.play','description':'play','status':'pending','conditional':False}]
        m.make_goals=lambda *a,**k:(goals,{'input_tokens':1,'output_tokens':1,'model_seconds':.001})
        script=iter([{'action':'call','name':'library.reserve','arguments':{}},{'action':'call','name':'music.play','arguments':{}},{'action':'stop'}])
        m.generate_once=lambda *a,**k:(json.dumps(next(script,{'action':'stop'})),1,1,.001)
        class Env(FakeToolEnv):pass
        Env.tools=graph['tools'];Env.responses={**fixtures['train_0488']['recorded_tool_responses'],**fixtures['train_0557']['recorded_tool_responses']}
        result=h.run({'id':'multi_goal_fixture','audio_text':request,'history':[],
            'context':{'installed_apps':{'fixture':{'tools':list(graph['tools'])}}}},graph['tools'],Env,ROOT)
        self.assertEqual([c['name'] for c in result['public']['tool_calls']],['library.search','library.reserve','music.list_tracks','music.play'])
        self.assertEqual(result['diagnostics']['graph_reads'],2)

    def test_repeated_stop_has_bounded_grounded_feedback_no_graph_write(self):
        result=run_development('train_0136',decisions=[{'action':'stop'}]*8)
        self.assertGreater(result['diagnostics']['graph_reads'],0)
        self.assertFalse(any(c['name']=='food_delivery.place_order' for c in result['public']['tool_calls']))
        repairs=[e for e in result['private_debug']['graph_events'] if e['status']=='STOP_REPAIR']
        self.assertTrue(repairs);self.assertLessEqual(len(repairs),2)
        result=run_development('train_0136',decisions=[{'action':'stop'},{'action':'call','name':'food_delivery.place_order','arguments':{}},{'action':'stop'}])
        self.assertEqual(result['public']['tool_calls'][-1]['name'],'food_delivery.place_order')

    def test_menu_parent_and_group_quantity_are_checked(self):
        wrong={'restaurant_id':'rest_01','items':[{'item_id':'rest_01_m2','quantity':4}]}
        result=run_development('train_0136',decisions=[{'action':'call','name':'food_delivery.place_order','arguments':wrong},{'action':'stop'}])
        self.assertFalse(any(c['name']=='food_delivery.place_order' for c in result['public']['tool_calls']))
        wrong['items'][0]['quantity']=3;wrong['restaurant_id']='another_restaurant'
        result=run_development('train_0136',decisions=[{'action':'call','name':'food_delivery.place_order','arguments':wrong},{'action':'stop'}])
        self.assertFalse(any(c['name']=='food_delivery.place_order' for c in result['public']['tool_calls']))

    def test_policy_read_and_graph_read_share_goal_evidence(self):
        decisions=[{'action':'call','name':'chat_ott.list_chats','arguments':{}},
                   {'action':'call','name':'chat_ott.read_chat','arguments':{'chat_id':'chat_453'}},
                   {'action':'call','name':'music.add_to_playlist','arguments':{}},{'action':'stop'}]
        with patch.object(B043Bridge,'preflight',return_value=False):result=run_development('train_0951',decisions=decisions)
        self.assertEqual([c['name'] for c in result['public']['tool_calls']],['chat_ott.list_chats','chat_ott.read_chat','music.list_tracks','music.add_to_playlist'])
        self.assertEqual(result['diagnostics']['graph_reads'],1)

    def test_all_original_19_contracts_still_execute_with_final_graph(self):
        for key in READY:
            with self.subTest(edge=key):
                result=run_ready_case(ready_case(key),final_graph())
                self.assertEqual([c['name'] for c in result['public']['tool_calls']],[key[0],key[1]])

    def test_nested_path_operations_do_not_broadcast(self):
        value={};set_path(value,'items[0].item_id','id1');set_path(value,'items[0].quantity',2)
        set_path(value,'items[1].item_id','id2')
        self.assertEqual(get_path(value,'items[0].item_id'),'id1')
        self.assertEqual(get_path(value,'items[1].item_id'),'id2')
        with self.assertRaises(ValueError):set_path(value,'items[].item_id','bad')

    def test_approved_device_predicate_blocks_wrong_class(self):
        graph=final_graph();request='Đèn mẫu Phòng khách';goal=GoalContext('g1','smart_home.light_set',{
            'device_id':{'name':GroundedValue('Đèn mẫu',0,7),'room':GroundedValue('Phòng khách',8,19)}})
        ledger=EvidenceLedger();ledger.observe('smart_home.get_status',{}, {'ok':True,'devices':[{'id':'wrong_fan','name':'Đèn mẫu','room':'Phòng khách','type':'fan'}]},'g1')
        r=BackwardDependencyResolver(graph).resolve(request,[goal],goal.target_tool,{'power':'on'},graph['tools'],ledger,goal=goal)
        self.assertNotEqual(r.status,Status.SATISFIED)

    def test_derived_context_cannot_reuse_other_goal_or_write_epoch(self):
        graph=final_graph();fixture=read_json(ROOT/'tests/data/development_regressions.json')['train_0951'];item=fixture['input']
        raw={'goal_id':'g1','completion_tool':'music.add_to_playlist'};ledger=EvidenceLedger()
        responses=fixture['recorded_tool_responses']
        ledger.observe('chat_ott.list_chats',{},responses['chat_ott.list_chats'],'g1')
        ledger.observe('chat_ott.read_chat',{'chat_id':'chat_453'},responses['chat_ott.read_chat'],'other')
        goal=recover_context(raw,item['audio_text'],graph,ledger,item)
        self.assertNotEqual(goal.context_requirements['chat_referenced_track']['status'],'satisfied')
        ledger.observations[-1].goal_id='g1'
        goal=recover_context(raw,item['audio_text'],graph,ledger,item)
        self.assertEqual(goal.context_requirements['chat_referenced_track']['status'],'satisfied')
        ledger.observe('tasks.complete',{}, {'ok':True},'other','write')
        r=BackwardDependencyResolver(graph).resolve(item['audio_text'],[goal],goal.target_tool,{},graph['tools'],ledger,goal=goal)
        self.assertEqual(r.status,Status.BLOCKED_UNSAFE)

    def test_disabled_keeps_baseline_without_graph_work(self):
        r=run_development('train_0463',mode='GRAPH_DISABLED')
        self.assertEqual(r['diagnostics']['graph_reads'],0)
        self.assertFalse(r['private_debug']['graph_events'])

if __name__=='__main__':unittest.main()
