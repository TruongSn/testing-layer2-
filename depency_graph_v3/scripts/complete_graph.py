"""One-pass review of every existing v3 candidate, not a discovery rebuild.

Reads JSONL offline to recheck paths/types/record shapes, then emits a separate
completed v3 graph and unified safe-READ overlay. Original graphs are immutable.
"""
from __future__ import annotations
import copy
from collections import Counter
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lifemate_graph.common import read_json,write_json,write_csv,sha256,object_sha,fields
from lifemate_graph.builder import input_inventory,scan_train
from lifemate_graph.validation import validate_edge,graph_cycles

# Required identity constraints, not action templates. No sample IDs or gold values.
IDENTITY={
 'alarm.delete':['label','time'], 'alarm.update':['label','time'],
 'appliances.stop':['name','room'], 'bike_rental.reserve':['name','station'],
 'calendar.cancel_event':['title','start'], 'calendar.update_event':['title','start'],
 'chat_ott.call':['name'], 'chat_ott.read_chat':['name'], 'chat_ott.send_location':['name'], 'chat_ott.send_message':['name'],
 'contacts.get':['name'], 'contacts.update':['name'], 'phone.call':['name'], 'sms.read_recent':['name'], 'sms.send':['name'],
 'email.archive':['subject','from','time'], 'email.read':['subject'], 'email.reply':['subject','from'],
 'email.send':['name'], 'email.create_draft':['name'], 'video_meeting.create':['name'],
 'flights.cancel':['flight.origin','flight.destination','flight.date','flight.depart'], 'flights.check_in':['flight.origin','flight.destination','flight.date','flight.depart'],
 'flights.book':['flight_no','date'], 'food_delivery.get_menu':['name'], 'food_delivery.place_order':['name'],
 'home_automation.delete_rule':['title','time'], 'learning.enroll':['name'], 'learning.log_progress':['name'],
 'learning.schedule_lesson':['name'], 'library.reserve':['title'], 'maps.save_place':['name'],
 'music.play':None, 'music.add_to_playlist':None, 'news.save':['title'], 'notes.append':['title'],
 'podcast_radio.play':['title'], 'podcast_radio.queue':['title'], 'podcast_radio.subscribe':['name'],
 'reminders.complete':['content'], 'reminders.delete':['content','time'],
 'ride_hailing.cancel':['pickup','dropoff','schedule_time'], 'shopping_list.check':['name'], 'shopping_list.remove':['name'],
 'smart_home.climate_set':['name','room'], 'smart_home.fan_set':['name','room'],
 'smart_home.light_set':['name','room'], 'smart_home.switch_set':['name','room'], 'smart_home.scene_run':['name'],
 'tasks.complete':['title'], 'tv.record':['title','channel','start'], 'video_meeting.join':['title','start'],
 'package_tracking.reschedule_delivery':['name','carrier'],'ride_hailing.book':['title','start'],
}
FILTERS={'smart_home.climate_set':{'type':['climate']},'smart_home.fan_set':{'type':['fan']},
         'smart_home.light_set':{'type':['light']},'smart_home.switch_set':{'type':['plug','curtain','tv']},
         'bike_rental.reserve':{'available':[True]}}
INPUTS={'learning.log_progress':['minutes'],'notes.append':['content'],'maps.save_place':['label'],
        'chat_ott.send_message':['content'],'sms.send':['content'],'email.reply':['body'],
        'email.send':['subject','body'],'email.create_draft':['subject','body'],
        'package_tracking.reschedule_delivery':['time']}
CONTEXT={
 ('chat_ott.read_chat','music.add_to_playlist'):('chat_referenced_track','messages[].content','track_id',
     'Only when user explicitly refers to a track sent by a named chat sender; validate sender, requested occurrence and observed title span, never copy message text to an ID.'),
 ('chat_ott.list_chats','food_delivery.place_order'):('chat_group_quantity','chats[].members[]','items[].quantity',
     'Only an explicit named-group member-count quantity request for one identified dish; count actual unique members, never infer a default quantity or subtract self blindly.'),
}

def complete(root=ROOT):
    root=Path(root);out=root/'artifacts_completed';out.mkdir(exist_ok=True)
    inventory=input_inventory(root)
    graph=read_json(root/'artifacts/full_dependency_graph_v3.json')
    before=object_sha(graph)
    assert graph['catalog_digest']==sha256(root/'inputs/train_tools_catalog.json')
    assert graph['train_digest']==sha256(root/'inputs/train.jsonl')
    obs,keys,flows,order,stats,errors=scan_train(root/'inputs/train.jsonl',graph['tools'])
    graph['completion_provenance']={'parent_graph_content_sha256':before,
        'parent_graph_file_sha256':sha256(root/'artifacts/full_dependency_graph_v3.json'),
        'method':'Review all existing candidates with catalog, Spec and rechecked offline observations; no blanket union or new cooccurrence discovery',
        'spec_sha256':sha256(root/'inputs/Spec.md'),'jsonl_schema_audit':{'stats':stats,'errors':errors}}
    decisions=[];rows=[]
    for edge in graph['dependencies']:
        original=copy.deepcopy(edge);p,c=edge['producer_tool'],edge['consumer_tool'];target=edge['target_argument_path']
        fs={f['path']:f for f in fields(graph['tools'][c]['parameters'])}
        source=edge.get('source_output_path')
        # Resolve two previously ambiguous source paths using the actual contract,
        # not occurrence order or value equality: named producer + field meaning.
        proven_paths={
            ('package_tracking.track','package_tracking.reschedule_delivery','tracking_id'):
                ('shipments[].tracking_id','Consumer explicitly names package_tracking.track and the tracking identifier, not the internal shipment id.'),
            ('calendar.list_events','ride_hailing.book','dropoff'):
                ('events[].location','Consumer dropoff description explicitly permits a calendar-derived location when the user refers to an event.')}
        selected=proven_paths.get((p,c,target))
        if selected:
            source,note=selected
            assert obs[p].get(source) and source in {v['path'] for v in edge['candidate_output_paths']}
            edge['source_output_path']=source;edge['contract_tier']='EXPLICIT_CONTRACT';edge['evidence_tier']='EXPLICIT_CONTRACT'
            edge['subject_binding_rule']['allowed_selector_keys']=sorted(keys[(p,source)])
            edge['evidence_sources'].append({'file':'inputs/train_tools_catalog.json','sha256':graph['catalog_digest'],
                'record':{'consumer_schema':fs[target]['schema'],'producer_contract':graph['tools'][p]['description'],
                          'source_field_disambiguation':note,'observed_path':source}})
        technical,reasons=validate_edge(edge,graph['tools'])
        if original['validation_status']=='REJECTED':technical,reasons='REJECTED',original['validation_reasons']
        # Contract proves array element semantics; runtime now requires a concrete
        # row selector and recursively validates the producer's parent arguments.
        if target in {'to[]','attendees[]','items[].item_id'} and edge['contract_tier']=='EXPLICIT_CONTRACT' and source and obs[p].get(source) and technical!='REJECTED':
            technical='VERIFIED';reasons=['explicit_catalog_or_Spec_element_contract_and_observed_typed_path',
                'runtime_requires_concrete_row_subject_and_recursive_parent_argument_proof']
            edge['evidence_sources'].append({'file':'inputs/Spec.md','sha256':sha256(root/'inputs/Spec.md'),
                'record':{'section':'6.2 nested ID and scoped snapshots','claim':'Menu item IDs must come from the menu of the selected restaurant; array slots require per-row identity'}})
        context=CONTEXT.get((p,c))
        if context and not target:
            requirement,source,target,note=context
            assert obs[p].get(source), 'Context source must have actual observed output'
            edge.update(source_output_path=source,target_argument_path=target,dependency_type='CONTEXT_PREREQUISITE',
                        context_requirement=requirement,contract_tier='EXPLICIT_TASK_CONDITION',
                        evidence_tier='USER_REFERENCE_CONTRACT_AND_OBSERVED_PATH')
            edge['subject_binding_rule']['allowed_selector_keys']=['name']
            edge['subject_binding_rule']['strategy']='explicit_reference_with_goal_epoch_and_unique_source_record'
            edge['selection_strategy']='validate_reference_then_derive_bound_data_never_copy_context_to_identifier'
            edge['candidate_output_paths']=[{'path':source,'observed_types':dict(obs[p][source]),'record_keys':sorted(keys[(p,source)])}]
            edge['required_when']={'slot_unresolved_or_ungrounded':target,'explicit_user_value_allowed':False}
            edge['notes'].append(note)
            edge['evidence_sources'].append({'file':'current_user_request','record':{
                'claim':'User explicitly requests data from another named source; its current observation must precede binding',
                'runtime_contract':note}})
            edge['evidence_sources'].append({'file':'inputs/train.jsonl','sha256':graph['train_digest'],
                'record':{'kind':'OFFLINE_OUTPUT_SHAPE_ONLY','path':source,'observed_types':dict(obs[p][source])}})
            technical='VERIFIED';reasons=['conditional_reference_contract_not_cooccurrence',
                'observed_source_path_and_dedicated_executable_reference_validator','no_direct_value_copy_to_target']
        edge['validation_status']=technical;edge['validation_reasons']=reasons
        edge['operator_decision']='PENDING';edge['runtime_enabled']=False
        source=edge.get('source_output_path');target=edge['target_argument_path']
        nested=target and '[]' in target
        shape=obs[p].get(source,{}) if source else {}
        reason='; '.join(reasons);status='FALLBACK';decision=None
        if technical=='VERIFIED':
            required=IDENTITY.get(c)
            if edge.get('context_requirement'):required=['name']
            elif c in {'music.play','music.add_to_playlist'}:required=['title' if target=='track_id' else 'name']
            elif c=='food_delivery.place_order' and target=='items[].item_id':required=['name']
            if c in {'flights.cancel','flights.check_in'}:
                for k in required:
                    assert obs[p].get('bookings[].'+k), 'Nested identity must be observed'
                    if k not in edge['subject_binding_rule']['allowed_selector_keys']:edge['subject_binding_rule']['allowed_selector_keys'].append(k)
            target_info=fs.get(target,{})
            allowed=edge['subject_binding_rule']['allowed_selector_keys']
            if required and set(required)<=set(allowed) and target_info.get('type') not in {'array','object'}:
                if p=='news.headlines':reason='Competing broader producer; narrow news.search is active, headlines evidence remains Policy-only'
                else:
                    selector_keys=list(dict.fromkeys(required+[k for k in ['id','name','title','artist','author','room','station','time','start','end','channel','from','subject','type','relation','address'] if k in allowed]))
                    bind={'query':{'subject_key':required[0]}} if 'query' in graph['tools'][p]['parameters'].get('properties',{}) else {}
                    inputs=[k for k in INPUTS.get(c,[]) if k in fs]
                    decision={'edge_id':edge['edge_id'],'decision':'APPROVED','source_output_path':source,
                        'selector_keys':selector_keys,'required_selector_keys':required,
                        'duplicate_subject_policy':'ABSTAIN','producer_argument_bindings':bind,
                        'read_cost':'LOW' if bind else 'NORMAL', 'consumer_input_requirements':inputs,
                        'reason':'Safe prerequisite READ only under explicit goal identity '+','.join(required)+
                            '; current write epoch; duplicate/ambiguous records abstain; producer args require provenance; Policy owns all WRITE decisions and B042 guards/ToolEnv.validate remain mandatory.'}
                    if c in FILTERS:decision['record_filters']=FILTERS[c]
                    reason=decision['reason'];status='ACTIVE_SAFE_READ'
            elif target_info.get('type') in {'array','object'}:reason='Root collection alias; use reviewed per-index element dependency; never broadcast a scalar'
            else:
                reason={
                    'appliances.start_cycle':'Needs same-appliance cycle membership predicate; current resolver must abstain',
                    'household_chores.assign':'Needs assignee membership validation; no automatic assignment inference',
                    'food_delivery.cancel_order':'Needs restaurant/order join and occurrence/state binding',
                }.get(c,'No safe automatic scope established for sensitive financial/security/medical data or unsupported action context; keep Policy/B042 fallback')
        if decision:decisions.append(decision)
        edge['completion_review']={'prior_validation_status':original['validation_status'],'runtime_disposition':status,'reason':reason}
        rows.append({'edge_id':edge['edge_id'],'producer_tool':p,'consumer_tool':c,
            'source_output_path':source,'target_argument_path':target,'previous_status':original['validation_status'],
            'validation_status':technical,'runtime_disposition':status,'reason':reason,
            'observed_types':dict(shape),'target_type':fs.get(target,{}).get('type'),
            'required':fs.get(target,{}).get('required'),'alternative_branches':edge['alternative_branches'],
            'required_identity':decision.get('required_selector_keys',[]) if decision else [],
            'producer_required_arguments':graph['tools'][p]['parameters'].get('required',[]),
            'nested_parent_policy':'explicit_index_and_recursive_parent_proof' if nested else 'scalar',
            'freshness':'current_goal_and_write_epoch','evidence_sha256':object_sha(edge['evidence_sources'])})
    assert len(rows)==190 and len(set(r['edge_id'] for r in rows))==190
    # Re-emit dependency relations from reviewed records: preserve structural
    # catalog relations but never leave old source paths/statuses in the full graph.
    dependency_ids={e['edge_id'] for e in graph['dependencies']}
    graph['relations']=[r for r in graph['relations'] if r.get('edge_id') not in dependency_ids]
    nodes={n['id'] for n in graph['nodes']}
    for e in graph['dependencies']:
        common={'edge_id':e['edge_id'],'validation_status':e['validation_status'],
                'runtime_enabled':False,'required_when':e['required_when']}
        source='output:'+e['producer_tool']+':'+str(e['source_output_path'])
        target='arg:'+e['consumer_tool']+':'+e['target_argument_path']
        if e.get('context_requirement'):
            condition='precondition:'+e['edge_id']
            graph['nodes'].append({'id':condition,'type':'PRECONDITION','rule':e['context_requirement'],
                'notes':'Validate referenced observation/identity and derive data; never copy message/member text into target identifiers or quantities'})
            nodes.add(condition)
            assert source in nodes and target in nodes
            graph['relations'].extend([{**common,'from':source,'to':condition,'type':'CONDITIONAL_DEPENDENCY'},
                                      {**common,'from':condition,'to':target,'type':'CONDITIONAL_DEPENDENCY'}])
        elif source in nodes and target in nodes:
            graph['relations'].append({**common,'from':source,'to':target,
                'type':'CONDITIONAL_DEPENDENCY' if e['dependency_type']=='RESOURCE_STATE_PRECONDITION' else 'REQUIRES_DATA_FROM'})
        else:graph['relations'].append({**common,'from':'tool:'+e['producer_tool'],'to':'tool:'+e['consumer_tool'],'type':'CONDITIONAL_DEPENDENCY'})
    assert all(r['from'] in nodes and r['to'] in nodes for r in graph['relations'])
    overlay={'scope':'PRODUCTION','graph_content_sha256':object_sha(graph),
        'reviewer':'User: FINAL GRAPH COMPLETION prompt authorizes one-pass activation of proven safe prerequisite READ dependencies; technical gates enforced by implementation',
        'authorization_scope':'Prerequisite READ only; no Graph-proposed, decided or executed WRITE; no safety bypass; sensitive unsupported routes remain disabled',
        'supersedes_for_this_completed_graph':'19-edge pilot overlay retained unchanged as rollback', 'decisions':decisions}
    write_json(out/'full_dependency_graph_v3.json',graph)
    write_json(out/'operator_approvals.json',overlay)
    write_csv(out/'candidate_completion.csv',rows,list(rows[0]))
    report={'scope':'OFFLINE_CONTRACT_REVIEW_NOT_GPU_OR_OFFICIAL_TOOLENV_VALIDATION','candidates_reviewed':len(rows),
        'status_counts':dict(Counter(r['validation_status'] for r in rows)),
        'runtime_disposition_counts':dict(Counter(r['runtime_disposition'] for r in rows)),
        'audited_tools':len(graph['tools']),'runtime_proposed_safe_read_edges':len(decisions),
        'disabled_edge_reasons':dict(Counter(r['reason'] for r in rows if r['runtime_disposition']=='FALLBACK')),
        'candidate_cycles':graph_cycles(graph['dependencies']),'source_integrity':inventory,
        'original_graph_unchanged':sha256(root/'artifacts/full_dependency_graph_v3.json')==graph['completion_provenance']['parent_graph_file_sha256'],
        'runtime_gates':'Separate CPU results: reports/cpu_test_results.json and reports/final/cpu_mechanism_report.json',
        'known_data_schema_errors':errors}
    write_json(out/'graph_validation_report.json',report)
    manifest={'version':graph['version'],'graph_content_sha256':object_sha(graph),'catalog_digest':graph['catalog_digest'],
        'artifacts_sha256':{f.name:sha256(f) for f in sorted(out.iterdir()) if f.is_file() and f.name!='graph_manifest.json'}}
    write_json(out/'graph_manifest.json',manifest)
    print(report['status_counts'],'safe READ decisions',len(decisions),flush=True)
    return report

if __name__=='__main__':complete()
