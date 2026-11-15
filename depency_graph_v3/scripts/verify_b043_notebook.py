"""Execute every B043 cell in a fresh CPU process, then verify its output ZIP."""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb'
nb=json.loads(path.read_text(encoding='utf-8'))
os.environ['B043_GPU']='0'
os.environ['B043_WORKDIR']=str(ROOT/'reports'/'notebook_verification')
ns={'__name__':'__main__'}
executed=[]
for i,cell in enumerate(nb['cells']):
    if cell['cell_type']!='code':continue
    source=''.join(cell['source']);ast.parse(source)
    exec(compile(source,str(path)+f':cell{i}','exec'),ns)
    executed.append(i)
zip_path=ns['ZIP_PATH']
with zipfile.ZipFile(zip_path) as z:
    assert z.testzip() is None
    assert 'cpu/cpu_test_results.json' in z.namelist()
    assert 'gpu_status.json' in z.namelist()
    cpu=json.loads(z.read('cpu/cpu_test_results.json'))
    gpu=json.loads(z.read('gpu_status.json'))
    assert cpu['status']=='PASS' and gpu['gpu_executed'] is False
report={'status':'PASS','executed_code_cells':executed,'cpu_tests':cpu['tests_run'],'skipped':cpu['skipped'],
        'gpu_tested':False,'zip_path':str(zip_path)}
(ROOT/'reports'/'notebook_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
