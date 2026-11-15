from __future__ import annotations
import argparse
from pathlib import Path
from .common import canonical


def main():
    p = argparse.ArgumentParser(description="LifeMate v3 offline build and CPU verification")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--root", type=Path, default=Path.cwd())
    b.add_argument("--out", type=Path)
    b.add_argument("--approvals", type=Path)
    c = sub.add_parser('compile', help='Compile an existing graph with explicit approvals; no discovery/rebuild')
    c.add_argument('--graph', required=True, type=Path)
    c.add_argument('--manifest', required=True, type=Path)
    c.add_argument('--catalog', required=True, type=Path)
    c.add_argument('--approvals', required=True, type=Path)
    c.add_argument('--out', required=True, type=Path)
    sub.add_parser("demo")
    args = p.parse_args()
    if args.command == "build":
        from .builder import build
        print(canonical(build(args.root, args.out, args.approvals)))
    elif args.command == "demo":
        from .demo import run_demo
        print(canonical(run_demo()))
    elif args.command == 'compile':
        from .compiler import compile_existing
        print(canonical(compile_existing(args.graph, args.manifest, args.catalog, args.approvals, args.out)))


if __name__ == "__main__": main()
