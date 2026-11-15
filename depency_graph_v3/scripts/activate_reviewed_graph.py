"""After real user approval: compile-only, verify, run closed loops, package Kaggle graph.

This script never creates operator approval. It requires a separately authorized JSON.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lifemate_graph.compiler import compile_existing,load_compiled
from lifemate_graph.common import read_json,sha256,write_json
from scripts.prepare_runtime_activation import check_closed_loops


def main():
    p=argparse.ArgumentParser(description='Activate an explicitly authorized frozen B043 runtime graph')
    p.add_argument('--approvals',type=Path,default=ROOT/'operator_approvals.json')
    p.add_argument('--out',type=Path,default=ROOT/'artifacts_reviewed')
    a=p.parse_args()
    if not a.approvals.is_file():raise FileNotFoundError('Explicit user approval must be recorded in operator_approvals.json first')
    overlay=read_json(a.approvals)
    if overlay.get('scope')!='PRODUCTION':raise ValueError('Kaggle production delivery requires real PRODUCTION approval, not synthetic grants')
    compile_existing(ROOT/'artifacts/full_dependency_graph_v3.json',ROOT/'artifacts/graph_manifest.json',
                     ROOT/'inputs/train_tools_catalog.json',a.approvals,a.out)
    profile=load_compiled(a.out/'compiled_graph_v3.json',a.out/'graph_manifest.json',ROOT/'inputs/train_tools_catalog.json')
    report=check_closed_loops(profile,a.out/'closed_loop_verification.json')
    assert report['closed_loop_cases']==len(profile['edges'])
    from scripts.run_cpu_checks import run
    cpu=run(ROOT,a.out/'cpu')
    if cpu['status']!='PASS':raise RuntimeError('CPU regressions failed; no Kaggle ZIP will be created')
    manifest=read_json(a.out/'graph_manifest.json')
    for f in sorted(a.out.rglob('*')):
        if f.is_file() and f.name!='graph_manifest.json':manifest['artifacts_sha256'][f.relative_to(a.out).as_posix()]=sha256(f)
    write_json(a.out/'graph_manifest.json',manifest)
    dest=ROOT/'deliverables/B043_Runtime_Graph_Reviewed.zip'
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
        for f in sorted(a.out.rglob('*')):
            if f.is_file():z.write(f,'artifacts_reviewed/'+f.relative_to(a.out).as_posix())
    with zipfile.ZipFile(dest) as z:assert z.testzip() is None
    write_json(ROOT/'deliverables/B043_Runtime_Graph_Reviewed.sha256.json',{'file':dest.name,'sha256':sha256(dest),
      'runtime_approved_edges':len(profile['edges']),'graph_rebuilt':False,'cpu_status':cpu['status']})
    print(dest)


if __name__=='__main__':main()
