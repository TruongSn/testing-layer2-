"""Nineteen real catalog contracts, entirely synthetic observations/authorization.

The grants below are SYNTHETIC_ONLY fixtures, never production operator approvals.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
from lifemate_graph.common import read_json, object_sha
from lifemate_graph.compiler import compile_graph
from lifemate_graph.b043 import B043Harness
from lifemate_graph.runtime import GraphMode
from tests.fixtures import FakeToolEnv
from scripts.review_runtime_approvals import READY

ROOT=Path(__file__).resolve().parents[1]
CONSUMER_INPUTS={'learning.log_progress':['minutes'], 'maps.save_place':['label'], 'notes.append':['content']}


def proposed_decisions(graph):
    result=[]
    for e in graph['dependencies']:
        key=e['producer_tool'],e['consumer_tool'],e['target_argument_path']
        if key not in READY:continue
        selector,bindings,cost,reason=READY[key]
        d={'edge_id':e['edge_id'],'decision':'PENDING','source_output_path':e['source_output_path'],
           'selector_keys':[selector],'required_selector_keys':[selector],'read_cost':cost,
           'producer_argument_bindings':bindings,'reason':reason}
        if e['consumer_tool'] in CONSUMER_INPUTS:d['consumer_input_requirements']=CONSUMER_INPUTS[e['consumer_tool']]
        result.append(d)
    assert len(result)==19
    return sorted(result,key=lambda d:d['edge_id'])


def synthetic_profile():
    graph=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')
    decisions=proposed_decisions(graph)
    for d in decisions:d['decision']='APPROVED'
    overlay={'graph_content_sha256':object_sha(graph),'scope':'SYNTHETIC_ONLY',
             'reviewer':'Synthetic test fixture, NOT a production operator','decisions':decisions}
    return graph,compile_graph(graph,overlay)[0],overlay


def span(request,value):
    start=request.index(str(value))
    return {'value':value,'start':start,'end':start+len(str(value))}


def ready_case(key):
    p,c,slot=key
    labels={'chat_id':('name','Nhóm dự án'), 'contact_id':('name','Mai'), 'email_id':('subject','Báo cáo tuần'),
      'restaurant_id':('name','Quán ăn mẫu'), 'course_id':('name','Khóa học mẫu'), 'book_id':('title','Cuốn sách mẫu'),
      'place_id':('name','Quán ăn mẫu'), 'track_id':('title','Bài hát mẫu'), 'playlist_id':('name','Danh sách mẫu'),
      'article_id':('title','Bài báo mẫu'), 'note_id':('title','Ghi chú mẫu'), 'episode_id':('title','Tập podcast mẫu'),
      'show_id':('name','Kênh podcast mẫu'), 'reminder_id':('content','Việc cần nhớ'), 'item_id':('name','Sữa'),
      'task_id':('title','Nhiệm vụ mẫu')}
    label=labels[slot][1]
    request=f'Thực hiện {c} cho {label}.'
    args={}
    subjects={slot:labels[slot]}
    if c=='music.add_to_playlist':
        request='Thêm Bài hát mẫu vào Danh sách mẫu.'
        subjects={'track_id':labels['track_id'],'playlist_id':labels['playlist_id']}
    if c=='learning.log_progress':request+=' Ghi 15 phút.';args={'minutes':15}
    if c=='maps.save_place':request+=' Đặt nhãn Quen.';args={'label':'Quen'}
    if c=='notes.append':request+=' Thêm nội dung mới.';args={'content':'nội dung mới'}
    context={'request_span':{'start':0,'end':len(request)},
       'subjects':{f'{c}:{s}':{k:span(request,v)} for s,(k,v) in subjects.items()},
       'bindings':{c:{a:span(request,v) for a,v in args.items()}},
       'required_optional':['contact_id'] if c=='sms.read_recent' else []}
    goal={'goal_id':'g1','completion_tool':c,'description':request,'conditional':False,
          'status':'pending','observed_step':None,'graph_context':context}
    graph=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')
    responses={};expected={}
    for e in graph['dependencies']:
        if e['consumer_tool']!=c or e['target_argument_path'] not in subjects:continue
        if (e['producer_tool'],c,e['target_argument_path']) not in READY:continue
        selector,value=subjects[e['target_argument_path']]
        field=e['source_output_path'].split('[].')[0]
        id_='fx_'+e['target_argument_path']
        responses.setdefault(e['producer_tool'],{'ok':True})[field]=[{'id':id_,selector:value}]
        expected[e['target_argument_path']]=id_
    return {'key':key,'request':request,'goal':goal,'args':args,'responses':responses,'expected':expected}


def run_ready_case(case, compiled=None, proposals=None, before_run=None):
    _,profile,_=synthetic_profile() if compiled is None else (None,compiled,None)
    harness=B043Harness(ROOT/'inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb',profile,
                        GraphMode.GRAPH_ASSISTED_SAFE_READ,profile['runtime_scope'])
    m=harness.module;m.CONFIG['max_stop_reviews']=0
    goal=copy.deepcopy(case['goal'])
    m.make_goals=lambda *a,**k:([copy.deepcopy(goal)],{'input_tokens':10,'output_tokens':10,'model_seconds':0.001})
    sequence=iter(proposals or [{'action':'call','name':case['key'][1],'arguments':case['args']},{'action':'stop'}])
    m.generate_once=lambda *a,**k:(json.dumps(next(sequence,{'action':'stop'}),ensure_ascii=False),10,10,0.001)
    class Env(FakeToolEnv):pass
    Env.tools=profile['tools'];Env.responses=copy.deepcopy(case['responses'])
    item={'id':'synthetic_ready_case','audio_text':case['request'],'history':[],
          'context':{'installed_apps':{'fixture':{'tools':list(profile['tools'])}}}}
    if before_run:before_run(harness,Env,item)
    output=harness.run(item,profile['tools'],Env,ROOT)
    output['validation_audit']=Env.last.validations
    return output
