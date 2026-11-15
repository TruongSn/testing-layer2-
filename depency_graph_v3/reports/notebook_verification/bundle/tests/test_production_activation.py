"""Gates against the actual user-approved compiled PRODUCTION graph.

Only observations/Policy/ToolEnv are CPU fixtures; authorization is never replaced.
"""
from __future__ import annotations
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from lifemate_graph.b043 import B043Harness, B043Bridge, goal_contexts, load_b042
from lifemate_graph.common import read_json, read_csv, sha256, object_sha
from lifemate_graph.compiler import load_compiled
from lifemate_graph.runtime import BackwardDependencyResolver, EvidenceLedger, Status
from tests.ready_cases import ROOT, READY, ready_case, run_ready_case, span
from tests.fixtures import FakeToolEnv

REVIEWED=ROOT/'artifacts_reviewed'
NOTEBOOK=ROOT/'inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb'


@unittest.skipUnless((REVIEWED/'compiled_graph_v3.json').is_file(), 'Production activation artifact not present')
class ProductionActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=load_compiled(REVIEWED/'compiled_graph_v3.json',REVIEWED/'graph_manifest.json',ROOT/'inputs/train_tools_catalog.json')
        cls.graph=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')

    def setUp(self):
        blocker=patch('tests.ready_cases.synthetic_profile',side_effect=AssertionError('Production gates must not substitute synthetic authorization'))
        blocker.start();self.addCleanup(blocker.stop)

    def run_case(self,case,proposals=None,before_run=None):
        return run_ready_case(case,self.profile,proposals,before_run)

    def test_exactly_19_authorized_ids_and_read_only_automatic_producers(self):
        approvals=read_json(ROOT/'operator_approvals.json')
        draft=read_json(ROOT/'operator_approvals.proposed.json')
        ids={d['edge_id'] for d in draft['decisions']}
        self.assertEqual(len(ids),19)
        self.assertEqual(ids,{d['edge_id'] for d in approvals['decisions']})
        self.assertEqual(ids,set(self.profile['edges']))
        self.assertEqual(ids,{r['edge_id'] for r in read_csv(ROOT/'runtime_activation_final.csv')})
        self.assertEqual(self.profile['runtime_scope'],'PRODUCTION')
        self.assertIn('User:',approvals['reviewer'])
        for e in self.profile['edges'].values():
            self.assertEqual(e['approval']['scope'],'PRODUCTION')
            self.assertEqual(e['operator_decision'],'APPROVED')
            self.assertTrue(e['runtime_enabled'])
            self.assertEqual(self.profile['tools'][e['producer_tool']]['kind'],'read')
            self.assertEqual(e['subject_binding_rule']['duplicate_subject_policy'],'ABSTAIN')

    def test_all_19_production_contracts_execute_in_real_b043_loop(self):
        for key in READY:
            with self.subTest(edge=key):
                case=ready_case(key);result=self.run_case(case);calls=result['public']['tool_calls']
                self.assertEqual([c['name'] for c in calls],[key[0],key[1]])
                self.assertEqual(result['diagnostics']['graph_reads'],1)
                self.assertEqual(len(result['validation_audit']),2)
                for slot,id_ in case['expected'].items():self.assertEqual(calls[-1]['arguments'][slot],id_)
                events=result['private_debug']['graph_events']
                proofs=[p for e in events for p in e.get('proofs',{}).values() if p.get('source')=='tool_observation']
                self.assertTrue(proofs)
                self.assertTrue(all(p['goal_id']=='g1' and p['epoch']==0 and p['edge_id'] in self.profile['edges'] for p in proofs))
                if self.profile['tools'][key[1]]['kind']=='write':
                    steps=[s for s in result['private_debug']['steps'] if s.get('tool_name')==key[1] and 'tool_result' in s]
                    self.assertTrue(steps);self.assertTrue(all(s['origin']=='model_policy' for s in steps))

    def test_duplicate_identical_conflicting_or_different_ids_all_abstain(self):
        for variant in ['identical','different_id','conflicting_record']:
            with self.subTest(variant=variant):
                case=ready_case(('tasks.list','tasks.complete','task_id'))
                row=copy.deepcopy(case['responses']['tasks.list']['tasks'][0])
                if variant=='different_id':row['id']='another_task'
                if variant=='conflicting_record':row['priority']='high'
                case['responses']['tasks.list']['tasks'].append(row)
                result=self.run_case(case)
                self.assertEqual([c['name'] for c in result['public']['tool_calls']],['tasks.list'])
                self.assertTrue(any(e['status']=='AMBIGUOUS' for e in result['private_debug']['graph_events']))

    def test_graph_never_writes_when_policy_stops(self):
        case=ready_case(('tasks.list','tasks.complete','task_id'))
        result=self.run_case(case,[{'action':'stop'}])
        self.assertEqual([c['name'] for c in result['public']['tool_calls']],['tasks.list'])
        self.assertEqual(result['diagnostics']['candidate_completed_goals'],0)

    def test_wrong_entity_and_identifier_case_block_write(self):
        for bad_id in ['another_task','FX_TASK_ID']:
            case=ready_case(('tasks.list','tasks.complete','task_id'))
            result=self.run_case(case,[{'action':'call','name':'tasks.complete','arguments':{'task_id':bad_id}},{'action':'stop'}])
            self.assertEqual([c['name'] for c in result['public']['tool_calls']],['tasks.list'])

    def test_minutes_content_label_cannot_be_invented(self):
        cases=[(('learning.list_courses','learning.log_progress','course_id'),'minutes',999),
               (('notes.list','notes.append','note_id'),'content','invented content'),
               (('maps.search_place','maps.save_place','place_id'),'label','invented label')]
        for key,arg,value in cases:
            with self.subTest(consumer=key[1]):
                case=ready_case(key);case['goal']['graph_context']['bindings']={}
                result=self.run_case(case,[{'action':'call','name':key[1],'arguments':{arg:value}},{'action':'stop'}])
                self.assertFalse(any(c['name']==key[1] for c in result['public']['tool_calls']))
                self.assertTrue(any(e['status']=='BLOCKED_UNSAFE' for e in result['private_debug']['graph_events']))

    def test_unknown_amount_argument_is_not_accepted_or_repaired(self):
        case=ready_case(('notes.list','notes.append','note_id'))
        args={**case['args'],'amount':1000000}
        result=self.run_case(case,[{'action':'call','name':'notes.append','arguments':args},{'action':'stop'}])
        self.assertFalse(any(c['name']=='notes.append' for c in result['public']['tool_calls']))

    def test_missing_subject_does_not_guess_query_or_identifier(self):
        case=ready_case(('contacts.search','contacts.get','contact_id'))
        context=case['goal']['graph_context'];context['subjects']['contacts.get:contact_id']['name']['value']='Unknown'
        self.assertEqual(self.run_case(case)['public']['tool_calls'],[])

    def test_direct_valid_id_has_no_prerequisite_read(self):
        case=ready_case(('contacts.search','contacts.get','contact_id'))
        case['request']='Xem contact fx_contact_id.'
        case['goal']['graph_context']={'request_span':{'start':0,'end':len(case['request'])},
            'subjects':{'contact_id':{'id':span(case['request'],'fx_contact_id')}}}
        result=self.run_case(case)
        self.assertEqual([c['name'] for c in result['public']['tool_calls']],['contacts.get'])
        self.assertEqual(result['diagnostics']['graph_reads'],0)

    def test_existing_policy_read_is_reused_in_execution_loop(self):
        key=('contacts.search','contacts.get','contact_id');case=ready_case(key)
        proposals=[{'action':'call','name':key[0],'arguments':{'query':'Mai'}},
                   {'action':'call','name':key[1],'arguments':{}},{'action':'stop'}]
        with patch.object(B043Bridge,'preflight',return_value=False):result=self.run_case(case,proposals)
        self.assertEqual([c['name'] for c in result['public']['tool_calls']],[key[0],key[1]])
        self.assertEqual(result['diagnostics']['graph_reads'],0)

    def test_goal_scope_and_write_epoch_prevent_incorrect_evidence_reuse(self):
        key=('contacts.search','contacts.get','contact_id');case=ready_case(key)
        g=goal_contexts([case['goal']],case['request'])[0]
        ledger=EvidenceLedger();resolver=BackwardDependencyResolver(self.profile)
        ledger.observe(key[0],{'query':'Mai'},case['responses'][key[0]],g.goal_id)
        def resolve():return resolver.resolve(case['request'],[g],key[1],{},self.profile['tools'],ledger,goal=g)
        result=resolve();self.assertEqual(result.status,Status.SATISFIED)
        self.assertEqual(result.proofs['contact_id']['goal_id'],g.goal_id)
        ledger.observations[0].goal_id='other_goal';self.assertEqual(resolve().status,Status.NEED_READ)
        ledger.observations[0].goal_id=g.goal_id
        ledger.observe('notes.append',{}, {'ok':True},'other_goal','write')
        self.assertEqual(resolve().status,Status.NEED_READ)

    def test_failed_or_empty_read_does_not_repeat(self):
        for response in [{'ok':False},{'ok':True,'tasks':[]}]:
            case=ready_case(('tasks.list','tasks.complete','task_id'));case['responses']['tasks.list']=response
            result=self.run_case(case,[{'action':'call','name':'tasks.complete','arguments':{}}]*3)
            self.assertEqual([c['name'] for c in result['public']['tool_calls']],['tasks.list'])

    def test_music_ambiguous_branch_abstains_and_valid_alternative_avoids_read(self):
        case=ready_case(('music.list_tracks','music.play','track_id'))
        case['request']='Phát Bài hát mẫu hoặc Danh sách mẫu.'
        case['goal']['graph_context']={'request_span':{'start':0,'end':len(case['request'])},'subjects':{
            'track_id':{'title':span(case['request'],'Bài hát mẫu')},
            'playlist_id':{'name':span(case['request'],'Danh sách mẫu')}}}
        self.assertEqual(self.run_case(case)['public']['tool_calls'],[])
        case['request']='Phát playlist fx_playlist_id.'
        case['goal']['graph_context']={'request_span':{'start':0,'end':len(case['request'])},
            'bindings':{'music.play':{'playlist_id':span(case['request'],'fx_playlist_id')}}}
        result=self.run_case(case)
        self.assertEqual([c['name'] for c in result['public']['tool_calls']],['music.play'])
        self.assertEqual(result['diagnostics']['graph_reads'],0)

    def test_toolenv_validation_and_installed_tool_gate_cannot_be_skipped(self):
        case=ready_case(('contacts.search','contacts.get','contact_id'))
        def reject(h,Env,item):
            parent=Env.validate
            def validate(self,name,args):
                if name=='contacts.search':
                    self.validations.append({'name':name,'errors':['rejected by ToolEnv']});return {'ok':False}
                return parent(self,name,args)
            Env.validate=validate
        result=self.run_case(case,before_run=reject)
        self.assertEqual(result['public']['tool_calls'],[])
        self.assertEqual(len(result['validation_audit']),1)
        def unavailable(h,Env,item):item['context']['installed_apps']['fixture']['tools'].remove('contacts.search')
        self.assertEqual(self.run_case(case,before_run=unavailable)['public']['tool_calls'],[])

    def test_original_grounding_and_progress_guards_run_for_policy_write(self):
        case=ready_case(('notes.list','notes.append','note_id'));seen=[]
        def instrument(h,Env,item):
            for function in ['grounding_check','additional_grounding_check']:
                original=getattr(h.module,function)
                def wrapper(name,args,*rest,_original=original,_function=function,**kw):
                    seen.append((_function,name));return _original(name,args,*rest,**kw)
                setattr(h.module,function,wrapper)
            original_inspect=h.module.B042ProgressGuard.inspect
            def inspect(self,name,args,journal):
                seen.append(('progress_guard',name));return original_inspect(self,name,args,journal)
            h.module.B042ProgressGuard.inspect=inspect
        result=self.run_case(case,before_run=instrument)
        self.assertTrue(any(c['name']=='notes.append' for c in result['public']['tool_calls']))
        for guard in ['grounding_check','additional_grounding_check','progress_guard']:
            self.assertIn((guard,'notes.append'),seen)

    def test_disabled_mode_matches_unmodified_b042(self):
        decision={'action':'call','name':'contacts.get','arguments':{'contact_id':'fx_contact_id'}}
        item={'id':'disabled_fixture','audio_text':'Xem contact fx_contact_id.','history':[],
              'context':{'installed_apps':{'contacts':{'tools':['contacts.get']}}}}
        def setup(m):
            m.make_goals=lambda *a,**k:([],{'input_tokens':0,'output_tokens':0,'model_seconds':0})
            seq=iter([decision,{'action':'stop'}])
            m.generate_once=lambda *a,**k:(json.dumps(next(seq,{'action':'stop'})),10,10,0.001)
        class Env(FakeToolEnv):pass
        Env.tools=self.profile['tools'];Env.responses={}
        baseline=load_b042(NOTEBOOK,'_b042_production_disabled_comparison');setup(baseline)
        expected=baseline.run_one_sample(item,self.profile['tools'],Env,ROOT,None,None,None)
        harness=B043Harness(NOTEBOOK,self.profile,'GRAPH_DISABLED');setup(harness.module)
        actual=harness.run(item,self.profile['tools'],Env,ROOT)
        self.assertEqual(actual['public'],expected['public'])


if __name__=='__main__':unittest.main()
