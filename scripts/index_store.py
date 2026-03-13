#!/usr/bin/env python3

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

INDEX_DIRNAME = ".ai-lens"
MANIFEST_FILENAME = "manifest.json"
SNAPSHOT_FILENAME = ".ai-lens.json"

_MANIFEST_CACHE: dict[tuple[str, int], dict] = {}
_CACHE_LOCK = threading.Lock()
_SCAN_LOCKS: dict[str, threading.Lock] = {}
_SCAN_LOCKS_LIMIT = 64


def load_json(path: Path) -> dict | None:
    """Load a JSON file, returning None if missing or invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: dict) -> None:
    """Write a dict as pretty-printed JSON to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_project_path(project_path: str | Path) -> Path:
    path = Path(project_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Project root not found: {path}")
    return path


def index_paths(project_path: str | Path) -> dict[str, Path]:
    root = normalize_project_path(project_path)
    index_dir = root / INDEX_DIRNAME
    return {
        "root": root,
        "index_dir": index_dir,
        "manifest": index_dir / MANIFEST_FILENAME,
        "snapshot": root / SNAPSHOT_FILENAME,
        "files_dir": index_dir / "files",
        "symbol_graph": index_dir / "symbol_graph.json",
        "semantic_dir": index_dir / "semantic",
    }


def resolve_index_path(index_or_project_path: str | Path) -> Path:
    path = Path(index_or_project_path).expanduser().resolve()
    if path.is_dir():
        manifest = path / INDEX_DIRNAME / MANIFEST_FILENAME
        if manifest.exists():
            return manifest
        if path.name == INDEX_DIRNAME and (path / MANIFEST_FILENAME).exists():
            return path / MANIFEST_FILENAME
        snapshot = path / SNAPSHOT_FILENAME
        if snapshot.exists():
            return snapshot
    if path.is_file():
        return path
    raise FileNotFoundError(f"Could not resolve index path from {path}")


def load_manifest(index_or_project_path: str | Path, *, use_cache: bool = True) -> tuple[dict, Path]:
    manifest_path = resolve_index_path(index_or_project_path)
    stat = manifest_path.stat()
    cache_key = (str(manifest_path), stat.st_mtime_ns)
    with _CACHE_LOCK:
        if use_cache and cache_key in _MANIFEST_CACHE:
            return _MANIFEST_CACHE[cache_key], manifest_path

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if use_cache:
        with _CACHE_LOCK:
            _MANIFEST_CACHE.clear()
            _MANIFEST_CACHE[cache_key] = manifest
    return manifest, manifest_path


def detect_stale(manifest: dict) -> bool:
    root_value = manifest.get("project", {}).get("root")
    generated_ns = manifest.get("generated_at_ns", 0)
    if not root_value or not generated_ns:
        return False
    root = Path(root_value)
    if not root.exists():
        return False
    for file_entry in manifest.get("files", []):
        absolute = root / file_entry["path"]
        if not absolute.exists():
            return True
        try:
            if absolute.stat().st_mtime_ns > generated_ns:
                return True
        except OSError:
            return True
    return False


def ensure_index(
    project_path: str | Path,
    *,
    force: bool = False,
    scan_kwargs: dict | None = None,
    scan_project_func: Callable[..., dict] | None = None,
) -> tuple[dict, Path, bool]:
    paths = index_paths(project_path)
    manifest_path = paths["manifest"]
    lock = _SCAN_LOCKS.setdefault(str(paths["root"]), threading.Lock())
    with lock:
        refreshed = force or not manifest_path.exists()
        manifest: dict | None = None

        if not refreshed:
            manifest, _ = load_manifest(paths["root"])
            refreshed = detect_stale(manifest)

        if refreshed:
            if scan_project_func is None:
                from scan import scan_project  # lazy import to avoid circular import

                scan_project_func = scan_project
            kwargs = {"force": force, "quiet": True}
            if scan_kwargs:
                kwargs.update(scan_kwargs)
            manifest = scan_project_func(str(paths["root"]), **kwargs)
            _MANIFEST_CACHE.clear()
            manifest_path = index_paths(paths["root"])["manifest"]
        elif manifest is None:
            manifest, manifest_path = load_manifest(paths["root"])

        return manifest, manifest_path, refreshed


def render_tree(tree: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for parent, children in tree.items():
        lines.append(parent)
        for child in children:
            lines.append(f"  - {child}")
    return "\n".join(lines)


def normalize_indexed_path(path: str | Path) -> str:
    return Path(path).as_posix().lstrip("./")


def read_symbol_range(
    project_path: str | Path,
    file_path: str | Path,
    symbol_name: str,
    *,
    manifest: dict | None = None,
) -> dict | None:
    root = normalize_project_path(project_path)
    if manifest is None:
        manifest, _ = load_manifest(root)
    normalized_file = normalize_indexed_path(file_path)
    needle = symbol_name.lower()

    for file_entry in manifest.get("files", []):
        if file_entry["path"] != normalized_file:
            continue
        for symbol in flatten_symbols(file_entry):
            if symbol["name"].lower() == needle or needle in symbol["name"].lower():
                absolute = root / normalized_file
                lines = absolute.read_text(encoding="utf-8", errors="replace").splitlines()
                start = max(symbol["line_start"] - 1, 0)
                end = max(symbol["line_end"], symbol["line_start"])
                snippet = "\n".join(lines[start:end])
                return {
                    "path": normalized_file,
                    "language": file_entry["language"],
                    "symbol": symbol_summary(symbol),
                    "content": snippet,
                    "line_start": symbol["line_start"],
                    "line_end": symbol["line_end"],
                }
        return None
    return None


def flatten_symbols(file_entry: dict) -> list[dict]:
    items: list[dict] = []
    for symbol in file_entry.get("symbols", []):
        items.append(symbol)
        for child in symbol.get("children", []):
            child_copy = dict(child)
            child_copy.setdefault("parent", symbol["name"])
            items.append(child_copy)
    return items


def symbol_summary(symbol: dict) -> dict:
    payload = {
        "type": symbol["type"],
        "name": symbol["name"],
        "signature": symbol["signature"],
        "line_start": symbol["line_start"],
        "line_end": symbol["line_end"],
    }
    if symbol.get("doc"):
        payload["doc"] = symbol["doc"]
    if symbol.get("parent"):
        payload["parent"] = symbol["parent"]
    return payload
