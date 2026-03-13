#!/usr/bin/env python3

"""Dead code detection for ai-lens.

Analyses the manifest and symbol graph to find:
- Exported symbols never imported by other files
- Functions/classes never called (empty called_by in symbol graph)
- Files never depended on (leaf nodes in dependency graph)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from .index_store import flatten_symbols, load_json, load_manifest
except ImportError:
    from index_store import flatten_symbols, load_json, load_manifest


def detect_dead_code(
    index_or_project_path: str | Path,
    *,
    include_tests: bool = False,
    min_confidence: float = 0.5,
) -> dict[str, Any]:
    """Detect potentially dead code in an indexed project.

    Returns a dict with dead_symbols, dead_files, and summary statistics.
    """
    manifest, manifest_path = load_manifest(index_or_project_path)
    project_root = manifest.get("project", {}).get("root", "")

    # Load symbol graph if available
    sg_path = manifest_path.parent / "symbol_graph.json"
    symbol_graph = load_json(sg_path) or {}
    symbols_in_graph = symbol_graph.get("symbols", {})

    files = manifest.get("files", [])
    deps_tree = manifest.get("dependency_tree", {})

    # Build sets of imported/depended-on files
    all_depended_files: set[str] = set()
    for _parent, children in deps_tree.items():
        if isinstance(children, list):
            all_depended_files.update(children)

    # Build set of called/referenced symbols
    called_symbols: set[str] = set()
    for _sym_name, sym_info in symbols_in_graph.items():
        if isinstance(sym_info, dict):
            for callee in sym_info.get("calls", []):
                called_symbols.add(callee)

    # Analyse each file
    dead_symbols: list[dict[str, Any]] = []
    dead_files: list[dict[str, Any]] = []

    for file_entry in files:
        file_path = file_entry.get("path", "")

        # Skip test files unless requested
        is_test = any(
            seg in file_path.lower()
            for seg in ("test_", "_test.", "tests/", "test/", "spec/", "__tests__")
        )
        if is_test and not include_tests:
            continue

        # Detect dead files (no dependents)
        if file_path not in all_depended_files:
            # Entrypoints and configs are expected to have no dependents
            is_entrypoint = file_entry.get("rank", 0) >= 8.0
            is_config = file_entry.get("language") == "config"
            if not is_entrypoint and not is_config:
                confidence = 0.6
                # Higher confidence if file has exports but nothing imports them
                exports = file_entry.get("exports", [])
                if exports:
                    confidence = 0.8
                if confidence >= min_confidence:
                    dead_files.append({
                        "path": file_path,
                        "language": file_entry.get("language", "unknown"),
                        "confidence": round(confidence, 2),
                        "reason": "no other file depends on this file",
                        "exports": exports[:5],  # first 5 exports as context
                    })

        # Detect dead symbols
        for symbol in flatten_symbols(file_entry):
            sym_name = symbol.get("name", "")
            sym_type = symbol.get("type", "")
            sym_fqn = f"{file_path}::{sym_name}"

            # Skip private/dunder symbols
            if sym_name.startswith("_"):
                continue

            # Check if symbol is in graph and has callers
            graph_entry = symbols_in_graph.get(sym_fqn) or symbols_in_graph.get(sym_name, {})
            if isinstance(graph_entry, dict):
                called_by = graph_entry.get("called_by", [])
                if not called_by and sym_name not in called_symbols:
                    confidence = 0.5
                    if sym_type == "function":
                        confidence = 0.7
                    if sym_type == "class":
                        confidence = 0.6
                    # Main entrypoints are not dead
                    if sym_name in ("main", "__main__", "app", "cli"):
                        continue
                    if confidence >= min_confidence:
                        dead_symbols.append({
                            "path": file_path,
                            "name": sym_name,
                            "type": sym_type,
                            "line_start": symbol.get("line_start", 0),
                            "line_end": symbol.get("line_end", 0),
                            "confidence": round(confidence, 2),
                            "reason": "symbol not called or referenced by other symbols",
                        })

    # Sort by confidence descending
    dead_symbols.sort(key=lambda x: x["confidence"], reverse=True)
    dead_files.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "mode": "dead_code",
        "project": project_root,
        "total_files_analysed": len(files),
        "total_symbols_analysed": sum(
            len(flatten_symbols(f)) for f in files
        ),
        "dead_files": dead_files,
        "dead_files_count": len(dead_files),
        "dead_symbols": dead_symbols,
        "dead_symbols_count": len(dead_symbols),
        "include_tests": include_tests,
        "min_confidence": min_confidence,
    }


def format_dead_code_report(result: dict[str, Any]) -> str:
    """Format dead code detection results as a human-readable report."""
    lines: list[str] = []
    lines.append(f"Dead Code Analysis — {result['project']}")
    lines.append(f"Files analysed: {result['total_files_analysed']}  |  Symbols analysed: {result['total_symbols_analysed']}")
    lines.append("")

    if result["dead_files"]:
        lines.append(f"## Dead Files ({result['dead_files_count']})")
        for f in result["dead_files"]:
            lines.append(f"  [{f['confidence']:.0%}] {f['path']}  — {f['reason']}")
        lines.append("")

    if result["dead_symbols"]:
        lines.append(f"## Dead Symbols ({result['dead_symbols_count']})")
        for s in result["dead_symbols"]:
            loc = f"L{s['line_start']}-{s['line_end']}" if s.get("line_start") else ""
            lines.append(f"  [{s['confidence']:.0%}] {s['path']}::{s['name']} ({s['type']}) {loc}")
            lines.append(f"        → {s['reason']}")
        lines.append("")

    if not result["dead_files"] and not result["dead_symbols"]:
        lines.append("✅ No dead code detected.")

    return "\n".join(lines)
