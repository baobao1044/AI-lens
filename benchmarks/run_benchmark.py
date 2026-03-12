#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.query import query_call_chain, query_related, query_semantic, query_symbol
from scripts.scan import scan_project


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a synthetic repo and benchmark ai-lens.")
    parser.add_argument("--workspace", help="Directory to create the synthetic repo in. Defaults to a temporary directory.")
    parser.add_argument("--modules", type=int, default=100, help="Number of Python modules to generate.")
    parser.add_argument("--fanout", type=int, default=3, help="How many forward dependencies each module should call.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable benchmark output.")
    parser.add_argument("--keep-repo", action="store_true", help="Keep the generated repo on disk.")
    return parser.parse_args()


def create_synthetic_repo(root: Path, modules: int, fanout: int) -> Path:
    repo = root / "synthetic_repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("# Synthetic Repo\n", encoding="utf-8")

    for index in range(modules):
        downstream = []
        for offset in range(1, fanout + 1):
            target = index + offset
            if target < modules:
                downstream.append(target)
        imports = "\n".join(f"from module_{target} import helper_{target}" for target in downstream)
        calls = "\n".join(f"    helper_{target}(value)\n" for target in downstream)
        content = f"""
{imports}


def entry_{index}(value: str) -> str:
{calls if calls else "    return helper_" + str(index) + "(value)\\n"}
    return helper_{index}(value)


def helper_{index}(value: str) -> str:
    return value.strip().lower()
"""
        (repo / f"module_{index}.py").write_text(content.strip() + "\n", encoding="utf-8")
    return repo


def run_benchmark(repo: Path) -> dict:
    started = time.perf_counter()
    manifest = scan_project(str(repo), force=True, quiet=True)
    scan_seconds = time.perf_counter() - started

    symbol_started = time.perf_counter()
    symbol_payload = query_symbol(repo, "entry_0", top=5)
    symbol_seconds = time.perf_counter() - symbol_started

    related_started = time.perf_counter()
    related_payload = query_related(repo, "helper", top=5)
    related_seconds = time.perf_counter() - related_started

    chain_started = time.perf_counter()
    chain_payload = query_call_chain(repo, "entry_0", depth=3, top=3)
    chain_seconds = time.perf_counter() - chain_started

    semantic_started = time.perf_counter()
    semantic_payload = query_semantic(repo, "helper normalization", engine="lexical", top=5)
    semantic_seconds = time.perf_counter() - semantic_started

    index_dir = repo / ".ai-lens"
    index_size = sum(path.stat().st_size for path in index_dir.rglob("*") if path.is_file())
    return {
        "repo": repo.as_posix(),
        "modules": len(list(repo.glob("module_*.py"))),
        "scan_seconds": round(scan_seconds, 4),
        "query_seconds": {
            "symbol": round(symbol_seconds, 4),
            "related": round(related_seconds, 4),
            "call_chain": round(chain_seconds, 4),
            "semantic_lexical": round(semantic_seconds, 4),
        },
        "index_size_bytes": index_size,
        "manifest_total_files": manifest["project"]["total_files"],
        "results": {
            "symbol": len(symbol_payload["results"]),
            "related": len(related_payload["results"]),
            "call_chain": len(chain_payload["results"]),
            "semantic": len(semantic_payload["results"]),
        },
    }


def main() -> int:
    args = parse_args()
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.workspace:
            base = Path(args.workspace).resolve()
            base.mkdir(parents=True, exist_ok=True)
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="ai_lens_bench_")
            base = Path(temp_dir.name)

        repo = create_synthetic_repo(base, args.modules, args.fanout)
        payload = run_benchmark(repo)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Repo: {payload['repo']}")
            print(f"Modules: {payload['modules']}")
            print(f"Scan: {payload['scan_seconds']}s")
            for name, value in payload["query_seconds"].items():
                print(f"{name}: {value}s")
            print(f"Index size: {payload['index_size_bytes']} bytes")
        return 0
    finally:
        if temp_dir is None and not args.keep_repo and args.workspace:
            shutil.rmtree(base / "synthetic_repo", ignore_errors=True)
        if temp_dir is not None and not args.keep_repo:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
