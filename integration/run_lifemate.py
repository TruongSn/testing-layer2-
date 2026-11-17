"""Run the supplied Whisper Turbo and B043 releases on LifeMate audio."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import io
import json
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_NAME = "LifeMate_B043_Full_Graph_Resolver_Kaggle.ipynb"
NOTEBOOK_SHA256 = "e4758e339f4f08a6bee561afc51dae29e294eaee6f0521ad41d5fa573519cf29"
B042_NOTEBOOK = "inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb"
SOURCE_CATALOG = "inputs/train_tools_catalog.json"
COMPILED_GRAPH = "artifacts_final/compiled_graph_v3.json"
GRAPH_MANIFEST = "artifacts_final/graph_manifest.json"
SAMPLE_BUDGET_SECONDS = 30


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack_b043(notebook, destination):
    data = notebook.read_bytes()
    if hashlib.sha256(data).hexdigest() != NOTEBOOK_SHA256:
        raise ValueError("Expected the pinned B043 Final notebook linked in docs/layer1-layer2.md")
    source = "".join(json.loads(data)["cells"][2]["source"])
    # The pinned notebook contains an embedded ZIP. Read its data without running its cells.
    payload = re.search(r"^PAYLOAD = '([A-Za-z0-9+/=]+)'$", source, re.MULTILINE)
    if payload is None:
        raise ValueError("B043 notebook is missing its embedded runtime")
    package = ("__init__", "b043", "common", "compiler", "context", "inference", "runtime")
    members = [f"lifemate_graph/{name}.py" for name in package]
    members.extend((B042_NOTEBOOK, SOURCE_CATALOG, COMPILED_GRAPH, GRAPH_MANIFEST))
    with ZipFile(io.BytesIO(base64.b64decode(payload[1], validate=True))) as archive:
        for member in members:
            path = destination / member
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(archive.read(member))


def user_audio_paths(samples, split_dir):
    paths = {}
    for sample in samples:
        refs = [sample["audio"]]
        refs.extend(turn["audio"] for turn in sample["history"] if turn["role"] == "user")
        for ref in refs:
            path = (split_dir / ref).resolve()
            if Path(ref).is_absolute() or not path.is_relative_to(split_dir):
                raise ValueError(f"Expected a split-relative WAV path: {ref}")
            if not path.is_file():
                raise FileNotFoundError(path)
            paths[ref] = str(path)
    return paths


def prepare_items(samples, transcripts):
    def text_for(audio):
        row = transcripts.get(audio)
        text = row.get("transcript") if isinstance(row, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Missing or empty ASR transcript for {audio}")
        return " ".join(unicodedata.normalize("NFC", text).split())

    for sample in samples:
        history = []
        for turn in sample["history"]:
            if turn["role"] == "user":
                history.append({"role": "user", "audio_text": text_for(turn["audio"])})
            elif turn["role"] == "assistant":
                history.append({k: turn[k] for k in ("role", "text", "tool_calls", "tool_results") if k in turn})
        yield {"id": sample["id"], "audio_text": text_for(sample["audio"]),
               "context": sample["context"], "history": history}


def checked_graph_tools(compiled, reviewed_tools, split_tools):
    endpoints = {edge[key] for edge in compiled["edges"].values()
                 for key in ("producer_tool", "consumer_tool")}
    for name in sorted(endpoints & split_tools.keys()):
        if split_tools[name] != reviewed_tools[name]:
            raise ValueError(f"Split catalog changes a reviewed B043 tool contract: {name}")
    return sum(edge["producer_tool"] in split_tools and edge["consumer_tool"] in split_tools
               for edge in compiled["edges"].values())


class DeadlineSession:
    def __init__(self, session, deadline):
        self.session = session
        self.deadline = deadline

    def check_deadline(self):
        if time.perf_counter() >= self.deadline:
            raise TimeoutError("Layer 2 sample budget exhausted")

    def validate(self, name, arguments):
        self.check_deadline()
        return self.session.validate(name, arguments)

    def call(self, name, arguments):
        self.check_deadline()
        return self.session.call(name, arguments)

    def get_tool_calls(self):
        return self.session.get_tool_calls()

    def get_state_hash(self):
        return self.session.get_state_hash()


def run_item(harness, item, tools, tool_env, split, root, model, tokenizer, torch, bounded_generate):
    deadline = time.perf_counter() + SAMPLE_BUDGET_SECONDS
    opened = False

    def factory(_b042_split, sample_id, *, root):
        # B042 passes "train" from its preserved loop. Bind the real split at the SDK boundary.
        nonlocal opened
        if opened or sample_id != item["id"]:
            raise RuntimeError("Expected one ToolEnv for this sample")
        opened = True
        return DeadlineSession(tool_env(split, sample_id, root=root), deadline)

    module = harness.module
    module.B041_FIELD_AUDIT.clear()
    module.B042_PROGRESS_AUDIT.clear()
    module.generate_once = bounded_generate(module, deadline)
    return harness.run(item, tools, factory, root, model, tokenizer, torch)


def main(args, bundle):
    import torch
    from lifemate_graph.b043 import B043Harness
    from lifemate_graph.common import read_json, tool_index, write_json
    from lifemate_graph.compiler import load_compiled
    from lifemate_graph.inference import bounded_generate
    from lifemate_graph.runtime import GraphMode

    root = args.data_root.resolve()
    split = args.split
    split_dir = (root / split).resolve()
    catalog_path = split_dir / f"{split}_tools_catalog.json"
    index_path = split_dir / f"{split}.jsonl"
    release = args.asr_release.resolve() if args.asr_release else None
    output = args.output.resolve()
    if output.is_relative_to(root) or (release and output.is_relative_to(release)):
        raise ValueError("Choose a new output directory outside the data and ASR releases")
    asr_files = ("inference.py", "audio.py", "normalizer.py", "model/model.bin")
    if release:
        for relative in asr_files:
            if not (release / relative).is_file():
                raise FileNotFoundError(release / relative)
    if not (split_dir / "zaic_tools/__init__.py").is_file():
        raise FileNotFoundError(split_dir / "zaic_tools/__init__.py")

    with index_path.open(encoding="utf-8-sig") as index:
        samples = [json.loads(line) for line in index if line.strip()]
    ids = {sample["id"] for sample in samples}
    if not ids or len(ids) != len(samples):
        raise ValueError("Expected a non-empty split with unique sample IDs")
    selected = set(args.sample_id or ids)
    if selected - ids:
        raise ValueError(f"Unknown IDs for {split}: {sorted(selected - ids)}")
    samples = [sample for sample in samples if sample["id"] in selected]
    paths = user_audio_paths(samples, split_dir) if release else None
    catalog = read_json(catalog_path)
    tools = tool_index(catalog)
    catalog_sha = sha256(catalog_path)
    compiled = load_compiled(bundle / COMPILED_GRAPH, bundle / GRAPH_MANIFEST, bundle / SOURCE_CATALOG)
    edge_count = checked_graph_tools(compiled, tool_index(read_json(bundle / SOURCE_CATALOG)), tools)
    harness = B043Harness(bundle / B042_NOTEBOOK, compiled, GraphMode.GRAPH_ASSISTED_SAFE_READ)
    harness.module.CONFIG["soft_deadline_seconds"] = SAMPLE_BUDGET_SECONDS
    if not torch.cuda.is_available():
        raise RuntimeError("The supplied B043 Qwen model requires a CUDA GPU")

    # Load the released SDK of the selected split without writing bytecode into the release.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(split_dir))
    from zaic_tools import ToolEnv

    manifest = {
        "split": split,
        "sample_ids": [sample["id"] for sample in samples],
        "split_sha256": sha256(index_path),
        "catalog_sha256": catalog_sha,
        "b043_notebook_sha256": NOTEBOOK_SHA256,
        "graph_payload_sha256": compiled["payload_sha256"],
        "graph_source_catalog_sha256": compiled["catalog_digest"],
        "graph_edges_available_in_split": edge_count,
        "layer2": {"protocol": "B043 JSON action",
                   "graph_mode": harness.mode.value,
                   "gpu": torch.cuda.get_device_name(0),
                   "cuda": torch.version.cuda,
                   "precision": "int4 NF4 double quantization, float16 compute",
                   "config": harness.module.CONFIG,
                   "packages": {name: importlib.metadata.version(name) for name in
                                ("torch", "transformers", "accelerate", "bitsandbytes")}},
    }
    if release:
        manifest["asr_files_sha256"] = {name: sha256(release / name) for name in asr_files}
    else:
        manifest["asr_input_sha256"] = sha256(args.asr_result)
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = output / "run_manifest.json"
    write_json(manifest_path, dict(manifest, status="running"))
    sample_id = None
    try:
        if release:
            print(f"Layer 1: {len(paths)} WAV files from {split}", flush=True)
            worker = subprocess.run(
                [args.asr_python, "-I", "-B", str(Path(__file__).with_name("asr_turbo_worker.py")),
                 str(release), str(catalog_path), args.asr_device],
                input=json.dumps(paths), stdout=subprocess.PIPE, encoding="utf-8", check=True,
            )
            asr = json.loads(worker.stdout)
        else:
            print(f"Layer 1: using saved transcripts from {args.asr_result}", flush=True)
            asr = read_json(args.asr_result)
        if not isinstance(asr, dict) or not isinstance(asr.get("transcripts"), dict):
            raise ValueError("Expected a LifeMate asr.json with a transcripts object")
        write_json(output / "asr.json", asr)
        manifest["asr"] = {k: v for k, v in asr.items() if k != "transcripts"}
        items = list(prepare_items(samples, asr["transcripts"]))

        print(f"Layer 2: B043 on {len(items)} samples, {edge_count} graph edges in the split catalog", flush=True)
        module = harness.module
        module.authenticate_hf()
        model, tokenizer = module.load_policy_model(torch)
        rows = []
        with (output / "traces.jsonl").open("x", encoding="utf-8") as trace:
            for item in items:
                sample_id = item["id"]
                module.b042_seed(sample_id, torch)
                result = run_item(harness, item, tools, ToolEnv, split, root, model, tokenizer,
                                  torch, bounded_generate)
                rows.append(result["public"])
                diagnostics = result["diagnostics"]
                # B043 includes gold-matching diagnostics even with no labels supplied.
                record = {"id": sample_id, "stop_reason": diagnostics["stop_reason"],
                          "graph_reads": diagnostics["graph_reads"], "seconds": diagnostics["seconds"],
                          "planner": result["private_debug"]["planner"],
                          "steps": result["private_debug"]["steps"],
                          "graph_events": result["private_debug"]["graph_events"]}
                trace.write(json.dumps(record, ensure_ascii=False) + "\n")
                trace.flush()
                print(f"{sample_id}: {record['stop_reason']}", flush=True)
        sample_id = None
        pending = output / "submission.jsonl.tmp"
        with pending.open("x", encoding="utf-8") as submission:
            for row in rows:
                submission.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        pending.replace(output / "submission.jsonl")
    except (Exception, KeyboardInterrupt) as exc:
        write_json(manifest_path, dict(manifest, status="failed", failed_sample_id=sample_id, error=str(exc)))
        raise
    write_json(manifest_path, dict(manifest, status="finished"))
    print(f"{len(rows)} rows -> {output / 'submission.jsonl'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--split", required=True, choices=("train", "public_test"))
    parser.add_argument("--sample-id", action="append", help="Repeat to select IDs; defaults to the entire split")
    asr_input = parser.add_mutually_exclusive_group(required=True)
    asr_input.add_argument("--asr-release", type=Path, help="LifeMate Turbo release directory")
    asr_input.add_argument("--asr-result", type=Path, help="Reuse asr.json from a previous LifeMate run")
    parser.add_argument("--asr-python", default=sys.executable)
    parser.add_argument("--asr-device", default="cpu", choices=("cpu", "cuda", "auto"))
    parser.add_argument("--b043-notebook", type=Path, default=ROOT / "depency_graph_v3" / NOTEBOOK_NAME)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="lifemate-b043-") as temporary:
        bundle = Path(temporary)
        unpack_b043(args.b043_notebook, bundle)
        sys.path.insert(0, temporary)
        main(args, bundle)
