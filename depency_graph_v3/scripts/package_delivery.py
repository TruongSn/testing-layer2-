"""Create a local handoff ZIP without raw BTC train or hidden environment files."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lifemate_graph.common import sha256, write_json


def package():
    files=[]
    for name in ['lifemate_graph','tests','scripts','schemas','artifacts','runtime_review','previous_graph','reference_notebooks']:
        files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.json','.csv','.ipynb','.md'})
    files.extend(p for p in ROOT.iterdir() if p.is_file() and (p.suffix in {'.md','.toml','.txt','.json','.csv','.ipynb'} or p.name=='.gitignore'))
    files.extend(p for p in (ROOT/'inputs').iterdir() if p.is_file() and p.name!='train.jsonl')
    files.extend(ROOT/'reports'/name for name in ['cpu_test_results.json','cpu_tests.txt','closed_loop_demo.json','ready19_closed_loop.json','notebook_verification.json','final_verification.json'])
    missing=[str(p) for p in files if not p.is_file()]
    if missing:raise FileNotFoundError(missing)
    out=ROOT/'deliverables';out.mkdir(exist_ok=True)
    manifest={'files_sha256':{p.relative_to(ROOT).as_posix():sha256(p) for p in sorted(set(files))},
              'excluded':['raw inputs/train.jsonl','hidden environments','credentials','local notebook extraction directories'],
              'gpu_tested':False,'production_runtime_grants':0,'status':'AWAITING_SINGLE_USER_APPROVAL','proposed_edges':19}
    write_json(out/'delivery_manifest.json',manifest)
    path=out/'B043_FastTrack_Ready_For_Approval.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(set(files)):z.write(p,p.relative_to(ROOT).as_posix())
        z.write(out/'delivery_manifest.json','delivery_manifest.json')
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert not any(n.endswith('train.jsonl') or '_environment/' in n for n in z.namelist())
    write_json(out/'zip_sha256.json',{'file':path.name,'sha256':sha256(path),'bytes':path.stat().st_size})
    print(path, path.stat().st_size, 'bytes')
    return path


if __name__=='__main__':package()
