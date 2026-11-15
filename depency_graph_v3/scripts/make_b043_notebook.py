"""Generate the final self-contained, paired B043 Kaggle notebook."""
from __future__ import annotations
import base64,hashlib,io,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def make():
    files=[]
    for directory in ['lifemate_graph','tests','schemas','artifacts','artifacts_reviewed','runtime_review','artifacts_completed','artifacts_final']:
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.json','.csv','.txt','.md'})
    files.extend(ROOT/p for p in ['scripts/__init__.py','scripts/run_cpu_checks.py','scripts/review_runtime_approvals.py',
        'scripts/prepare_runtime_activation.py','inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb',
        'inputs/train_tools_catalog.json','requirements-kaggle.txt','approvals.example.json','operator_approvals.proposed.json',
        'runtime_activation_final.csv','runtime_approval_review.csv','reports/ready19_closed_loop.json','operator_approvals.json'])
    for name in ['B043_FINAL_REPORT.md','B043_KAGGLE_FINAL_GUIDE.md']:
        if (ROOT/name).is_file():files.append(ROOT/name)
    files=sorted(set(files));checksums={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in files:
            info=zipfile.ZipInfo(p.relative_to(ROOT).as_posix(),date_time=(2026,11,15,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,p.read_bytes())
    raw=payload.getvalue();encoded=base64.b64encode(raw).decode('ascii');cells=[]
    def md(text):cells.append({'cell_type':'markdown','metadata':{},'source':text.splitlines(keepends=True)})
    def code(text):cells.append({'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':text.splitlines(keepends=True)})
    md('''# LifeMate B043 Final — Active Graph & paired GPU evaluation

Run All: verify source/graph payload → CPU safety gates and actual B043-loop graph demos → Qwen3.5-4B non-thinking → paired GRAPH_DISABLED / GRAPH_ASSISTED_SAFE_READ → checkpoints, predictions, traces, report ZIP.

Attach the authorized BTC train dataset containing `zaic_tools` and `train_environment`. Enable GPU and Internet/model cache. The notebook embeds the final v3 graph and approved READ overlay. It embeds no raw train, environment files, credentials or GPU results. Every WRITE still comes from Decision Policy and passes B042 guards and ToolEnv.validate.

Local CPU tests use scripted Policy and fixture observations. They are not Qwen/ToolEnv accuracy measurements. The final GPU run has not been performed locally. State Match requires independent authoritative reference hashes; train partial final_state is not a full-state hash. Fuzzy-call F1 below is a Spec-oriented proxy, not official competition scoring.
''')
    code('''import os
from pathlib import Path
B043_GPU = os.environ.get('B043_GPU','auto')  # 0=CPU only, 1=require CUDA, auto=use CUDA if present
B043_MODES = ['GRAPH_DISABLED','GRAPH_ASSISTED_SAFE_READ']
B043_CASE_IDS = None  # automatic seeded representative 40 cases, beginning with the 5 reported regressions
B043_SAMPLE_LIMIT = 40
B043_GRAPH_DIR = os.environ.get('B043_GRAPH_DIR')  # optional dataset path ending in artifacts_final
B043_REFERENCE_HASHES = os.environ.get('B043_REFERENCE_HASHES')  # optional independent {sample_id: full_state_hash}
B043_BASE = Path(os.environ.get('B043_WORKDIR','/kaggle/working/lifemate_b043_final' if Path('/kaggle/working').is_dir() else str(Path.cwd()/'reports/notebook_cpu_run')))
BUNDLE_ROOT = B043_BASE/'bundle'
OUTPUT = B043_BASE/'outputs'
OUTPUT.mkdir(parents=True,exist_ok=True)
''')
    code(f'''import base64,hashlib,io,sys,zipfile,importlib
PAYLOAD_SHA256 = {hashlib.sha256(raw).hexdigest()!r}
EMBEDDED_SHA256 = {checksums!r}
PAYLOAD = {encoded!r}
raw_bundle=base64.b64decode(PAYLOAD)
assert hashlib.sha256(raw_bundle).hexdigest()==PAYLOAD_SHA256
BUNDLE_ROOT.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(io.BytesIO(raw_bundle)) as archive:
    assert set(archive.namelist())==set(EMBEDDED_SHA256)
    for member in archive.infolist():
        target=(BUNDLE_ROOT/member.filename).resolve()
        if BUNDLE_ROOT.resolve() not in target.parents:raise ValueError('Unsafe payload member')
    archive.extractall(BUNDLE_ROOT)
for name,digest in EMBEDDED_SHA256.items():
    assert hashlib.sha256((BUNDLE_ROOT/name).read_bytes()).hexdigest()==digest, name
sys.path.insert(0,str(BUNDLE_ROOT))
for name in list(sys.modules):
    if any(name==p or name.startswith(p+'.') for p in ['lifemate_graph','tests','scripts']):del sys.modules[name]
importlib.invalidate_caches()
from lifemate_graph.common import read_json,write_json,sha256
from lifemate_graph.compiler import load_compiled
ACTIVE_GRAPH_DIR=Path(B043_GRAPH_DIR) if B043_GRAPH_DIR else BUNDLE_ROOT/'artifacts_final'
compiled=load_compiled(ACTIVE_GRAPH_DIR/'compiled_graph_v3.json',ACTIVE_GRAPH_DIR/'graph_manifest.json',BUNDLE_ROOT/'inputs/train_tools_catalog.json')
embedded=load_compiled(BUNDLE_ROOT/'artifacts_final/compiled_graph_v3.json',BUNDLE_ROOT/'artifacts_final/graph_manifest.json',BUNDLE_ROOT/'inputs/train_tools_catalog.json')
assert compiled['payload_sha256']==embedded['payload_sha256'],'External graph differs from the CPU-tested final graph'
manifest=read_json(ACTIVE_GRAPH_DIR/'graph_manifest.json')
for relative,digest in manifest['artifacts_sha256'].items():assert sha256(ACTIVE_GRAPH_DIR/relative)==digest,relative
authorized={{d['edge_id'] for d in read_json(BUNDLE_ROOT/'artifacts_completed/operator_approvals.json')['decisions'] if d['decision']=='APPROVED'}}
assert compiled['runtime_scope']=='PRODUCTION' and set(compiled['edges'])==authorized
print('Verified embedded files:',len(EMBEDDED_SHA256),'safe READ dependencies:',len(compiled['edges']))
print('Graph SHA:',compiled['graph_content_sha256'],'compiled payload SHA:',compiled['payload_sha256'])
''')
    code('''import runpy
cpu_runner=runpy.run_path(str(BUNDLE_ROOT/'scripts/run_cpu_checks.py'),run_name='b043_cpu_module')
CPU_REPORT=cpu_runner['run'](BUNDLE_ROOT,OUTPUT/'cpu')
assert CPU_REPORT['status']=='PASS','CPU regression failure: GPU stage blocked'
assert any('FinalGraphTests.test_all_approved_dependencies_participate_in_real_loop' in name for name in CPU_REPORT['passed'])
print('Final PRODUCTION graph passed CPU B043-loop gates. Fixture Policy/ToolEnv only.')
''')
    code('''import importlib.util
cuda=False
if B043_GPU!='0' and importlib.util.find_spec('torch'):
    import torch
    cuda=torch.cuda.is_available()
GPU_REPORT={'status':'NOT_RUN','gpu_executed':False,'reason':'CPU-only setting or no CUDA GPU'}
if B043_GPU=='1' and not cuda:GPU_REPORT={'status':'FAILED','gpu_executed':False,'reason':'GPU requested but CUDA unavailable'}
if B043_GPU!='0' and cuda:
    try:
        from lifemate_graph.kaggle import run_benchmark
        reference=read_json(B043_REFERENCE_HASHES) if B043_REFERENCE_HASHES else None
        GPU_REPORT=run_benchmark(BUNDLE_ROOT,OUTPUT/'benchmark',B043_CASE_IDS,B043_MODES,
            graph_dir=ACTIVE_GRAPH_DIR,reference_hashes=reference,limit=B043_SAMPLE_LIMIT,
            sample_budget_seconds=30.,require_graph_smoke=True)
        if not GPU_REPORT['graph_smoke_passed'] or GPU_REPORT['status']!='COMPLETE':
            GPU_REPORT['status']='FAILED'
            GPU_REPORT['reason']='Initial real-GPU smoke had no graph READ; inspect saved traces before a larger run'
    except Exception as exc:
        import traceback
        (OUTPUT/'gpu_failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
        GPU_REPORT={'status':'FAILED','gpu_executed':False,'error':str(exc),'reason':'Partial checkpoints and traceback saved'}
write_json(OUTPUT/'gpu_status.json',GPU_REPORT)
print({k:GPU_REPORT.get(k) for k in ['status','gpu_executed','n_runs','graph_smoke_passed','reason']})
''')
    code('''import shutil
write_json(OUTPUT/'delivery_manifest.json',{'cpu_status':CPU_REPORT['status'],'gpu_status':GPU_REPORT['status'],
    'payload_sha256':PAYLOAD_SHA256,'compiled_payload_sha256':compiled['payload_sha256'],
    'privacy':'Private development predictions/traces; do not publish without project permission'})
ZIP_PATH=Path(shutil.make_archive(str(B043_BASE/'LifeMate_B043_final_outputs'),'zip',OUTPUT))
print('Download:',ZIP_PATH)
try:
    from IPython.display import FileLink,display
    display(FileLink(str(ZIP_PATH)))
except ImportError:pass
if GPU_REPORT['status']=='FAILED':raise RuntimeError('GPU stage failed; output ZIP and checkpoints are available')
''')
    md('''## Outputs and interpretation

`benchmark/benchmark_report.json`, `run_manifest.json`, `predictions.jsonl`, `traces_PRIVATE.jsonl` and `checkpoints/` contain actual GPU runs only. Initial smoke must show graph_reads > 0 before the representative run continues. Selection includes single-tool, multi-tool/app/hop, ambiguous, missing-info, no-op and READ→WRITE strata; it is train-development, not an independent holdout.

Both arms use the same model, seed, ToolEnv and bounded inference settings. One ToolEnv is used per trajectory; no hidden state, future observations or gold rollout is used. Labels are evaluated only after each trajectory. `state_hash` is saved; State Match stays null without independently supplied full-state reference hashes. The fuzzy-call metric is explicitly a proxy until the official evaluator/tokenizer is available. Unnecessary READ, wrong-subject and unsafe-WRITE semantic rates stay null without independent adjudication.

30s is enforced before generation/new tool calls, with a remaining-time generation limit. CUDA passes and ToolEnv calls cannot be forcibly preempted; actual overruns are counted. Model loading/installation are outside per-sample inference timing. GRAPH_DISABLED remains the preserved B042 execution path with the same benchmark budget.

Resume only with the same fingerprint; changed source/model/graph/settings require a new B043_WORKDIR. No parity-92, Shadow benchmark, fine-tuning or graph discovery runs here.
''')
    nb={'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},
        'language_info':{'name':'python','version':'3.11'},'accelerator':'gpu','kaggle':{'isInternetEnabled':True,'accelerator':'gpu'}},'cells':cells}
    for i,c in enumerate(cells):c['id']=f'b043-final-{i:02d}'
    path=ROOT/'LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb'
    path.write_text(json.dumps(nb,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/final_payload_manifest.json').write_text(json.dumps({'payload_sha256':hashlib.sha256(raw).hexdigest(),
        'embedded_files':checksums,'notebook_sha256':hashlib.sha256(path.read_bytes()).hexdigest()},indent=2),encoding='utf-8')
    print(path,'embedded_files',len(files),'compressed_bytes',len(raw))
    return path

if __name__=='__main__':make()
