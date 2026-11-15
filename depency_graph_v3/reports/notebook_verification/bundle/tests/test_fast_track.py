from __future__ import annotations
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lifemate_graph.common import read_json, write_json, object_sha, canonical, sha256
from lifemate_graph.compiler import compile_existing, load_compiled
from lifemate_graph.runtime import BackwardDependencyResolver, EvidenceLedger, GoalContext, GroundedValue, Status
from lifemate_graph.b043 import B043Bridge
from tests.ready_cases import ROOT, READY, ready_case, synthetic_profile, run_ready_case, span
from tests.fixtures import compiled, goal, literal


class FastTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.graph,cls.profile,cls.overlay=synthetic_profile()
    def test_all_19_contracts_in_real_loop_with_synthetic_grants(self):
        for key in READY:
            with self.subTest(edge=key):
                case=ready_case(key);r=run_ready_case(case,self.profile)
                calls=r['public']['tool_calls']
                self.assertEqual([c['name'] for c in calls],[key[0],key[1]])
                self.assertEqual(r['diagnostics']['graph_reads'],1)
                for slot,value in case['expected'].items():self.assertEqual(calls[-1]['arguments'][slot],value)
                self.assertEqual(len(r['validation_audit']),2)
                if self.profile['tools'][key[1]]['kind']=='write':
                    writes=[s for s in r['private_debug']['steps'] if s.get('tool_name')==key[1] and 'tool_result' in s]
                    self.assertEqual(writes[0]['origin'],'model_policy')
    def test_policy_read_is_scoped_and_reused(self):
        key=('contacts.search','contacts.get','contact_id');case=ready_case(key)
        proposals=[{'action':'call','name':key[0],'arguments':{'query':'Mai'}},
                   {'action':'call','name':key[1],'arguments':{}},{'action':'stop'}]
        with patch.object(B043Bridge,'preflight',return_value=False):r=run_ready_case(case,self.profile,proposals)
        self.assertEqual([c['name'] for c in r['public']['tool_calls']],[key[0],key[1]])
        self.assertEqual(r['diagnostics']['graph_reads'],0)
        proof=next(e['proofs']['contact_id'] for e in r['private_debug']['graph_events'] if e.get('proofs',{}).get('contact_id'))
        self.assertEqual(proof['goal_id'],'g1')
    def test_explicit_identifier_avoids_preflight(self):
        case=ready_case(('contacts.search','contacts.get','contact_id'))
        case['request']='Xem contact fx_contact_id.'
        case['goal']['graph_context']={'request_span':{'start':0,'end':len(case['request'])},
            'subjects':{'contact_id':{'id':span(case['request'],'fx_contact_id')}}}
        r=run_ready_case(case,self.profile)
        self.assertEqual([c['name'] for c in r['public']['tool_calls']],['contacts.get'])
        self.assertEqual(r['diagnostics']['graph_reads'],0)
    def test_identical_rows_deduplicate_but_conflicts_abstain(self):
        case=ready_case(('tasks.list','tasks.complete','task_id'))
        row=case['responses']['tasks.list']['tasks'][0]
        case['responses']['tasks.list']['tasks']=[row,copy.deepcopy(row)]
        self.assertTrue(any(c['name']=='tasks.complete' for c in run_ready_case(case,self.profile)['public']['tool_calls']))
        case['responses']['tasks.list']['tasks'][1]['priority']='high'
        self.assertFalse(any(c['name']=='tasks.complete' for c in run_ready_case(case,self.profile)['public']['tool_calls']))
    def test_same_name_different_ids_never_writes(self):
        case=ready_case(('tasks.list','tasks.complete','task_id'))
        row=copy.deepcopy(case['responses']['tasks.list']['tasks'][0]);row['id']='another'
        case['responses']['tasks.list']['tasks'].append(row)
        self.assertEqual([c['name'] for c in run_ready_case(case,self.profile)['public']['tool_calls']],['tasks.list'])
    def test_missing_subject_restores_legacy_preflight(self):
        from lifemate_graph.b043 import B043Harness
        h=B043Harness(ROOT/'inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb',self.profile,
                      'GRAPH_ASSISTED_SAFE_READ','SYNTHETIC_ONLY')
        b=B043Bridge(h.module,self.profile,'GRAPH_ASSISTED_SAFE_READ','SYNTHETIC_ONLY')
        request='Tra liên hệ và nội dung khác.'
        g={'goal_id':'g1','completion_tool':'contacts.get','status':'pending','conditional':False,'description':request,
           'graph_context':{'bindings':{'contacts.get':{}},'subjects':{'unrelated':{'name':span(request,'nội dung')}}}}
        b.attach({'audio_text':request},list(self.profile['tools']),h.module.GoalTracker([g]))
        self.assertFalse(b.owns_prerequisites)
    def test_wrong_identifier_case_is_not_repaired_as_equal(self):
        case=ready_case(('tasks.list','tasks.complete','task_id'))
        r=run_ready_case(case,self.profile,[{'action':'call','name':'tasks.complete','arguments':{'task_id':'FX_TASK_ID'}},{'action':'stop'}])
        self.assertFalse(any(c['name']=='tasks.complete' for c in r['public']['tool_calls']))
    def test_human_name_nfc_case_and_spaces_match_without_fuzzy_alias(self):
        import unicodedata
        case=ready_case(('food_delivery.list_restaurants','food_delivery.get_menu','restaurant_id'))
        case['responses']['food_delivery.list_restaurants']['restaurants'][0]['name']=unicodedata.normalize('NFD','QUÁN  ĂN MẪU')
        self.assertTrue(any(c['name']=='food_delivery.get_menu' for c in run_ready_case(case,self.profile)['public']['tool_calls']))
    def test_write_data_cannot_come_from_another_clause(self):
        case=ready_case(('notes.list','notes.append','note_id'))
        r=run_ready_case(case,self.profile,[{'action':'call','name':'notes.append','arguments':{'content':'invented'}},{'action':'stop'}])
        self.assertFalse(any(c['name']=='notes.append' for c in r['public']['tool_calls']))
    def test_failed_or_empty_producer_does_not_repeat(self):
        for response in [{'ok':False},{'ok':True,'tasks':[]}]:
            case=ready_case(('tasks.list','tasks.complete','task_id'));case['responses']['tasks.list']=response
            r=run_ready_case(case,self.profile)
            self.assertEqual([c['name'] for c in r['public']['tool_calls']],['tasks.list'])
    def test_matching_read_reused_despite_newer_unrelated_query(self):
        c=compiled();r=BackwardDependencyResolver(c,'SYNTHETIC_ONLY');l=EvidenceLedger();g=goal()
        l.observe('demo.people',{'query':'Mai'},{'ok':True,'people':[{'id':'right','name':'Mai'}]},g.goal_id)
        l.observe('demo.people',{'query':'Other'},{'ok':True,'people':[{'id':'wrong','name':'Other'}]},g.goal_id)
        result=r.resolve('Nhắn Mai hello',[g],'demo.send',{'content':'hello'},c['tools'],l,goal=g)
        self.assertEqual(result.status,Status.SATISFIED);self.assertEqual(result.arguments['contact_id'],'right')
    def test_latest_failure_supersedes_previous_success(self):
        c=compiled();r=BackwardDependencyResolver(c,'SYNTHETIC_ONLY');l=EvidenceLedger();g=goal()
        l.observe('demo.people',{'query':'Mai'},{'ok':True,'people':[{'id':'right','name':'Mai'}]},g.goal_id)
        l.observe('demo.people',{'query':'Mai'},{'ok':False},g.goal_id)
        result=r.resolve('Nhắn Mai hello',[g],'demo.send',{'content':'hello'},c['tools'],l,goal=g)
        self.assertEqual(result.status,Status.UNRESOLVED)
    def test_unscoped_observation_requires_unique_binding_and_keeps_other_goals_isolated(self):
        c=compiled();r=BackwardDependencyResolver(c,'SYNTHETIC_ONLY');l=EvidenceLedger();g=goal()
        l.observe('demo.people',{'query':'Mai'},{'ok':True,'people':[{'id':'right','name':'Mai'}]},None)
        result=r.resolve('Nhắn Mai hello',[g],'demo.send',{'content':'hello'},c['tools'],l,goal=g)
        self.assertEqual(result.status,Status.SATISFIED)
        self.assertEqual(result.proofs['contact_id']['goal_id'],'g1')
        self.assertIsNone(result.proofs['contact_id']['observed_goal_id'])
        l.observations[0].goal_id='other_goal'
        self.assertEqual(r.resolve('Nhắn Mai hello',[g],'demo.send',{'content':'hello'},c['tools'],l,goal=g).status,Status.NEED_READ)
    def test_compile_only_uses_frozen_graph_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);approval=tmp/'test_grants.json';write_json(approval,self.overlay)
            with patch('lifemate_graph.builder.build',side_effect=AssertionError('Must not rebuild')):
                m=compile_existing(ROOT/'artifacts/full_dependency_graph_v3.json',ROOT/'artifacts/graph_manifest.json',
                    ROOT/'inputs/train_tools_catalog.json',approval,tmp/'synthetic_compiled')
            self.assertFalse(m['graph_rebuilt'])
            c=load_compiled(tmp/'synthetic_compiled/compiled_graph_v3.json',tmp/'synthetic_compiled/graph_manifest.json',
                            ROOT/'inputs/train_tools_catalog.json',scope='SYNTHETIC_ONLY')
            self.assertEqual(len(c['edges']),19)
            with self.assertRaises(ValueError):load_compiled(tmp/'synthetic_compiled/compiled_graph_v3.json')
    def test_pending_proposal_cannot_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):compile_existing(ROOT/'artifacts/full_dependency_graph_v3.json',
                ROOT/'artifacts/graph_manifest.json',ROOT/'inputs/train_tools_catalog.json',ROOT/'operator_approvals.proposed.json',Path(tmp)/'out')


if __name__=='__main__':unittest.main()
