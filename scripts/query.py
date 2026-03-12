#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path

try:
    from .index_store import detect_stale, flatten_symbols, load_manifest, read_symbol_range, symbol_summary
    from .semantic import query_semantic as run_semantic_query
    from .symbol_graph import query_call_chain as run_call_chain_query
except ImportError:
    from index_store import detect_stale, flatten_symbols, load_manifest, read_symbol_range, symbol_summary
    from semantic import query_semantic as run_semantic_query
    from symbol_graph import query_call_chain as run_call_chain_query


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query an ai-lens project index.")
    parser.add_argument("--index", default=".", help="Project root, .ai-lens directory, manifest path, or .ai-lens.json snapshot.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", help="Look up a symbol by name.")
    group.add_argument("--related", help="Find files and symbols related to free text.")
    group.add_argument("--dependents", help="Find files that depend on the given indexed file.")
    group.add_argument("--pattern", help="Glob pattern over indexed file paths.")
    group.add_argument("--semantic", help="Find symbols by meaning using TF-IDF, embeddings, or lexical fallback.")
    group.add_argument("--call-chain", dest="call_chain", help="Trace the call chain for a symbol.")
    group.add_argument("--type", choices=["architecture", "full"], help="Built-in query mode.")
    parser.add_argument("--top", type=int, default=10, help="Maximum number of results to show.")
    parser.add_argument("--depth", type=int, default=3, help="Depth for --call-chain traversal.")
    parser.add_argument("--direction", choices=["down", "up"], default="down", help="Direction for --call-chain traversal.")
    parser.add_argument("--semantic-engine", choices=["auto", "tfidf", "embedding", "lexical"], default="auto", help="Engine preference for --semantic.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    parser.add_argument("--include-dependents", action="store_true", help="Include dependent file paths in related and symbol output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.symbol:
        payload = query_symbol(args.index, args.symbol, top=args.top)
    elif args.related:
        payload = query_related(args.index, args.related, top=args.top)
    elif args.dependents:
        payload = query_dependents(args.index, args.dependents, top=args.top)
    elif args.pattern:
        payload = query_pattern(args.index, args.pattern, top=args.top)
    elif args.semantic:
        payload = query_semantic(args.index, args.semantic, top=args.top, engine=args.semantic_engine)
    elif args.call_chain:
        payload = query_call_chain(args.index, args.call_chain, depth=args.depth, direction=args.direction, top=args.top)
    elif args.type == "architecture":
        payload = query_architecture(args.index, top=args.top)
    else:
        payload = query_full(args.index, top=args.top, as_json=args.json)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_query_result(payload, include_dependents=args.include_dependents))
    return 0


def load_index(index_or_project_path: str | Path) -> tuple[dict, Path]:
    return load_manifest(index_or_project_path)


def query_symbol(index_or_project_path: str | Path, symbol: str, *, top: int = 10) -> dict:
    manifest, manifest_path = load_index(index_or_project_path)
    files = manifest.get("files", [])
    reverse_graph = build_reverse_graph(manifest.get("dependency_graph", {}))
    results = []
    needle = symbol.lower()
    for file_entry in files:
        for item in flatten_symbols(file_entry):
            name = item["name"].lower()
            if name == needle:
                score = 100.0 + file_entry["rank"]
            elif needle in name:
                score = 50.0 + file_entry["rank"]
            else:
                continue
            results.append(
                {
                    "path": file_entry["path"],
                    "rank": file_entry["rank"],
                    "score": score,
                    "lines": file_entry["lines"],
                    "symbol": symbol_summary(item),
                    "imports": file_entry.get("imports", []),
                    "exports": file_entry.get("exports", []),
                    "dependents": reverse_graph.get(file_entry["path"], []),
                }
            )
    payload = {
        "query": f'symbol "{symbol}"',
        "mode": "symbol",
        "results": sorted(results, key=lambda item: (-item["score"], item["path"]))[:top],
    }
    return _finalize_payload(payload, manifest, manifest_path)


def query_related(index_or_project_path: str | Path, keyword: str, *, top: int = 10) -> dict:
    manifest, manifest_path = load_index(index_or_project_path)
    files = manifest.get("files", [])
    reverse_graph = build_reverse_graph(manifest.get("dependency_graph", {}))
    keywords = [part.lower() for part in keyword.replace("/", " ").replace("-", " ").split() if part]
    max_rank = max((entry["rank"] for entry in files), default=1.0)
    results: list[dict] = []
    for file_entry in files:
        score = 0.0
        reasons: list[str] = []
        path_lower = file_entry["path"].lower()
        for item in flatten_symbols(file_entry):
            name = item["name"].lower()
            for part in keywords:
                if name == part:
                    score += 10
                    reasons.append(f'exact symbol "{part}"')
                elif part in name:
                    score += 5
                    reasons.append(f'symbol contains "{part}"')
        for part in keywords:
            if part in path_lower:
                score += 3
                reasons.append(f'path contains "{part}"')
            if any(part in value.lower() for value in file_entry.get("imports", [])):
                score += 2
                reasons.append(f'import mentions "{part}"')
            if any(part in value.lower() for value in file_entry.get("exports", [])):
                score += 2
                reasons.append(f'export mentions "{part}"')
        if score <= 0:
            continue
        score *= 0.5 + (file_entry["rank"] / max_rank if max_rank else 0)
        results.append(
            {
                "path": file_entry["path"],
                "rank": file_entry["rank"],
                "score": round(score, 2),
                "lines": file_entry["lines"],
                "matches": shortlist_symbols(file_entry, keywords),
                "dependents": reverse_graph.get(file_entry["path"], []),
                "reasons": sorted(set(reasons))[:5],
            }
        )
    payload = {
        "query": f'related "{keyword}"',
        "mode": "related",
        "results": sorted(results, key=lambda item: (-item["score"], item["path"]))[:top],
    }
    return _finalize_payload(payload, manifest, manifest_path)


def query_architecture(index_or_project_path: str | Path, *, top: int = 10) -> dict:
    manifest, manifest_path = load_index(index_or_project_path)
    files = manifest.get("files", [])
    graph = manifest.get("dependency_graph", {})
    reverse_graph = build_reverse_graph(graph)
    entry_points = [entry for entry in files if entry.get("rank_inputs", {}).get("entrypoint_bonus", 0) > 0]
    config_files = [entry for entry in files if entry.get("rank_inputs", {}).get("config_bonus", 0) > 0]
    top_files = sorted(files, key=lambda item: (-item["rank"], item["path"]))[:top]
    most_depended = sorted(reverse_graph.items(), key=lambda item: (-len(item[1]), item[0]))[:top]
    payload = {
        "query": "architecture",
        "mode": "architecture",
        "entry_points": summarize_files(entry_points[:top]),
        "config_files": summarize_files(config_files[:top]),
        "top_files": summarize_files(top_files),
        "dependency_summary": [
            {"path": path, "dependents": dependents[:10], "count": len(dependents)}
            for path, dependents in most_depended
        ],
        "graph_stats": {
            "nodes": len(files),
            "edges": sum(len(targets) for targets in graph.values()),
        },
    }
    return _finalize_payload(payload, manifest, manifest_path)


def query_dependents(index_or_project_path: str | Path, file_path: str, *, top: int = 10) -> dict:
    manifest, manifest_path = load_index(index_or_project_path)
    files = manifest.get("files", [])
    reverse_graph = build_reverse_graph(manifest.get("dependency_graph", {}))
    normalized = Path(file_path).as_posix().lstrip("./")
    by_path = {entry["path"]: entry for entry in files}
    dependents = reverse_graph.get(normalized, [])
    results = []
    for dependent in dependents[:top]:
        file_entry = by_path.get(dependent)
        if not file_entry:
            continue
        results.append(
            {
                "path": file_entry["path"],
                "rank": file_entry["rank"],
                "lines": file_entry["lines"],
                "imports": file_entry.get("imports", []),
                "exports": file_entry.get("exports", []),
            }
        )
    payload = {
        "query": f'dependents "{normalized}"',
        "mode": "dependents",
        "target": normalized,
        "results": results,
    }
    return _finalize_payload(payload, manifest, manifest_path)


def query_pattern(index_or_project_path: str | Path, pattern: str, *, top: int = 10) -> dict:
    manifest, manifest_path = load_index(index_or_project_path)
    results = []
    for file_entry in manifest.get("files", []):
        if fnmatch.fnmatch(file_entry["path"], pattern):
            results.append(
                {
                    "path": file_entry["path"],
                    "rank": file_entry["rank"],
                    "lines": file_entry["lines"],
                    "symbols": [symbol_summary(symbol) for symbol in flatten_symbols(file_entry)[:10]],
                }
            )
    payload = {
        "query": f'pattern "{pattern}"',
        "mode": "pattern",
        "results": sorted(results, key=lambda item: (-item["rank"], item["path"]))[:top],
    }
    return _finalize_payload(payload, manifest, manifest_path)


def query_full(index_or_project_path: str | Path, *, top: int = 10, as_json: bool = False) -> dict:
    manifest, manifest_path = load_index(index_or_project_path)
    files = manifest.get("files", [])
    results = files if as_json or len(files) <= 50 else files[:top]
    payload = {
        "query": "full",
        "mode": "full",
        "results": results,
        "truncated": len(results) != len(files),
    }
    return _finalize_payload(payload, manifest, manifest_path)


def query_semantic(
    index_or_project_path: str | Path,
    text: str,
    *,
    top: int = 10,
    engine: str = "auto",
) -> dict:
    payload = run_semantic_query(index_or_project_path, text, top_k=top, engine=engine)
    manifest, manifest_path = load_index(index_or_project_path)
    payload.setdefault("mode", "semantic")
    payload.setdefault("query", f'semantic "{text}"')
    payload["requested_engine"] = engine
    return _finalize_payload(payload, manifest, manifest_path)


def query_call_chain(
    index_or_project_path: str | Path,
    symbol_name: str,
    *,
    depth: int = 3,
    direction: str = "down",
    top: int = 5,
) -> dict:
    payload = run_call_chain_query(
        index_or_project_path,
        symbol_name,
        depth=depth,
        direction=direction,
        top=top,
    )
    manifest, manifest_path = load_index(index_or_project_path)
    return _finalize_payload(payload, manifest, manifest_path)


def get_symbol_implementation(project_path: str | Path, file_path: str, symbol_name: str) -> dict | None:
    manifest, _ = load_index(project_path)
    return read_symbol_range(project_path, file_path, symbol_name, manifest=manifest)


def format_query_result(payload: dict, *, include_dependents: bool = False) -> str:
    lines = [f"## Query: {payload['query']}"]
    if payload.get("stale"):
        lines.append("")
        lines.append("Warning: index may be stale. Re-run `python scripts/scan.py .` from the project root.")

    mode = payload["mode"]
    if mode == "architecture":
        lines.extend(render_architecture(payload))
        return "\n".join(lines)
    if mode == "call_chain":
        lines.extend(render_call_chain(payload))
        return "\n".join(lines)

    results = payload.get("results", [])
    if not results:
        lines.append("")
        lines.append("No matches found.")
        return "\n".join(lines)

    for result in results:
        lines.append("")
        if mode == "symbol":
            symbol = result["symbol"]
            lines.append(f"### Found in: {result['path']} (rank: {result['rank']}, {result['lines']} lines)")
            lines.append(f"```text\n{symbol['signature']}\n```")
            lines.append(
                f"Lines: {symbol['line_start']}-{symbol['line_end']} | Imports: {format_list(result['imports'])} | Exports: {format_list(result['exports'])}"
            )
            if include_dependents and result.get("dependents"):
                lines.append(f"Dependents: {format_list(result['dependents'])}")
        elif mode == "related":
            lines.append(f"### {result['path']} (score: {result['score']}, rank: {result['rank']}, {result['lines']} lines)")
            if result["matches"]:
                first = result["matches"][0]
                lines.append(f"```text\n{first['signature']}\n```")
                lines.append(f"Best match lines: {first['line_start']}-{first['line_end']}")
            lines.append(f"Why: {format_list(result['reasons'])}")
            if include_dependents and result.get("dependents"):
                lines.append(f"Dependents: {format_list(result['dependents'])}")
        elif mode == "dependents":
            lines.append(f"### {result['path']} (rank: {result['rank']}, {result['lines']} lines)")
            lines.append(f"Imports: {format_list(result['imports'])} | Exports: {format_list(result['exports'])}")
        elif mode == "pattern":
            lines.append(f"### {result['path']} (rank: {result['rank']}, {result['lines']} lines)")
            if result["symbols"]:
                lines.append(f"```text\n{result['symbols'][0]['signature']}\n```")
        elif mode == "full":
            lines.append(f"### {result['path']} (rank: {result['rank']}, {result['lines']} lines)")
            symbols = flatten_symbols(result)
            if symbols:
                lines.append(f"```text\n{symbols[0]['signature']}\n```")
        elif mode == "semantic":
            symbol = result["symbol"]
            lines.append(
                f"### {result['path']} (score: {result.get('boosted_score', result['score'])}, engine: {payload.get('engine', 'unknown')})"
            )
            lines.append(f"```text\n{symbol['signature']}\n```")
            lines.append(
                f"Lines: {symbol['line_start']}-{symbol['line_end']} | Rank boost: {result.get('rank', 0)}"
            )
            if result.get("why"):
                lines.append(f"Why: {result['why']}")
    if mode == "full" and payload.get("truncated"):
        lines.append("")
        lines.append(f"Showing top {len(results)} files only. Use `--json` to dump the full index snapshot.")
    return "\n".join(lines)


def build_reverse_graph(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)
    return {key: sorted(value) for key, value in reverse.items()}


def shortlist_symbols(file_entry: dict, keywords: list[str]) -> list[dict]:
    matches = []
    for symbol in flatten_symbols(file_entry):
        haystack = f"{symbol['name']} {symbol['signature']}".lower()
        if any(keyword in haystack for keyword in keywords):
            matches.append(symbol_summary(symbol))
    return matches[:5]


def summarize_files(files: list[dict]) -> list[dict]:
    return [
        {
            "path": file_entry["path"],
            "rank": file_entry["rank"],
            "lines": file_entry["lines"],
            "exports": file_entry.get("exports", [])[:10],
        }
        for file_entry in files
    ]


def render_architecture(payload: dict) -> list[str]:
    lines = ["", "### Entry points"]
    if payload["entry_points"]:
        for item in payload["entry_points"]:
            lines.append(f"- {item['path']} (rank: {item['rank']}, {item['lines']} lines)")
    else:
        lines.append("- None detected")

    lines.append("")
    lines.append("### Config files")
    if payload["config_files"]:
        for item in payload["config_files"]:
            lines.append(f"- {item['path']} (rank: {item['rank']})")
    else:
        lines.append("- None detected")

    lines.append("")
    lines.append("### Top ranked files")
    for item in payload["top_files"]:
        exports = f" | Exports: {format_list(item['exports'])}" if item["exports"] else ""
        lines.append(f"- {item['path']} (rank: {item['rank']}, {item['lines']} lines){exports}")

    lines.append("")
    lines.append("### Dependency summary")
    if payload["dependency_summary"]:
        for item in payload["dependency_summary"]:
            lines.append(f"- {item['path']} <- {item['count']} dependents")
    else:
        lines.append("- No internal dependency edges resolved")
    lines.append("")
    lines.append(
        f"Graph: {payload['graph_stats']['nodes']} indexed files, {payload['graph_stats']['edges']} internal dependency edges"
    )
    return lines


def render_call_chain(payload: dict) -> list[str]:
    lines = [
        "",
        f"### Direction: {payload.get('direction', 'down')} | Depth: {payload.get('depth', 0)}",
    ]
    if payload.get("reason"):
        lines.append(payload["reason"])
        return lines
    for result in payload.get("results", []):
        root = result["root"]
        lines.append(f"- {root['qualified_name']} ({root['path']}:{root['line_start']})")
        _render_call_tree_node(result["tree"], lines, level=1)
        if root.get("external_calls"):
            lines.append(f"  external: {format_list(root['external_calls'])}")
    return lines


def _render_call_tree_node(node: dict, lines: list[str], *, level: int) -> None:
    for child in node.get("children", []):
        indent = "  " * level
        if child.get("cycle"):
            lines.append(f"{indent}-> cycle to {child['id']}")
            continue
        lines.append(f"{indent}-> {child['qualified_name']} ({child['path']}:{child['line_start']})")
        if child.get("external_calls"):
            lines.append(f"{indent}   external: {format_list(child['external_calls'])}")
        _render_call_tree_node(child, lines, level=level + 1)


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _finalize_payload(payload: dict, manifest: dict, manifest_path: Path) -> dict:
    payload["stale"] = detect_stale(manifest)
    payload["index"] = manifest_path.as_posix()
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
