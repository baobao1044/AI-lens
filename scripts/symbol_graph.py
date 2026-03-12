#!/usr/bin/env python3

"""Symbol relationship graph helpers for ai-lens."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from .index_store import index_paths, load_manifest
except ImportError:
    from index_store import index_paths, load_manifest

GRAPH_FILENAME = "symbol_graph.json"
CALLABLE_TYPES = {"function", "method"}
TYPE_LIKE_TYPES = {"class", "interface", "struct", "enum", "trait", "type_alias"}
GRAPH_TYPES = CALLABLE_TYPES | TYPE_LIKE_TYPES
IDENTIFIER_RE = re.compile(r"\b([A-Za-z_][\w$]*)\s*\(")
TOKEN_RE = re.compile(r"\b[A-Za-z_][\w$]*\b")
STRING_RE = re.compile(r"('''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")")
LINE_COMMENT_RE = re.compile(r"(?m)#.*$|//.*$")
BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")


def symbol_graph_path(project_path: str | Path) -> Path:
    return index_paths(project_path)["symbol_graph"]


def build_symbol_graph(project_path: str | Path, records: dict[str, dict]) -> dict[str, Any]:
    root = index_paths(project_path)["root"]
    nodes: dict[str, dict[str, Any]] = {}
    name_index: dict[str, list[str]] = {}

    for file_entry in records.values():
        for symbol in _flatten_graph_symbols(file_entry):
            symbol_id = _symbol_id(file_entry["path"], symbol)
            node = {
                "id": symbol_id,
                "path": file_entry["path"],
                "name": symbol["name"],
                "qualified_name": symbol["qualified_name"],
                "type": symbol["type"],
                "signature": symbol["signature"],
                "line_start": symbol["line_start"],
                "line_end": symbol["line_end"],
                "calls": [],
                "called_by": [],
                "extends": [],
                "implements": [],
                "uses_type": [],
                "decorates": [],
                "external_calls": [],
            }
            nodes[symbol_id] = node
            for alias in {symbol["name"], symbol["qualified_name"], symbol_id}:
                name_index.setdefault(alias.lower(), []).append(symbol_id)

    for node in nodes.values():
        snippet = _read_snippet(root / node["path"], node["line_start"], node["line_end"])
        cleaned = _clean_code(snippet)
        node["extends"] = _resolve_signature_refs(node["signature"], name_index, prefix="extends")
        node["implements"] = _resolve_signature_refs(node["signature"], name_index, prefix="implements")
        node["uses_type"] = _resolve_type_refs(node["signature"], name_index, skip=node["id"])
        node["decorates"] = _resolve_decorators(snippet, name_index)

        if node["type"] in CALLABLE_TYPES:
            seen_external: list[str] = []
            for called_name in IDENTIFIER_RE.findall(cleaned):
                if called_name == node["name"]:
                    continue
                target = _pick_target(name_index.get(called_name.lower(), []), node["path"], node["id"])
                if target is None:
                    if called_name not in seen_external and called_name not in {"if", "for", "while", "switch", "return", "catch"}:
                        seen_external.append(called_name)
                    continue
                if target != node["id"] and target not in node["calls"]:
                    node["calls"].append(target)
            node["external_calls"] = seen_external[:25]

    for node in nodes.values():
        for target in node["calls"]:
            target_node = nodes.get(target)
            if target_node is not None and node["id"] not in target_node["called_by"]:
                target_node["called_by"].append(node["id"])

    graph = {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "project_root": str(root),
        "nodes": nodes,
        "summary": {
            "node_count": len(nodes),
            "edge_count": sum(len(node["calls"]) for node in nodes.values()),
        },
    }
    return graph


def write_symbol_graph(project_path: str | Path, graph: dict[str, Any]) -> Path:
    path = symbol_graph_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_symbol_graph(index_or_project_path: str | Path) -> tuple[dict[str, Any] | None, Path]:
    manifest, manifest_path = load_manifest(index_or_project_path)
    root = Path(manifest["project"]["root"])
    path = symbol_graph_path(root)
    if not path.exists():
        return None, path
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except json.JSONDecodeError:
        return None, path


def query_call_chain(
    index_or_project_path: str | Path,
    symbol_name: str,
    *,
    depth: int = 3,
    direction: str = "down",
    top: int = 5,
) -> dict[str, Any]:
    manifest, manifest_path = load_manifest(index_or_project_path)
    graph, graph_path = load_symbol_graph(index_or_project_path)
    payload = {
        "query": f'call chain "{symbol_name}"',
        "mode": "call_chain",
        "direction": direction,
        "depth": depth,
        "graph_path": graph_path.as_posix(),
    }
    if graph is None:
        payload["results"] = []
        payload["reason"] = "symbol graph not built"
        payload["stale"] = False
        payload["index"] = manifest_path.as_posix()
        return payload

    candidates = find_symbol_matches(graph, symbol_name, top=top)
    if not candidates:
        payload["results"] = []
        payload["reason"] = "symbol not found"
        payload["stale"] = False
        payload["index"] = manifest_path.as_posix()
        return payload

    roots = []
    for candidate in candidates:
        roots.append(
            {
                "root": _graph_symbol_summary(candidate),
                "tree": build_call_tree(graph, candidate["id"], depth=depth, direction=direction),
            }
        )
    payload["results"] = roots
    payload["stale"] = False
    payload["index"] = manifest_path.as_posix()
    return payload


def find_symbol_matches(graph: dict[str, Any], symbol_name: str, *, top: int = 5) -> list[dict[str, Any]]:
    needle = symbol_name.lower()
    matches: list[tuple[float, dict[str, Any]]] = []
    for node in graph.get("nodes", {}).values():
        name = node["name"].lower()
        qualified = node["qualified_name"].lower()
        if name == needle or qualified.endswith(f".{needle}") or node["id"].lower().endswith(f"::{needle}"):
            score = 100.0
        elif needle in qualified or needle in name:
            score = 50.0
        else:
            continue
        matches.append((score, node))
    matches.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["qualified_name"]))
    return [node for _, node in matches[:top]]


def build_call_tree(
    graph: dict[str, Any],
    root_id: str,
    *,
    depth: int,
    direction: str = "down",
    _seen: set[str] | None = None,
) -> dict[str, Any]:
    nodes = graph.get("nodes", {})
    node = nodes.get(root_id)
    if node is None:
        return {}
    seen = set(_seen or set())
    seen.add(root_id)
    edge_name = "calls" if direction == "down" else "called_by"
    result = _graph_symbol_summary(node)
    if depth <= 0:
        result["children"] = []
        return result
    children = []
    for child_id in node.get(edge_name, []):
        if child_id in seen:
            children.append({"id": child_id, "cycle": True})
            continue
        children.append(
            build_call_tree(graph, child_id, depth=depth - 1, direction=direction, _seen=seen)
        )
    result["children"] = children
    return result


def _flatten_graph_symbols(file_entry: dict) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for symbol in file_entry.get("symbols", []):
        if symbol["type"] not in GRAPH_TYPES:
            continue
        parent_name = symbol["name"]
        item = dict(symbol)
        item["qualified_name"] = parent_name
        items.append(item)
        for child in symbol.get("children", []):
            if child["type"] not in GRAPH_TYPES:
                continue
            child_item = dict(child)
            child_item["parent"] = parent_name
            child_item["qualified_name"] = f"{parent_name}.{child['name']}"
            items.append(child_item)
    return items


def _symbol_id(path: str, symbol: dict[str, Any]) -> str:
    qualified = symbol.get("qualified_name") or symbol["name"]
    return f"{path}::{qualified}"


def _read_snippet(path: Path, line_start: int, line_end: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(line_start - 1, 0)
    end = max(line_end, line_start)
    return "\n".join(lines[start:end])


def _clean_code(text: str) -> str:
    text = BLOCK_COMMENT_RE.sub(" ", text)
    text = LINE_COMMENT_RE.sub(" ", text)
    text = STRING_RE.sub(" ", text)
    return text


def _resolve_signature_refs(signature: str, name_index: dict[str, list[str]], *, prefix: str) -> list[str]:
    match = re.search(rf"\b{prefix}\s+([A-Za-z_][\w$<>,\s.]*)", signature)
    if not match:
        return []
    values = []
    for token in TOKEN_RE.findall(match.group(1)):
        values.extend(name_index.get(token.lower(), []))
    return sorted(set(values))


def _resolve_type_refs(signature: str, name_index: dict[str, list[str]], *, skip: str) -> list[str]:
    refs = []
    for token in TOKEN_RE.findall(signature):
        for candidate in name_index.get(token.lower(), []):
            if candidate != skip:
                refs.append(candidate)
    return sorted(set(refs))


def _resolve_decorators(snippet: str, name_index: dict[str, list[str]]) -> list[str]:
    refs = []
    for line in snippet.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@"):
            continue
        token = stripped[1:].split("(", 1)[0].strip()
        refs.extend(name_index.get(token.lower(), []))
    return sorted(set(refs))


def _pick_target(candidates: list[str], current_path: str, current_id: str) -> str | None:
    if not candidates:
        return None
    same_file = [candidate for candidate in candidates if candidate.startswith(f"{current_path}::")]
    ordered = same_file or candidates
    ordered = sorted(ordered, key=lambda item: (item == current_id, item.count("."), item))
    candidate = ordered[0]
    return None if candidate == current_id else candidate


def _graph_symbol_summary(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node["id"],
        "path": node["path"],
        "name": node["name"],
        "qualified_name": node["qualified_name"],
        "type": node["type"],
        "signature": node["signature"],
        "line_start": node["line_start"],
        "line_end": node["line_end"],
        "external_calls": node.get("external_calls", []),
    }


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
