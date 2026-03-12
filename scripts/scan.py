#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

try:
    from .index_store import index_paths
    from .parsers import ParserUnavailable, parse_with_fallback, parse_with_tree_sitter
    from .parsers.tree_sitter import available_languages as tree_sitter_available_languages
    from .symbol_graph import build_symbol_graph, write_symbol_graph
except ImportError:
    from index_store import index_paths
    from parsers import ParserUnavailable, parse_with_fallback, parse_with_tree_sitter
    from parsers.tree_sitter import available_languages as tree_sitter_available_languages
    from symbol_graph import build_symbol_graph, write_symbol_graph

SCHEMA_VERSION = 1
FILE_TIMEOUT_SECONDS = 5
DEFAULT_MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {
    ".ai-lens",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".turbo",
    ".idea",
    ".vscode",
}
SKIP_FILES = {".ai-lens.json"}
SOURCE_LANGUAGES = {"python", "javascript", "typescript", "rust", "go", "java", "c", "cpp"}
ENTRYPOINT_FILES = {
    "main.py",
    "app.py",
    "server.py",
    "manage.py",
    "index.js",
    "index.ts",
    "main.ts",
    "app.ts",
    "server.ts",
    "main.rs",
    "lib.rs",
    "main.go",
    "Main.java",
    "main.c",
    "main.cpp",
}
CONFIG_BONUS_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "poetry.lock",
    "requirements.txt",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "tsconfig.json",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "openai.yaml",
}
README_FILES = {"README.md", "README.txt", "ARCHITECTURE.md", "docs"}
EXTENSION_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".md": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".sh": "shell",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan a project and build the ai-lens index.")
    parser.add_argument("root", nargs="?", default=".", help="Project root to scan.")
    parser.add_argument("--force", action="store_true", help="Ignore fingerprints and rebuild every indexed file.")
    parser.add_argument("--full-dump", action="store_true", help="Also write a merged .ai-lens.json snapshot.")
    parser.add_argument("--no-tree-sitter", action="store_true", help="Disable tree-sitter and use fallback parsers only.")
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES, help="Skip files larger than this size.")
    parser.add_argument("--workers", type=int, default=max(2, min(8, (os.cpu_count() or 4))), help="Worker count for parsing files.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = scan_project(
        args.root,
        force=args.force,
        full_dump=args.full_dump,
        no_tree_sitter=args.no_tree_sitter,
        max_file_bytes=args.max_file_bytes,
        workers=args.workers,
        quiet=True,
    )
    if args.json:
        print(json.dumps(manifest["stats"], indent=2))
    else:
        print(format_scan_summary(manifest, wrote_dump=args.full_dump))
    return 0


def scan_project(
    project_path: str,
    *,
    force: bool = False,
    changed_files: list[str] | None = None,
    full_dump: bool = False,
    no_tree_sitter: bool = False,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    workers: int | None = None,
    quiet: bool = False,
) -> dict:
    workers = workers or max(2, min(8, (os.cpu_count() or 4)))
    paths = index_paths(project_path)
    root = paths["root"]
    paths["files_dir"].mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    old_manifest = load_json(paths["manifest"])
    if old_manifest and old_manifest.get("schema_version") != SCHEMA_VERSION:
        old_manifest = None

    parser_status = (
        {language: False for language in SOURCE_LANGUAGES}
        if no_tree_sitter
        else tree_sitter_available_languages()
    )
    gitignore_patterns = read_gitignore(root)
    discovered = discover_files(root, gitignore_patterns, max_file_bytes)
    discovered_paths = {item["path"] for item in discovered}
    old_files = {item["path"]: item for item in (old_manifest or {}).get("files", [])}
    changed_hints = normalize_changed_hints(root, changed_files)

    changed, reused = partition_files(discovered, old_files, force, changed_hints)
    records = scan_changed_files(root, changed, parser_status, no_tree_sitter, workers)
    records.update(reused)

    deleted_paths = sorted(set(old_files) - discovered_paths)
    decorate_records(root, records)
    dependency_graph = build_dependency_graph(records)
    rank_records(records, dependency_graph)
    tree = build_tree(records)
    symbol_graph = build_symbol_graph(root, records)
    write_symbol_graph(root, symbol_graph)

    sorted_records = sorted(records.values(), key=lambda item: (-item["rank"], item["path"]))
    for record in sorted_records:
        cache_name = f"{hashlib.sha1(record['path'].encode('utf-8')).hexdigest()}.json"
        record["cache_path"] = f".ai-lens/files/{cache_name}"
        write_json(root / record["cache_path"], record)

    cleanup_deleted_caches(root, old_files, deleted_paths)

    language_histogram = histogram(
        item["language"] for item in sorted_records if item["language"] in SOURCE_LANGUAGES
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "generated_at_ns": time.time_ns(),
        "project": {
            "name": root.name,
            "root": str(root),
            "primary_language": choose_primary_language(language_histogram),
            "languages": language_histogram,
            "total_files": len(sorted_records),
        },
        "settings": {
            "max_file_bytes": max_file_bytes,
            "tree_sitter_enabled": not no_tree_sitter,
        },
        "parser_availability": parser_status,
        "tree": tree,
        "files": sorted_records,
        "dependency_graph": dependency_graph,
        "symbol_graph": {
            "path": paths["symbol_graph"].as_posix(),
            "summary": symbol_graph.get("summary", {}),
        },
        "stats": {
            "changed_files": len(changed),
            "reused_files": len(reused),
            "deleted_files": len(deleted_paths),
            "scan_seconds": round(time.perf_counter() - started, 3),
            "max_source_mtime_ns": max((item["fingerprint"]["mtime_ns"] for item in sorted_records), default=0),
            "snapshot_written": full_dump,
            "symbol_graph_nodes": symbol_graph.get("summary", {}).get("node_count", 0),
        },
        "warnings": collect_manifest_warnings(sorted_records),
    }
    write_json(paths["manifest"], manifest)
    if full_dump:
        write_json(paths["snapshot"], manifest)
    if not quiet:
        print(format_scan_summary(manifest, wrote_dump=full_dump))
    return manifest


def format_scan_summary(manifest: dict, *, wrote_dump: bool | None = None) -> str:
    project = manifest["project"]
    stats = manifest["stats"]
    root = Path(project["root"])
    wrote_dump = stats.get("snapshot_written") if wrote_dump is None else wrote_dump
    lines = [
        f"Indexed {project['total_files']} files in {stats['scan_seconds']}s",
        f"Primary language: {project['primary_language'] or 'unknown'}",
        f"Manifest: {(root / '.ai-lens' / 'manifest.json').as_posix()}",
    ]
    if wrote_dump:
        lines.append(f"Snapshot: {(root / '.ai-lens.json').as_posix()}")
    if manifest["warnings"]:
        lines.append(f"Warnings: {len(manifest['warnings'])} (showing in manifest)")
    return "\n".join(lines)


def discover_files(root: Path, gitignore_patterns: list[str], max_file_bytes: int) -> list[dict]:
    results: list[dict] = []
    for current_root, dirnames, filenames in os.walk(root, topdown=True):
        current_path = Path(current_root)
        rel_dir = current_path.relative_to(root).as_posix() if current_path != root else ""
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS and not is_ignored(join_rel(rel_dir, name), gitignore_patterns, is_dir=True)
        ]
        for filename in filenames:
            if filename in SKIP_FILES:
                continue
            rel_path = join_rel(rel_dir, filename)
            if is_ignored(rel_path, gitignore_patterns, is_dir=False):
                continue
            absolute = root / rel_path
            try:
                stat = absolute.stat()
            except OSError:
                continue
            if stat.st_size > max_file_bytes:
                continue
            language = detect_language(Path(rel_path))
            if language is None:
                continue
            results.append(
                {
                    "path": rel_path,
                    "abs_path": str(absolute),
                    "language": language,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    return sorted(results, key=lambda item: item["path"])


def partition_files(
    discovered: list[dict], old_files: dict[str, dict], force: bool, changed_hints: set[str] | None = None
) -> tuple[list[dict], dict[str, dict]]:
    changed: list[dict] = []
    reused: dict[str, dict] = {}
    for item in discovered:
        old = old_files.get(item["path"])
        if force or old is None or (changed_hints and item["path"] in changed_hints):
            item["sha1"] = file_sha1(Path(item["abs_path"]))
            changed.append(item)
            continue
        old_fp = old.get("fingerprint", {})
        if old_fp.get("size") == item["size"] and old_fp.get("mtime_ns") == item["mtime_ns"]:
            reused[item["path"]] = old
            continue
        item["sha1"] = file_sha1(Path(item["abs_path"]))
        if old_fp.get("sha1") == item["sha1"]:
            cloned = dict(old)
            cloned["fingerprint"] = {
                "size": item["size"],
                "mtime_ns": item["mtime_ns"],
                "sha1": item["sha1"],
            }
            reused[item["path"]] = cloned
        else:
            changed.append(item)
    return changed, reused


def scan_changed_files(
    root: Path,
    changed: list[dict],
    parser_status: dict[str, bool],
    no_tree_sitter: bool,
    workers: int,
) -> dict[str, dict]:
    records: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(parse_record, root, item, parser_status, no_tree_sitter): item["path"]
            for item in changed
        }
        for future, path in futures.items():
            try:
                record = future.result(timeout=FILE_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                item = next(candidate for candidate in changed if candidate["path"] == path)
                record = timeout_record(item)
            records[path] = record
    return records


def parse_record(root: Path, item: dict, parser_status: dict[str, bool], no_tree_sitter: bool) -> dict:
    absolute = root / item["path"]
    text = absolute.read_text(encoding="utf-8", errors="replace")
    lines = text.count("\n") + (0 if not text else 1)
    warnings: list[str] = []
    symbols: list[dict] = []
    parse_engine = "none"
    if item["language"] in SOURCE_LANGUAGES:
        if not no_tree_sitter and parser_status.get(item["language"], False):
            try:
                symbols = parse_with_tree_sitter(str(absolute), item["language"])
                parse_engine = "tree-sitter"
            except (ParserUnavailable, Exception) as exc:  # pragma: no cover - integration dependent
                warnings.append(f"tree-sitter fallback: {exc}")
        if not symbols:
            symbols = parse_with_fallback(str(absolute), item["language"])
            parse_engine = "fallback"

    record = {
        "path": item["path"],
        "language": item["language"],
        "lines": lines,
        "size_bytes": item["size"],
        "parse_engine": parse_engine,
        "fingerprint": {
            "size": item["size"],
            "mtime_ns": item["mtime_ns"],
            "sha1": item.get("sha1") or file_sha1(absolute),
        },
        "symbols": symbols,
        "imports": extract_imports(item["language"], symbols),
        "exports": extract_exports(item["language"], symbols),
        "resolved_dependencies": [],
        "rank_inputs": {},
        "rank": 0.0,
        "warnings": warnings,
    }
    if item["language"] in {"markdown", "json", "toml", "yaml", "xml"}:
        preview = first_nonempty_line(text)
        if preview:
            record["summary"] = preview[:120]
    return record


def decorate_records(root: Path, records: dict[str, dict]) -> None:
    package_main = read_package_main(root)
    cargo_targets = read_cargo_targets(root)
    for record in records.values():
        record["resolved_dependencies"] = []
        record["rank_inputs"] = {
            "entrypoint_bonus": entrypoint_bonus(record["path"], package_main, cargo_targets),
            "config_bonus": 5.0 if is_config_or_schema(record["path"]) else 0.0,
            "docs_bonus": 3.0 if is_doc_file(record["path"]) else 0.0,
            "size_penalty": size_penalty(record["lines"]),
            "test_penalty": -3.0 if is_test_file(record["path"]) else 0.0,
            "export_bonus": round(0.5 * len(record.get("exports", [])), 2),
            "import_frequency": 0.0,
        }


def build_dependency_graph(records: dict[str, dict]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    known_paths = set(records)
    for path, record in records.items():
        resolved = []
        for import_name in record.get("imports", []):
            target = resolve_import(path, import_name, record["language"], known_paths)
            if target:
                resolved.append(target)
        deduped = sorted(set(resolved))
        record["resolved_dependencies"] = deduped
        graph[path] = deduped
    return graph


def rank_records(records: dict[str, dict], dependency_graph: dict[str, list[str]]) -> None:
    import_frequency: dict[str, int] = {path: 0 for path in records}
    for dependencies in dependency_graph.values():
        for dependency in dependencies:
            if dependency in import_frequency:
                import_frequency[dependency] += 1

    for record in records.values():
        record["rank_inputs"]["import_frequency"] = float(import_frequency.get(record["path"], 0))
        record["rank"] = round(sum(record["rank_inputs"].values()), 2)


def resolve_import(current_path: str, import_name: str, language: str, known_paths: set[str]) -> str | None:
    import_name = import_name.strip()
    if not import_name:
        return None
    current_parent = Path(current_path).parent
    candidates: list[Path] = []
    if language in {"javascript", "typescript"} and import_name.startswith("."):
        base = current_parent / import_name
        candidates.extend(
            [
                base,
                base.with_suffix(".ts"),
                base.with_suffix(".tsx"),
                base.with_suffix(".js"),
                base.with_suffix(".jsx"),
                base / "index.ts",
                base / "index.tsx",
                base / "index.js",
                base / "index.jsx",
            ]
        )
    elif language == "python":
        module = import_name
        dots = len(module) - len(module.lstrip("."))
        if dots:
            relative_bits = [bit for bit in module.lstrip(".").split(".") if bit]
            ancestor = current_parent
            for _ in range(max(dots - 1, 0)):
                ancestor = ancestor.parent
            base = ancestor.joinpath(*relative_bits)
        else:
            base = Path(*module.split("."))
        candidates.extend([base.with_suffix(".py"), base / "__init__.py"])
    elif language in {"c", "cpp"} and import_name.startswith('"'):
        candidates.append(current_parent / import_name.strip('"'))

    for candidate in candidates:
        normalized = Path(os.path.normpath(str(candidate))).as_posix().lstrip("./")
        if normalized in known_paths:
            return normalized
    return None


def extract_imports(language: str, symbols: list[dict]) -> list[str]:
    modules: list[str] = []
    for symbol in symbols:
        if symbol["type"] != "import":
            continue
        modules.extend(parse_import_entry(language, symbol["signature"]))
    return sorted(set(modules))


def extract_exports(language: str, symbols: list[dict]) -> list[str]:
    exports: set[str] = set()
    if language in {"javascript", "typescript"}:
        for symbol in symbols:
            if symbol["type"] == "export":
                exports.update(parse_export_entry(symbol["signature"]))
            elif symbol["type"] in {"function", "class", "interface", "type_alias", "enum"} and symbol["signature"].startswith("export "):
                exports.add(symbol["name"])
    elif language == "python":
        for symbol in symbols:
            if symbol["type"] in {"function", "class", "constant"} and not symbol["name"].startswith("_"):
                exports.add(symbol["name"])
    elif language == "go":
        for symbol in symbols:
            if symbol["name"][:1].isupper():
                exports.add(symbol["name"])
    else:
        for symbol in symbols:
            if symbol["type"] in {"function", "class", "interface", "struct", "enum", "trait"} and (
                "pub " in symbol["signature"] or "public " in symbol["signature"]
            ):
                exports.add(symbol["name"])
    return sorted(exports)


def parse_import_entry(language: str, entry: str) -> list[str]:
    if language in {"javascript", "typescript"}:
        matches = re_findall(r"""from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]""", entry)
        return [match for pair in matches for match in pair if match]
    if language == "python":
        match = re_match(r"from\s+([^\s]+)\s+import", entry)
        if match:
            return [match.group(1)]
        match = re_match(r"import\s+(.+)", entry)
        if match:
            return [chunk.strip().split(" as ")[0] for chunk in match.group(1).split(",")]
    if language == "rust":
        match = re_match(r"use\s+(.+);", entry)
        if match:
            return [match.group(1)]
    if language == "go":
        return re_findall(r'"([^"]+)"', entry)
    if language == "java":
        match = re_match(r"import\s+(.+);", entry)
        if match:
            return [match.group(1)]
    if language in {"c", "cpp"}:
        match = re_match(r"#include\s+([<\"].+[>\"])", entry)
        if match:
            return [match.group(1)]
    return []


def parse_export_entry(entry: str) -> list[str]:
    names: list[str] = []
    if "export default" in entry:
        names.append("default")
    for pattern in [
        r"export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
        r"export\s+class\s+([A-Za-z_$][\w$]*)",
        r"export\s+interface\s+([A-Za-z_$][\w$]*)",
        r"export\s+type\s+([A-Za-z_$][\w$]*)",
        r"export\s+enum\s+([A-Za-z_$][\w$]*)",
        r"export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
    ]:
        names.extend(re_findall(pattern, entry))
    brace_match = re_match(r"export\s*\{(.+)\}", entry)
    if brace_match:
        for chunk in brace_match.group(1).split(","):
            names.append(chunk.strip().split(" as ")[-1])
    return [name for name in names if name]


def build_tree(records: dict[str, dict]) -> dict[str, list[str]]:
    tree: dict[str, set[str]] = {".": set()}
    for path in records:
        parts = path.split("/")
        tree["."].add(parts[0] + ("/" if len(parts) > 1 else ""))
        if len(parts) > 1:
            top = f"{parts[0]}/"
            tree.setdefault(top, set()).add(parts[1] + ("/" if len(parts) > 2 else ""))
        if len(parts) > 2:
            second = f"{parts[0]}/{parts[1]}/"
            tree.setdefault(second, set()).add(parts[2] + ("/" if len(parts) > 3 else ""))
    return {key: sorted(value) for key, value in sorted(tree.items())}


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cleanup_deleted_caches(root: Path, old_files: dict[str, dict], deleted_paths: list[str]) -> None:
    for path in deleted_paths:
        cache_path = old_files.get(path, {}).get("cache_path")
        if not cache_path:
            continue
        target = root / cache_path
        if target.exists():
            target.unlink()


def timeout_record(item: dict) -> dict:
    return {
        "path": item["path"],
        "language": item["language"],
        "lines": 0,
        "size_bytes": item["size"],
        "parse_engine": "timeout",
        "fingerprint": {
            "size": item["size"],
            "mtime_ns": item["mtime_ns"],
            "sha1": item.get("sha1", ""),
        },
        "symbols": [],
        "imports": [],
        "exports": [],
        "resolved_dependencies": [],
        "rank_inputs": {},
        "rank": 0.0,
        "warnings": ["parse timeout"],
    }


def read_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.exists():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped.rstrip("/"))
    return patterns


def is_ignored(rel_path: str, patterns: list[str], *, is_dir: bool) -> bool:
    if not rel_path:
        return False
    path = rel_path.replace("\\", "/")
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").lstrip("/")
        if "/" not in normalized and fnmatch.fnmatch(Path(path).name, normalized):
            return True
        if fnmatch.fnmatch(path, normalized):
            return True
        if is_dir and (path == normalized or path.startswith(f"{normalized}/")):
            return True
    return False


def detect_language(path: Path) -> str | None:
    if path.name == "Dockerfile":
        return "docker"
    language = EXTENSION_LANGUAGE.get(path.suffix.lower())
    if language:
        return language
    if path.name in CONFIG_BONUS_FILES or path.name in README_FILES:
        return "config"
    return None


def normalize_changed_hints(root: Path, changed_files: list[str] | None) -> set[str]:
    if not changed_files:
        return set()
    normalized: set[str] = set()
    for raw_path in changed_files:
        try:
            candidate = Path(raw_path).resolve().relative_to(root).as_posix()
        except ValueError:
            candidate = Path(raw_path).as_posix().lstrip("./")
        normalized.add(candidate)
    return normalized


def entrypoint_bonus(path: str, package_main: str | None, cargo_targets: set[str]) -> float:
    filename = Path(path).name
    if filename in ENTRYPOINT_FILES or (package_main and path == package_main) or path in cargo_targets:
        return 10.0
    return 0.0


def read_package_main(root: Path) -> str | None:
    package_json = root / "package.json"
    if not package_json.exists():
        return None
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    main_field = data.get("main")
    if isinstance(main_field, str):
        return Path(main_field).as_posix().lstrip("./")
    return None


def read_cargo_targets(root: Path) -> set[str]:
    cargo_toml = root / "Cargo.toml"
    if not cargo_toml.exists() or tomllib is None:
        return set()
    try:
        data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    targets: set[str] = set()
    library = data.get("lib")
    if isinstance(library, dict) and isinstance(library.get("path"), str):
        targets.add(Path(library["path"]).as_posix())
    bins = data.get("bin", [])
    if isinstance(bins, list):
        for item in bins:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                targets.add(Path(item["path"]).as_posix())
    return targets


def is_config_or_schema(path: str) -> bool:
    filename = Path(path).name
    lower = path.lower()
    return filename in CONFIG_BONUS_FILES or "schema" in lower or "migration" in lower


def is_doc_file(path: str) -> bool:
    parts = path.split("/")
    return Path(path).name in README_FILES or (parts and parts[0] == "docs")


def is_test_file(path: str) -> bool:
    filename = Path(path).name.lower()
    return (
        filename.startswith("test_")
        or filename.endswith(
            (
                "_test.go",
                "_spec.ts",
                "_spec.tsx",
                "_spec.js",
                "_test.py",
                ".test.ts",
                ".test.tsx",
                ".test.js",
                ".test.jsx",
            )
        )
        or "/tests/" in f"/{path.lower()}/"
    )


def size_penalty(lines: int) -> float:
    if lines > 1000:
        return -2.0
    if lines > 500:
        return -1.0
    return 0.0


def histogram(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def choose_primary_language(histogram_map: dict[str, int]) -> str | None:
    return next(iter(histogram_map), None)


def collect_manifest_warnings(records: list[dict]) -> list[str]:
    warnings: list[str] = []
    for record in records:
        for warning in record.get("warnings", []):
            warnings.append(f"{record['path']}: {warning}")
    return warnings[:100]


def first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def join_rel(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def re_match(pattern: str, text: str):
    import re

    return re.match(pattern, text)


def re_findall(pattern: str, text: str):
    import re

    return re.findall(pattern, text)


if __name__ == "__main__":
    raise SystemExit(main())
