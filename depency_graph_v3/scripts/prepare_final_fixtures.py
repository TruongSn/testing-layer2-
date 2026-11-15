"""Offline recorded-observation fixtures for each approved data-binding contract.

Does not grant approval and does not feed labels into the deployed runtime.
"""
from pathlib import Path
import json,copy,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lifemate_graph.common import read_json,write_json,path_entries,get_path,set_path,fields,field_equal

def main():
    g=read_json(ROOT/'artifacts_final/compiled_graph_v3.json');observations={}
    for line in (ROOT/'inputs/train.jsonl').open(encoding='utf-8'):
        sample=json.loads(line)
        for turn in [*sample.get('history',[]),sample['label']]:
            for call,result in zip(turn.get('tool_calls',[]),turn.get('tool_results',[])):
                if isinstance(result,dict) and result.get('ok') is True:
                    observations.setdefault(call['name'],[]).append((call['arguments'],result))
    fixtures=[]
    for id_,e in g['edges'].items():
        if e.get('context_requirement'):continue
        required=e['subject_binding_rule']['required_selector_keys'];found=None
        for pargs,result in observations.get(e['producer_tool'],[]):
            for value,record in path_entries(result,e['source_output_path']):
                if any(get_path(record,k) is None or isinstance(get_path(record,k),(list,dict)) for k in required):continue
                if any(get_path(record,k) not in vs for k,vs in e.get('record_filters',{}).items()):continue
                selectors={k:get_path(record,k) for k in required}
                matches=[v for v,r in path_entries(result,e['source_output_path']) if all(field_equal(k,get_path(r,k),s) for k,s in selectors.items())]
                if len(matches)!=1:continue
                found=(pargs,result,value,selectors);break
            if found:break
        if found is None:raise ValueError('No positive observed contract fixture for '+id_)
        pargs,result,value,selectors=found
        pargs=copy.deepcopy(pargs)
        for arg,mapping in e['producer_argument_bindings'].items():pargs[arg]=selectors[mapping['subject_key']]
        path=e['target_argument_path'].replace('[]','[0]');tool=e['consumer_tool'];schema=g['tools'][tool]['parameters']
        args={}
        def example(s):
            if 'enum' in s:return s['enum'][0]
            if s.get('format')=='date-time':return '2026-12-14T12:00:00+07:00'
            if s.get('format')=='date':return '2026-12-14'
            return {'string':'fixture value','integer':1,'number':1,'boolean':True,'array':[],'object':{}}[s['type']]
        for key in schema.get('required',[]):
            spec=schema['properties'][key]
            if key==e['target_argument_path']:continue
            args[key]=example(spec)
        if '[' in path:
            root=path.split('[')[0];args[root]=[{}] if '.' in path else []
            if '.' in path:
                for f in fields(schema):
                    if f['locally_required'] and f['path'].startswith(root+'[]') and f['path']!=e['target_argument_path']:
                        set_path(args,f['path'].replace('[]','[0]'),example(f['schema']))
        if tool=='music.play':args={}  # choose exactly the tested x-one-of branch
        if tool=='contacts.update':args['phone']='0912345678'
        if tool=='food_delivery.place_order' and e['producer_tool']=='food_delivery.get_menu':args['restaurant_id']=pargs['restaurant_id']
        fixtures.append({'edge_id':id_,'target_path':path,'producer_arguments':pargs,'observation':result,
            'selectors':selectors,'consumer_arguments':args,'expected_value':value})
    write_json(ROOT/'tests/data/approved_contracts.json',fixtures)
    print('Recorded contract fixtures:',len(fixtures))
if __name__=='__main__':main()
