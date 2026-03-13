#!/usr/bin/env python3

"""Change impact analysis for ai-lens.

Given a list of changed files, determines:
- Direct dependents (files that import the changed files)
- Transitive dependents (BFS through dependency graph)
- Affected symbols (symbols in dependents that reference changed symbols)
- Risk score based on depth and breadth of impact
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

try:
    from .index_store import load_manifest, normalize_indexed_path
except ImportError:
    from index_store import load_manifest, normalize_indexed_path


def analyse_impact(
    index_or_project_path: str | Path,
    changed_files: list[str],
    *,
    max_depth: int = 5,
) -> dict[str, Any]:
    """Analyse the impact of changing specific files.

    Returns direct dependents, transitive dependents, and a risk score.
    """
    manifest, _manifest_path = load_manifest(index_or_project_path)
    project_root = manifest.get("project", {}).get("root", "")
    deps_tree = manifest.get("dependency_tree", {})

    # Normalise changed file paths
    normalised_changed = {normalize_indexed_path(f) for f in changed_files}

    # Build a reverse dependency graph (file → who depends on it)
    reverse_deps: dict[str, set[str]] = {}
    for parent, children in deps_tree.items():
        if isinstance(children, list):
            for child in children:
                reverse_deps.setdefault(child, set()).add(parent)

    # BFS to find all transitive dependents
    direct_dependents: set[str] = set()
    transitive_dependents: set[str] = set()
    depth_map: dict[str, int] = {}  # file → shortest depth from changed files

    queue: deque[tuple[str, int]] = deque()
    for changed in normalised_changed:
        queue.append((changed, 0))
        depth_map[changed] = 0

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue

        for dependent in reverse_deps.get(current, set()):
            if dependent in normalised_changed:
                continue  # Skip the changed files themselves
            if dependent not in depth_map or depth + 1 < depth_map[dependent]:
                depth_map[dependent] = depth + 1
                queue.append((dependent, depth + 1))
                if depth == 0:
                    direct_dependents.add(dependent)
                transitive_dependents.add(dependent)

    # Build impact layers (grouped by depth)
    layers: dict[int, list[str]] = {}
    for dep, depth in sorted(depth_map.items(), key=lambda x: x[1]):
        if dep not in normalised_changed:
            layers.setdefault(depth, []).append(dep)

    # Calculate risk score
    # Factors: number of direct dependents, depth of impact, total affected files
    total_affected = len(transitive_dependents)
    max_depth_reached = max(depth_map.values()) if depth_map else 0
    direct_count = len(direct_dependents)

    if total_affected == 0:
        risk_score = 0.0
        risk_level = "none"
    elif total_affected <= 2 and max_depth_reached <= 1:
        risk_score = round(0.1 + 0.1 * total_affected, 2)
        risk_level = "low"
    elif total_affected <= 5 and max_depth_reached <= 2:
        risk_score = round(0.3 + 0.05 * total_affected + 0.1 * max_depth_reached, 2)
        risk_level = "medium"
    else:
        risk_score = min(round(0.5 + 0.03 * total_affected + 0.1 * max_depth_reached, 2), 1.0)
        risk_level = "high"

    # Get file metadata for affected files
    file_lookup = {f["path"]: f for f in manifest.get("files", [])}
    affected_details: list[dict[str, Any]] = []
    for dep in sorted(transitive_dependents):
        entry = file_lookup.get(dep, {})
        affected_details.append({
            "path": dep,
            "depth": depth_map.get(dep, 0),
            "is_direct": dep in direct_dependents,
            "language": entry.get("language", "unknown"),
            "rank": entry.get("rank", 0),
        })

    affected_details.sort(key=lambda x: (x["depth"], -x["rank"]))

    return {
        "mode": "impact_analysis",
        "project": project_root,
        "changed_files": sorted(normalised_changed),
        "direct_dependents_count": direct_count,
        "transitive_dependents_count": total_affected,
        "max_depth_reached": max_depth_reached,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "layers": {str(k): v for k, v in sorted(layers.items())},
        "affected_files": affected_details,
    }


def format_impact_report(result: dict[str, Any]) -> str:
    """Format impact analysis results as a human-readable report."""
    lines: list[str] = []
    lines.append(f"Impact Analysis — {result['project']}")
    lines.append(f"Changed: {', '.join(result['changed_files'])}")
    lines.append(
        f"Risk: {result['risk_level'].upper()} ({result['risk_score']:.0%})  |  "
        f"Direct: {result['direct_dependents_count']}  |  "
        f"Total affected: {result['transitive_dependents_count']}  |  "
        f"Max depth: {result['max_depth_reached']}"
    )
    lines.append("")

    if result["affected_files"]:
        lines.append("## Affected Files")
        for f in result["affected_files"]:
            marker = "⬆ DIRECT" if f["is_direct"] else f"  depth={f['depth']}"
            lines.append(f"  {f['path']}  ({f['language']})  {marker}")
        lines.append("")

    if result["layers"]:
        lines.append("## Impact Layers")
        for depth, files in sorted(result["layers"].items()):
            lines.append(f"  Layer {depth}: {', '.join(files)}")

    if not result["affected_files"]:
        lines.append("✅ No downstream impact detected.")

    return "\n".join(lines)
