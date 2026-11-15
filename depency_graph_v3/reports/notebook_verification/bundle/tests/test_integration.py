import copy
import unittest
from pathlib import Path
from lifemate_graph.b043 import B043Harness, B043Bridge, runtime_item
from lifemate_graph.common import read_json
from lifemate_graph.demo import run_scenario, run_demo
from lifemate_graph.runtime import GraphMode

ROOT=Path(__file__).resolve().parents[1]
NOTEBOOK=ROOT/'inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb'


class IntegrationTests(unittest.TestCase):
    def test_graph_changes_real_execution_path(self):
        r=run_scenario(root=ROOT)
        self.assertEqual([c['name'] for c in r['public']['tool_calls']],['demo.people','demo.send'])
        self.assertEqual(r['diagnostics']['graph_reads'],1)
        self.assertEqual(len(r['validation_audit']),2)
        self.assertTrue(any(e['status']=='SATISFIED' and e.get('proofs') for e in r['private_debug']['graph_events']))
    def test_baseline_shadow_trace_equal(self):
        base=run_scenario(GraphMode.GRAPH_DISABLED,ROOT); shadow=run_scenario(GraphMode.GRAPH_SHADOW,ROOT)
        self.assertEqual(base['public'],shadow['public']); self.assertEqual(shadow['diagnostics']['graph_reads'],0)
    def test_ambiguous_subject_never_writes(self):
        r=run_scenario(root=ROOT,people=[{'id':'p1','name':'Mai'},{'id':'p2','name':'Mai'}])
        self.assertEqual([c['name'] for c in r['public']['tool_calls']],['demo.people'])
    def test_duplicate_write_stopped_in_b043(self):
        r=run_scenario(root=ROOT,proposals=[
          {'action':'call','name':'demo.send','arguments':{'content':'hello'}},
          {'action':'call','name':'demo.send','arguments':{'contact_id':'p_mai','content':'hello'}}])
        self.assertEqual(len([c for c in r['public']['tool_calls'] if c['name']=='demo.send']),1)
        self.assertEqual(r['diagnostics']['b042_duplicate_write_suppressed'],1)
    def test_multihop_executes_through_original_invoke_in_order(self):
        from tests.fixtures import compiled, edge, tool
        from lifemate_graph.common import read_json
        ts=compiled()['tools']
        ts['demo.people']['parameters']['properties']['region_id']={'type':'string'}
        ts['demo.people']['parameters']['required']=['region_id']
        ts['demo.regions']=tool('demo.regions')
        g=compiled(ts,[edge(bindings={'query':{'subject_key':'name'}}),edge('demo.regions','demo.people','region_id','regions[].id')])
        request='Nhắn Mai ở North hello'
        plan=[{'goal_id':'g1','completion_tool':'demo.send','description':request,'conditional':False,'status':'pending','observed_step':None,
            'graph_context':{'subjects':{'contact_id':{'name':{'value':'Mai','start':5,'end':8}},
                                       'demo.people:region_id':{'name':{'value':'North','start':11,'end':16}}}}}]
        responses={'demo.regions':{'ok':True,'regions':[{'id':'r1','name':'North'}]},
                   'demo.people':{'ok':True,'people':[{'id':'p_mai','name':'Mai'}]}}
        r=run_scenario(root=ROOT,graph=g,request=request,plan=plan,responses=responses)
        self.assertEqual([c['name'] for c in r['public']['tool_calls']],['demo.regions','demo.people','demo.send'])
        self.assertEqual(r['public']['tool_calls'][1]['arguments']['region_id'],'r1')
        self.assertEqual(len(r['validation_audit']),3)
    def test_graph_read_schema_failure_never_executes(self):
        # Subject-to-query mapping is reviewed, but actual dynamic arguments still face ToolEnv.validate.
        from tests.fixtures import compiled
        g=compiled();g['tools']['demo.people']['parameters']['properties']['query']['enum']=['Other']
        from lifemate_graph.common import object_sha
        g['payload_sha256']=object_sha({k:v for k,v in g.items() if k!='payload_sha256'})
        r=run_scenario(root=ROOT,graph=g)
        self.assertEqual(r['public']['tool_calls'],[])
    def test_toolenv_validate_rejection_blocks_graph_read(self):
        r=run_scenario(root=ROOT,reject_tools={'demo.people'})
        self.assertEqual(r['public']['tool_calls'],[])
        self.assertEqual(len(r['validation_audit']),1)
        self.assertEqual(r['validation_audit'][0]['name'],'demo.people')
    def test_conditional_goal_not_enabled_has_no_graph_read_or_write(self):
        plan=[{'goal_id':'g1','completion_tool':'demo.send','description':'Nhắn Mai hello','conditional':True,
               'status':'pending','observed_step':None,'graph_context':{'subjects':{'contact_id':{
                   'name':{'value':'Mai','start':5,'end':8}}}}}]
        r=run_scenario(root=ROOT,plan=plan)
        self.assertEqual(r['public']['tool_calls'],[])
    def test_missing_edge_uses_baseline_execution(self):
        from tests.fixtures import compiled
        g=compiled(edges=[])
        proposals=[{'action':'call','name':'demo.send','arguments':{'contact_id':'p_mai','content':'hello'}},{'action':'stop'}]
        base=run_scenario(GraphMode.GRAPH_DISABLED,ROOT,graph=g,request='Nhắn p_mai hello',proposals=proposals)
        changed=run_scenario(GraphMode.GRAPH_ASSISTED_SAFE_READ,ROOT,graph=g,request='Nhắn p_mai hello',proposals=proposals)
        self.assertEqual(base['public'],changed['public']);self.assertEqual(len(changed['public']['tool_calls']),1)
    def test_unrelated_active_goal_keeps_legacy_coordinator(self):
        from tests.fixtures import compiled
        from lifemate_graph.b043 import B043Bridge
        h=B043Harness(NOTEBOOK,compiled(),GraphMode.GRAPH_ASSISTED_SAFE_READ,'SYNTHETIC_ONLY')
        b=B043Bridge(h.module,compiled(),GraphMode.GRAPH_ASSISTED_SAFE_READ,'SYNTHETIC_ONLY')
        goals=[{'goal_id':'g0','completion_tool':'demo.unrelated','description':'first','status':'pending','conditional':False},
               {'goal_id':'g1','completion_tool':'demo.send','description':'Nhắn Mai hello','status':'pending','conditional':False,
                'graph_context':{'request_span':{'start':0,'end':14},'subjects':{'contact_id':{'name':{'value':'Mai','start':5,'end':8}}}}}]
        tracker=h.module.GoalTracker(goals)
        b.attach({'audio_text':'Nhắn Mai hello'},['demo.send','demo.people','demo.unrelated'],tracker)
        self.assertFalse(b.owns_prerequisites)
        tracker.goals[0]['status']='candidate_completed'
        self.assertTrue(b.owns_prerequisites)
    def test_multi_goal_same_tool_keeps_subjects_and_values_separate(self):
        request='Nhắn Mai hello; nhắn An bye'
        def planned(id_,name,start,end,text,text_start,span):
            return {'goal_id':id_,'completion_tool':'demo.send','description':name,'conditional':False,'status':'pending','observed_step':None,
                'graph_context':{'request_span':{'start':span[0],'end':span[1]},
                  'subjects':{'contact_id':{'name':{'value':name,'start':start,'end':end}}},
                  'bindings':{'demo.send':{'content':{'value':text,'start':text_start,'end':text_start+len(text)}}}}}
        plan=[planned('g1','Mai',5,8,'hello',9,(0,14)),planned('g2','An',21,23,'bye',24,(16,27))]
        responses={'demo.people':lambda a:{'ok':True,'people':[{'id':'id_'+a['query'],'name':a['query']}]}}
        proposals=[{'action':'call','name':'demo.send','arguments':{'content':'hello'}},
                   {'action':'call','name':'demo.send','arguments':{'content':'bye'}},{'action':'stop'}]
        r=run_scenario(root=ROOT,request=request,plan=plan,responses=responses,proposals=proposals)
        calls=r['public']['tool_calls']
        self.assertEqual([c['name'] for c in calls],['demo.people','demo.send','demo.people','demo.send'])
        self.assertEqual(calls[1]['arguments'],{'contact_id':'id_Mai','content':'hello'})
        self.assertEqual(calls[3]['arguments'],{'contact_id':'id_An','content':'bye'})
    def test_original_progress_suite(self):
        h=B043Harness(NOTEBOOK,read_json(ROOT/'artifacts/compiled_graph_v3.json'))
        h.module.run_b042_offline_tests();h.module.run_b042_closed_loop_tests()
    def test_original_amount_and_recipient_tests_on_b043_loop(self):
        h=B043Harness(NOTEBOOK,read_json(ROOT/'artifacts/compiled_graph_v3.json'),GraphMode.GRAPH_SHADOW)
        m=h.module
        # The original fixtures call run_one_sample; route those calls through B043.
        m.run_one_sample=lambda *a,**kw: h.run(*a,**kw)
        m.run_guarded_self_tests();m.run_b041_offline_tests()
        self.assertEqual(m.run_guarded_closed_loop_test()['status'],'PASS')
        self.assertEqual(m.run_b041_closed_loop_test()['status'],'PASS')
    def test_label_and_hidden_fields_removed_before_runtime(self):
        source={'id':'x','audio_text':'hello','label':{'tool_calls':[{'name':'oracle'}]},'expected_tool_calls':['bad'],
          'future_state':{'x':1},'context':{'current_time':'now','hidden_state':{'x':1}},'history':[]}
        clean=runtime_item(source)
        self.assertNotIn('label',clean);self.assertNotIn('future_state',clean);self.assertNotIn('hidden_state',clean['context'])
    def test_closed_loop_report(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r=run_demo(ROOT,Path(tmp)/'demo.json');self.assertEqual(r['status'],'PASS')


if __name__=='__main__':unittest.main()
