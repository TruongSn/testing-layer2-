"""Package only workspace-owned final artifacts; verify every archived byte."""
from __future__ import annotations
import ast,base64,io,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lifemate_graph.common import read_json,write_json,sha256,object_sha
from lifemate_graph.compiler import load_compiled

def main():
    graph=load_compiled(ROOT/'artifacts_final/compiled_graph_v3.json',ROOT/'artifacts_final/graph_manifest.json',ROOT/'inputs/train_tools_catalog.json')
    cpu=read_json(ROOT/'reports/cpu_test_results.json');nbcheck=read_json(ROOT/'reports/notebook_verification.json')
    assert cpu['status']=='PASS' and nbcheck['status']=='PASS' and not nbcheck['gpu_tested']
    payload=read_json(ROOT/'reports/final_payload_manifest.json')
    for relative,digest in payload['embedded_files'].items():assert sha256(ROOT/relative)==digest,relative
    notebook=ROOT/'LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb'
    assert sha256(notebook)==payload['notebook_sha256']
    # Check the actual encoded notebook against current files, independently of its Run All gate.
    assignments={}
    for cell in read_json(notebook)['cells']:
        if cell['cell_type']!='code':continue
        for node in ast.parse(''.join(cell['source'])).body:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in {'PAYLOAD','PAYLOAD_SHA256','EMBEDDED_SHA256'}:
                assignments[node.targets[0].id]=ast.literal_eval(node.value)
    import hashlib
    raw=base64.b64decode(assignments['PAYLOAD']);assert hashlib.sha256(raw).hexdigest()==assignments['PAYLOAD_SHA256']
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert set(z.namelist())==set(payload['embedded_files'])
        assert z.testzip() is None
        for name in z.namelist():assert hashlib.sha256(z.read(name)).hexdigest()==sha256(ROOT/name)
    paths={ROOT/name for name in payload['embedded_files']}
    paths.update((ROOT/'scripts').glob('*.py'))
    paths.update([notebook,ROOT/'README.md',ROOT/'manifest.json',ROOT/'README_RECOVERED.md',
        ROOT/'reports/cpu_test_results.json',ROOT/'reports/cpu_tests.txt',ROOT/'reports/notebook_verification.json',ROOT/'reports/final_payload_manifest.json'])
    paths.update(p for p in (ROOT/'reports/final').rglob('*') if p.is_file())
    # Include byte-preserved recovered documents for reproducibility, never raw train/environment.
    for entry in read_json(ROOT/'manifest.json'):
        path=ROOT/Path(*Path(entry['path']).parts[1:]);assert sha256(path)==entry['sha256'];paths.add(path)
    assert all(ROOT in p.resolve().parents and 'train_environment' not in p.parts and p.name!='train.jsonl' for p in paths)
    paths=sorted(paths)
    delivery={'scope':'B043_FINAL_LOCAL_VERIFIED_GPU_NOT_RUN','runtime_edges':len(graph['edges']),
        'compiled_payload_sha256':graph['payload_sha256'],'graph_content_sha256':graph['graph_content_sha256'],
        'catalog_sha256':graph['catalog_digest'],'notebook_sha256':sha256(notebook),
        'payload_sha256':payload['payload_sha256'],'all_embedded_files_verified':len(payload['embedded_files']),
        'cpu_tests':{'run':cpu['tests_run'],'pass':len(cpu['passed']),'fail':len(cpu['failures'])+len(cpu['errors']),'skip':len(cpu['skipped'])},
        'notebook_run_all_cpu_status':nbcheck['status'],'gpu_executed':False,
        'files_sha256':{p.relative_to(ROOT).as_posix():sha256(p) for p in paths}}
    write_json(ROOT/'B043_FINAL_DELIVERY_MANIFEST.json',delivery)
    paths.append(ROOT/'B043_FINAL_DELIVERY_MANIFEST.json')
    output=ROOT/'deliverables';output.mkdir(exist_ok=True)
    archives={}
    for name,members in [('B043_Final_Graph_Kaggle.zip',paths),('B043_Runtime_Graph_Final.zip',
        [p for p in paths if p.parent in {ROOT/'artifacts_final',ROOT/'artifacts_completed'} or p.name in {'B043_KAGGLE_FINAL_GUIDE.md','B043_FINAL_REPORT.md'}])]:
        target=output/name
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for path in members:z.write(path,path.relative_to(ROOT).as_posix())
        with zipfile.ZipFile(target) as z:
            assert z.testzip() is None
            for path in members:assert z.read(path.relative_to(ROOT).as_posix())==path.read_bytes()
        archives[name]={'sha256':sha256(target),'bytes':target.stat().st_size,'files':len(members)}
    write_json(output/'B043_Final_Graph_Kaggle.sha256.json',{'archives':archives,'notebook_sha256':sha256(notebook),
        'compiled_payload_sha256':graph['payload_sha256'],'embedded_payload_sha256':payload['payload_sha256']})
    print(archives)
    return delivery
if __name__=='__main__':main()
