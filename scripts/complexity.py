#!/usr/bin/env python3

"""Complexity metrics for ai-lens.

Calculates:
- Cyclomatic complexity per function (counting branches)
- Lines of code (LOC) per function
- Parameter count per function
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    from .index_store import flatten_symbols, load_manifest
except ImportError:
    from index_store import flatten_symbols, load_manifest


# Branch keywords by language
_BRANCH_PATTERNS: dict[str, list[str]] = {
    "python": [r"\bif\b", r"\belif\b", r"\bfor\b", r"\bwhile\b", r"\bexcept\b", r"\band\b", r"\bor\b"],
    "javascript": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b", r"\bcatch\b", r"\bcase\b", r"\b&&\b", r"\b\|\|\b", r"\?\s*"],
    "typescript": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b", r"\bcatch\b", r"\bcase\b", r"\b&&\b", r"\b\|\|\b", r"\?\s*"],
    "rust": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b", r"\bmatch\b", r"\b&&\b", r"\b\|\|\b"],
    "go": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bcase\b", r"\b&&\b", r"\b\|\|\b"],
    "java": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b", r"\bcatch\b", r"\bcase\b", r"\b&&\b", r"\b\|\|\b"],
    "c": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b", r"\bcase\b", r"\b&&\b", r"\b\|\|\b"],
    "cpp": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b", r"\bcatch\b", r"\bcase\b", r"\b&&\b", r"\b\|\|\b"],
}


def _count_branches(source_lines: list[str], language: str) -> int:
    """Count branch points in source code for cyclomatic complexity."""
    patterns = _BRANCH_PATTERNS.get(language, _BRANCH_PATTERNS.get("python", []))
    count = 0
    for line in source_lines:
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        for pattern in patterns:
            count += len(re.findall(pattern, line))
    return count


def _count_params(signature: str) -> int:
    """Count parameters from a function signature string."""
    if not signature:
        return 0
    # Find content inside parentheses
    paren_match = re.search(r"\(([^)]*)\)", signature)
    if not paren_match:
        return 0
    params_str = paren_match.group(1).strip()
    if not params_str:
        return 0
    # Split by comma, filter out self/cls for Python
    params = [p.strip() for p in params_str.split(",")]
    params = [p for p in params if p and p not in ("self", "cls")]
    return len(params)


def calculate_complexity(
    index_or_project_path: str | Path,
    *,
    max_results: int = 50,
    sort_by: str = "cyclomatic",  # cyclomatic | loc | params
) -> dict[str, Any]:
    """Calculate complexity metrics for all symbols in an indexed project.

    Returns a dict with per-symbol metrics and overall summary.
    """
    manifest, _manifest_path = load_manifest(index_or_project_path)
    project_root = Path(manifest.get("project", {}).get("root", ""))

    results: list[dict[str, Any]] = []
    total_complexity = 0
    total_loc = 0
    total_functions = 0

    for file_entry in manifest.get("files", []):
        file_path = file_entry.get("path", "")
        language = file_entry.get("language", "unknown")
        absolute = project_root / file_path

        # Try to read file content
        try:
            if absolute.exists():
                all_lines = absolute.read_text(encoding="utf-8", errors="replace").splitlines()
            else:
                all_lines = []
        except OSError:
            all_lines = []

        for symbol in flatten_symbols(file_entry):
            sym_type = symbol.get("type", "")
            if sym_type not in ("function", "method"):
                continue

            total_functions += 1
            line_start = symbol.get("line_start", 0)
            line_end = symbol.get("line_end", line_start)
            loc = max(line_end - line_start + 1, 1)
            total_loc += loc

            # Extract source lines for this symbol
            if all_lines and line_start > 0:
                symbol_lines = all_lines[max(line_start - 1, 0):line_end]
            else:
                symbol_lines = []

            cyclomatic = 1 + _count_branches(symbol_lines, language)
            total_complexity += cyclomatic

            params = _count_params(symbol.get("signature", ""))

            results.append({
                "path": file_path,
                "name": symbol.get("name", ""),
                "type": sym_type,
                "line_start": line_start,
                "line_end": line_end,
                "loc": loc,
                "cyclomatic": cyclomatic,
                "params": params,
                "language": language,
            })

    # Sort results
    sort_key = sort_by if sort_by in ("cyclomatic", "loc", "params") else "cyclomatic"
    results.sort(key=lambda x: x[sort_key], reverse=True)
    top_results = results[:max_results]

    avg_complexity = round(total_complexity / total_functions, 2) if total_functions else 0
    avg_loc = round(total_loc / total_functions, 2) if total_functions else 0

    return {
        "mode": "complexity",
        "project": str(project_root),
        "total_functions": total_functions,
        "average_cyclomatic": avg_complexity,
        "average_loc": avg_loc,
        "total_complexity": total_complexity,
        "hotspots": top_results,
        "sort_by": sort_key,
    }


def format_complexity_report(result: dict[str, Any]) -> str:
    """Format complexity metrics as a human-readable report."""
    lines: list[str] = []
    lines.append(f"Complexity Analysis — {result['project']}")
    lines.append(
        f"Functions: {result['total_functions']}  |  "
        f"Avg complexity: {result['average_cyclomatic']}  |  "
        f"Avg LOC: {result['average_loc']}"
    )
    lines.append("")

    if result["hotspots"]:
        lines.append(f"## Top Hotspots (sorted by {result['sort_by']})")
        for h in result["hotspots"]:
            lines.append(
                f"  {h['path']}::{h['name']}  "
                f"CC={h['cyclomatic']}  LOC={h['loc']}  params={h['params']}  "
                f"L{h['line_start']}-{h['line_end']}"
            )

    return "\n".join(lines)
