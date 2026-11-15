"""Write machine-readable verification evidence, including test case names and failures."""
from __future__ import annotations
import contextlib
import io
import platform
import sys
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lifemate_graph.common import write_json, sha256
from lifemate_graph.demo import run_demo


class RecordedResult(unittest.TextTestResult):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.passed=[]
    def addSuccess(self,test):super().addSuccess(test);self.passed.append(test.id())


def run(root=ROOT, output=None):
    root=Path(root);output=Path(output or root/'reports');output.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_*.py',top_level_dir=str(root))
    started=time.perf_counter(); stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=RecordedResult).run(suite)
    (output/'cpu_tests.txt').write_text(stream.getvalue(),encoding='utf-8')
    report={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests_run':result.testsRun,
            'passed':result.passed,'failures':[(t.id(),s) for t,s in result.failures],
            'errors':[(t.id(),s) for t,s in result.errors],'skipped':[(t.id(),s) for t,s in result.skipped],
            'seconds':time.perf_counter()-started,'python':platform.python_version(),'platform':platform.platform(),
            'gpu_tested':False,'real_toolenv_tested':False,
            'source_sha256':{str(p.relative_to(root)).replace('\\','/'):sha256(p) for p in
                sorted((root/'lifemate_graph').glob('*.py'))+sorted((root/'tests').glob('*.py'))}}
    write_json(output/'cpu_test_results.json',report)
    if result.wasSuccessful():run_demo(root,output/'closed_loop_demo.json')
    print(stream.getvalue());print('CPU tests:',report['status'],report['tests_run'])
    return report


if __name__=='__main__':sys.exit(0 if run()['status']=='PASS' else 1)
