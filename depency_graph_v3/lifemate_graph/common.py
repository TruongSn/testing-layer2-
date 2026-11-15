"""Deterministic IO, nested paths, and the catalog's JSON Schema subset."""
from __future__ import annotations
import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def object_sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, columns):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: canonical(row[k]) if isinstance(row.get(k), (dict, list)) else row.get(k, "") for k in columns})


def norm(value):
    # Preserve accents and role words: no fuzzy or honorific stripping for identity.
    return " ".join(unicodedata.normalize("NFC", str(value)).casefold().split())


def equal(a, b):
    return type(a) is type(b) and (norm(a) == norm(b) if isinstance(a, str) else a == b)


def opaque_field(path):
    key = path.split('.')[-1]
    return key == 'id' or key.endswith('_id') or key in {'phone', 'phone_number', 'account_number', 'tracking_id'}


def field_equal(path, a, b):
    """Human labels normalize Unicode/case; identifiers remain typed and case-sensitive."""
    return type(a) is type(b) and (a == b if opaque_field(path) else equal(a, b))


def value_type(v):
    if v is None: return "null"
    if isinstance(v, bool): return "boolean"
    if isinstance(v, int): return "integer"
    if isinstance(v, float): return "number"
    if isinstance(v, str): return "string"
    if isinstance(v, list): return "array"
    if isinstance(v, dict): return "object"
    return "unknown"


def fields(schema, prefix="", ancestors_required=True):
    for k, v in schema.get("properties", {}).items():
        path = prefix + ("." if prefix else "") + k
        local = k in schema.get("required", [])
        yield {"path": path, "required": local and ancestors_required,
               "locally_required": local, "schema": v, "type": v.get("type"),
               "x_ref": v.get("x-ref", v.get("x_ref")), "description": v.get("description", "")}
        yield from fields(v, path, ancestors_required and local)
    if isinstance(schema.get("items"), dict):
        if schema["items"].get("type") != "object":
            yield {"path": prefix + "[]", "required": ancestors_required, "locally_required": True,
                   "schema": schema["items"], "type": schema["items"].get("type"),
                   "x_ref": schema["items"].get("x-ref"), "description": schema.get("description", "")}
        yield from fields(schema["items"], prefix + "[]", ancestors_required)


def leaves(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, prefix + ("." if prefix else "") + str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from leaves(v, prefix + "[]")
    else:
        yield prefix, obj


def path_entries(obj, path):
    """Return (leaf value, closest enclosing record), keeping array row identity."""
    parts = [p for p in re.sub(r'\[(\d*)\]', r'.[\1]', path).split('.') if p]
    current = [(obj, obj if isinstance(obj, dict) else {})]
    for part in parts:
        nxt = []
        for value, record in current:
            if part == "[]" and isinstance(value, list):
                nxt.extend((x, x if isinstance(x, dict) else record) for x in value)
            elif re.fullmatch(r'\[\d+\]', part) and isinstance(value, list):
                index = int(part[1:-1])
                if index < len(value):
                    child = value[index]
                    nxt.append((child, child if isinstance(child, dict) else record))
            elif isinstance(value, dict) and part in value:
                child = value[part]
                nxt.append((child, child if isinstance(child, dict) else value))
        current = nxt
    return current


def get_path(obj, path, default=None):
    vals = path_entries(obj, path)
    return vals[0][0] if len(vals) == 1 else default


def set_path(obj, path, value):
    if "[]" in path:
        raise ValueError("Array-row construction requires a model decision; never broadcast an ID")
    parts = [int(p[1:-1]) if p.startswith('[') else p
             for p in re.sub(r'\[(\d+)\]', r'.[\1]', path).split('.')]
    for i, k in enumerate(parts[:-1]):
        container = [] if isinstance(parts[i+1], int) else {}
        if isinstance(k, int):
            if not isinstance(obj, list) or k > len(obj): raise ValueError('Non-contiguous array row')
            if k == len(obj): obj.append(container)
            obj = obj[k]
        else: obj = obj.setdefault(k, container)
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(obj, list) or last > len(obj): raise ValueError('Non-contiguous array row')
        if last == len(obj): obj.append(value)
        else: obj[last] = value
    else: obj[last] = value


def schema_path(path):
    return re.sub(r'\[\d+\]', '[]', path)


def schema_errors(value, schema, path="", partial=False):
    errors = []
    expected = schema.get("type")
    actual = value_type(value)
    if expected and actual != expected and not (expected == "number" and actual == "integer"):
        return [f"{path or '$'}: expected {expected}, got {actual}"]
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        errors.append(f"{path}: invalid enum")
    if actual in {"integer", "number"}:
        for key, compare in [("minimum", lambda a,b:a<b), ("maximum",lambda a,b:a>b),
                             ("exclusiveMinimum",lambda a,b:a<=b), ("exclusiveMaximum",lambda a,b:a>=b)]:
            if key in schema and compare(value, schema[key]): errors.append(f"{path}: {key}")
    if actual == "string":
        if "pattern" in schema and not re.search(schema["pattern"], value): errors.append(f"{path}: pattern")
        if len(value) < schema.get("minLength", 0): errors.append(f"{path}: minLength")
        fmt = schema.get("format")
        if fmt:
            import datetime
            try:
                if fmt == "date": datetime.date.fromisoformat(value)
                elif fmt == "date-time":
                    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if dt.tzinfo is None: raise ValueError("timezone missing")
            except ValueError: errors.append(f"{path}: {fmt}")
    if actual == "object":
        props = schema.get("properties", {})
        if not partial:
            errors.extend(f"{path + '.' if path else ''}{k}: missing" for k in schema.get("required", []) if k not in value)
            alts = schema.get("x-one-of", [])
            if alts and not any(all(k in value and value[k] not in (None, "", []) for k in group) for group in alts):
                errors.append(f"{path}: unsatisfied x-one-of")
        for k, v in value.items():
            if k not in props:
                errors.append(f"{path}.{k}: unknown argument")
            else: errors.extend(schema_errors(v, props[k], path + ("." if path else "") + k, partial))
    if actual == "array":
        if len(value) < schema.get("minItems", 0): errors.append(f"{path}: minItems")
        for i, v in enumerate(value):
            errors.extend(schema_errors(v, schema.get("items", {}), f"{path}[{i}]", partial))
    return errors


def tool_index(catalog):
    tools = {}
    for app in catalog["apps"]:
        for t in app["tools"]:
            if t["name"] in tools: raise ValueError("Duplicate tool " + t["name"])
            if t["kind"] not in {"read", "write", "meta"}: raise ValueError("Unknown tool kind")
            tools[t["name"]] = {**t, "app": app["id"]}
    return tools
