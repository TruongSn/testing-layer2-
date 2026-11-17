"""Run the supplied LifeMate Turbo release in its own Python process."""
import importlib.metadata
import json
import sys
from pathlib import Path


def main():
    release, catalog_path, device = sys.argv[1:]
    paths = json.load(sys.stdin)
    sys.path.insert(0, release)
    from inference import normalize_texts, transcribe_ct2

    if device == "auto":
        import ctranslate2

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"

    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8-sig"))
    decoded = transcribe_ct2(list(dict.fromkeys(paths.values())), str(Path(release) / "model"), device)
    raw = {relative: decoded[path][0] for relative, path in paths.items()}
    normalized = normalize_texts(raw, catalog=catalog, mode="safe")
    result = {
        "device": device,
        "compute_type": "int8_float16" if device == "cuda" else "int8",
        "packages": {name: importlib.metadata.version(name) for name in ("faster-whisper", "ctranslate2")},
        "transcripts": {
            relative: {"transcript": normalized[relative], "raw_transcript": raw[relative],
                       "confidence": decoded[path][1]}
            for relative, path in paths.items()
        },
    }
    json.dump(result, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
