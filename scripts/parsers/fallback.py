#!/usr/bin/env python3

"""Regex-based fallback parser for ai-lens."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

MAX_DOC_CHARS = 100


PATTERNS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "python": [
        (re.compile(r"^(class\s+([A-Za-z_][\w]*)[^\n]*:)", re.MULTILINE), "class"),
        (
            re.compile(
                r"^((?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\([^)]*\)\s*(?:->\s*[^:]+)?:)",
                re.MULTILINE,
            ),
            "function",
        ),
        (re.compile(r"^((?:from\s+\S+\s+import\s+.+|import\s+.+))", re.MULTILINE), "import"),
        (re.compile(r"^([A-Z][A-Z0-9_]*\s*=.+)", re.MULTILINE), "constant"),
    ],
    "javascript": [
        (
            re.compile(
                r"^((?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\))",
                re.MULTILINE,
            ),
            "function",
        ),
        (
            re.compile(
                r"^((?:export\s+)?class\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+[^{\n]+)?)",
                re.MULTILINE,
            ),
            "class",
        ),
        (
            re.compile(
                r"^((?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)",
                re.MULTILINE,
            ),
            "function",
        ),
        (re.compile(r"^((?:import|export)\s.+)", re.MULTILINE), "import"),
    ],
    "typescript": [
        (
            re.compile(
                r"^((?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)(?:\s*:\s*[^{\n]+)?)",
                re.MULTILINE,
            ),
            "function",
        ),
        (
            re.compile(
                r"^((?:export\s+)?interface\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+[^{\n]+)?)",
                re.MULTILINE,
            ),
            "interface",
        ),
        (
            re.compile(
                r"^((?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=.+)",
                re.MULTILINE,
            ),
            "type_alias",
        ),
        (
            re.compile(
                r"^((?:export\s+)?class\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+[^{\n]+)?)",
                re.MULTILINE,
            ),
            "class",
        ),
        (
            re.compile(
                r"^((?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:<[^>]+>\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)",
                re.MULTILINE,
            ),
            "function",
        ),
        (re.compile(r"^((?:import|export)\s.+)", re.MULTILINE), "import"),
    ],
    "rust": [
        (re.compile(r"^((?:pub\s+)?fn\s+([A-Za-z_][\w]*)\s*\([^)]*\)(?:\s*->\s*[^{\n]+)?)", re.MULTILINE), "function"),
        (re.compile(r"^((?:pub\s+)?struct\s+([A-Za-z_][\w]*))", re.MULTILINE), "struct"),
        (re.compile(r"^((?:pub\s+)?enum\s+([A-Za-z_][\w]*))", re.MULTILINE), "enum"),
        (re.compile(r"^((?:pub\s+)?trait\s+([A-Za-z_][\w]*))", re.MULTILINE), "trait"),
        (re.compile(r"^(use\s+.+;)", re.MULTILINE), "import"),
        (re.compile(r"^((?:pub\s+)?(?:const|static)\s+([A-Za-z_][\w]*).+;)", re.MULTILINE), "constant"),
    ],
    "go": [
        (re.compile(r"^(func\s+([A-Za-z_][\w]*)\s*\([^)]*\)\s*(?:\([^)]*\)|[^{\n]+)?)", re.MULTILINE), "function"),
        (re.compile(r"^(func\s+\([^)]*\)\s+([A-Za-z_][\w]*)\s*\([^)]*\)\s*(?:\([^)]*\)|[^{\n]+)?)", re.MULTILINE), "method"),
        (re.compile(r"^(type\s+([A-Za-z_][\w]*)\s+(?:struct|interface|map|chan|func|[A-Za-z_\[]))", re.MULTILINE), "type_alias"),
        (re.compile(r"^(import\s+(?:\([^)]+\)|\"[^\"]+\"))", re.MULTILINE), "import"),
        (re.compile(r"^((?:const|var)\s+([A-Za-z_][\w]*).*)", re.MULTILINE), "constant"),
    ],
    "java": [
        (
            re.compile(
                r"^((?:public|protected|private|abstract|final|static|\s)+class\s+([A-Za-z_][\w]*).*)",
                re.MULTILINE,
            ),
            "class",
        ),
        (
            re.compile(
                r"^((?:public|protected|private|abstract|final|static|\s)+interface\s+([A-Za-z_][\w]*).*)",
                re.MULTILINE,
            ),
            "interface",
        ),
        (
            re.compile(
                r"^((?:public|protected|private|static|final|synchronized|abstract|\s)+[A-Za-z_<>\[\], ?]+\s+([A-Za-z_][\w]*)\s*\([^;]*\))",
                re.MULTILINE,
            ),
            "method",
        ),
        (re.compile(r"^(import\s+.+;)", re.MULTILINE), "import"),
    ],
    "c": [
        (re.compile(r"^(#include\s+[<\"].+[>\"])", re.MULTILINE), "import"),
        (re.compile(r"^(typedef\s+struct\s+([A-Za-z_][\w]*).*)", re.MULTILINE), "struct"),
        (re.compile(r"^([A-Za-z_][\w\s\*]+\s+([A-Za-z_][\w]*)\s*\([^;]*\))\s*\{", re.MULTILINE), "function"),
    ],
    "cpp": [
        (re.compile(r"^(#include\s+[<\"].+[>\"])", re.MULTILINE), "import"),
        (re.compile(r"^((?:template\s*<[^>]+>\s*)?(?:class|struct)\s+([A-Za-z_][\w]*).*)", re.MULTILINE), "class"),
        (re.compile(r"^([A-Za-z_:\<\>\~\w\s\*&]+\s+([A-Za-z_][\w:]*)\s*\([^;]*\))\s*(?:const)?\s*\{", re.MULTILINE), "function"),
    ],
    "ruby": [
        (re.compile(r"^(\s*class\s+([A-Za-z_][\w:]*)(?:\s*<\s*[A-Za-z_][\w:]*)?)", re.MULTILINE), "class"),
        (re.compile(r"^(\s*module\s+([A-Za-z_][\w:]*))", re.MULTILINE), "module"),
        (re.compile(r"^(\s*def\s+(?:self\.)?([A-Za-z_][\w?!]*)(?:\s*\([^)]*\))?)", re.MULTILINE), "function"),
        (re.compile(r"^(require\s+.+|require_relative\s+.+)", re.MULTILINE), "import"),
    ],
    "php": [
        (re.compile(r"^(\s*(?:abstract\s+|final\s+)?class\s+([A-Za-z_][\w]*)(?:\s+extends\s+[A-Za-z_][\w]*)?(?:\s+implements\s+[^\n{]+)?)", re.MULTILINE), "class"),
        (re.compile(r"^(\s*interface\s+([A-Za-z_][\w]*))", re.MULTILINE), "interface"),
        (re.compile(r"^(\s*trait\s+([A-Za-z_][\w]*))", re.MULTILINE), "trait"),
        (re.compile(r"^(\s*(?:public|protected|private|static|\s)*function\s+([A-Za-z_][\w]*)\s*\([^)]*\))", re.MULTILINE), "function"),
        (re.compile(r"^(use\s+.+;|namespace\s+.+;)", re.MULTILINE), "import"),
    ],
    "kotlin": [
        (re.compile(r"^(\s*(?:open|abstract|data|sealed|inner|enum)?\s*class\s+([A-Za-z_][\w]*)(?:\s*\([^)]*\))?(?:\s*:\s*[^\n{]+)?)", re.MULTILINE), "class"),
        (re.compile(r"^(\s*interface\s+([A-Za-z_][\w]*))", re.MULTILINE), "interface"),
        (re.compile(r"^(\s*object\s+([A-Za-z_][\w]*))", re.MULTILINE), "class"),
        (re.compile(r"^(\s*(?:(?:override|open|abstract|private|public|internal|protected|suspend|inline)\s+)*fun\s+(?:<[^>]+>\s*)?([A-Za-z_][\w]*)\s*\([^)]*\))", re.MULTILINE), "function"),
        (re.compile(r"^(import\s+.+)", re.MULTILINE), "import"),
    ],
    "swift": [
        (re.compile(r"^(\s*(?:open|public|internal|fileprivate|private|final)?\s*class\s+([A-Za-z_][\w]*)(?:\s*:\s*[^\n{]+)?)", re.MULTILINE), "class"),
        (re.compile(r"^(\s*struct\s+([A-Za-z_][\w]*)(?:\s*:\s*[^\n{]+)?)", re.MULTILINE), "struct"),
        (re.compile(r"^(\s*protocol\s+([A-Za-z_][\w]*))", re.MULTILINE), "interface"),
        (re.compile(r"^(\s*enum\s+([A-Za-z_][\w]*))", re.MULTILINE), "enum"),
        (re.compile(r"^(\s*(?:(?:override|open|public|private|internal|fileprivate|static|class|mutating)\s+)*func\s+([A-Za-z_][\w]*)\s*\([^)]*\)(?:\s*->\s*[^\n{]+)?)", re.MULTILINE), "function"),
        (re.compile(r"^(import\s+.+)", re.MULTILINE), "import"),
    ],
}


def parse_file(filepath: str, language: str, max_doc_chars: int = MAX_DOC_CHARS) -> list[dict]:
    text = Path(filepath).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    patterns = PATTERNS.get(language, [])
    symbols: list[dict] = []
    for pattern, symbol_type in patterns:
        for match in pattern.finditer(text):
            raw_signature = match.group(1).strip()
            name = match.group(2).strip() if match.lastindex and match.lastindex >= 2 else _infer_name(raw_signature, symbol_type)
            line_number = text.count("\n", 0, match.start()) + 1
            entry = {
                "type": symbol_type,
                "name": name,
                "signature": _normalize_signature(raw_signature, symbol_type, language),
                "line_start": line_number,
                "line_end": _estimate_line_end(lines, line_number, language, symbol_type),
            }
            doc = _extract_preceding_doc(lines, line_number, max_doc_chars)
            if doc:
                entry["doc"] = doc
            if symbol_type == "import" and raw_signature.lstrip().startswith("export "):
                entry["type"] = "export"
            symbols.append(entry)
    symbols.sort(key=lambda item: (item["line_start"], item["name"]))
    return _dedupe_symbols(symbols)


def _dedupe_symbols(symbols: Iterable[dict]) -> list[dict]:
    seen: set[tuple[str, str, int]] = set()
    unique: list[dict] = []
    for symbol in symbols:
        key = (symbol["type"], symbol["name"], symbol["line_start"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(symbol)
    return unique


def _extract_preceding_doc(lines: list[str], line_number: int, max_doc_chars: int) -> str | None:
    index = line_number - 2
    collected: list[str] = []
    while index >= 0:
        line = lines[index].strip()
        if not line:
            if collected:
                break
            index -= 1
            continue
        if line.startswith(("#", "//", "/*", "*", "--")):
            collected.append(re.sub(r"^(#|//|/\*+|\*+|--)\s?", "", line).rstrip("*/ ").strip())
            index -= 1
            continue
        break
    if not collected:
        return None
    doc = " ".join(reversed([part for part in collected if part]))
    return doc[:max_doc_chars] if doc else None


def _normalize_signature(signature: str, symbol_type: str, language: str) -> str:
    normalized = " ".join(signature.replace("\r", "").split())
    if symbol_type in {"function", "method"}:
        normalized = normalized.rstrip("{").rstrip(":").rstrip(";").strip()
    elif language in {"javascript", "typescript"} and symbol_type in {"import", "export"}:
        normalized = normalized.rstrip(";")
    return normalized


def _infer_name(signature: str, symbol_type: str) -> str:
    if symbol_type == "import":
        return signature
    patterns = [
        r"\b(class|interface|enum|struct|trait|type)\s+([A-Za-z_][\w$:]*)",
        r"\bdef\s+([A-Za-z_][\w]*)",
        r"\bfn\s+([A-Za-z_][\w]*)",
        r"\bfunc\s+(?:\([^)]*\)\s+)?([A-Za-z_][\w]*)",
        r"\bfunction\s+([A-Za-z_$][\w$]*)",
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, signature)
        if not match:
            continue
        if match.lastindex and match.lastindex > 1:
            return match.group(match.lastindex)
        return match.group(1)
    return signature.split()[0] if signature.split() else "unknown"


def _estimate_line_end(lines: list[str], line_number: int, language: str, symbol_type: str) -> int:
    if line_number > len(lines):
        return line_number
    if language == "python" and symbol_type in {"class", "function", "method"}:
        return _estimate_python_block_end(lines, line_number)
    if language in {"javascript", "typescript", "rust", "go", "java", "c", "cpp"} and symbol_type in {
        "class",
        "function",
        "method",
        "interface",
        "struct",
        "enum",
        "trait",
        "type_alias",
    }:
        return _estimate_brace_block_end(lines, line_number)
    return line_number


def _estimate_python_block_end(lines: list[str], line_number: int) -> int:
    start_index = line_number - 1
    header_end = start_index
    paren_depth = 0
    for index in range(start_index, len(lines)):
        line = _strip_strings(lines[index])
        paren_depth += line.count("(") + line.count("[") + line.count("{")
        paren_depth -= line.count(")") + line.count("]") + line.count("}")
        header_end = index
        if paren_depth <= 0 and line.rstrip().endswith(":"):
            break

    header_line = lines[header_end]
    start_indent = len(header_line) - len(header_line.lstrip())
    last_content = header_end + 1
    for index in range(header_end + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= start_indent and not stripped.startswith(("#", '"""', "'''")):
            break
        last_content = index + 1
    return last_content


def _estimate_brace_block_end(lines: list[str], line_number: int) -> int:
    start_index = line_number - 1
    brace_depth = 0
    seen_open = False
    last_content = line_number
    for index in range(start_index, len(lines)):
        line = _strip_strings(lines[index])
        if line.strip():
            last_content = index + 1
        brace_depth += line.count("{")
        if "{" in line:
            seen_open = True
        brace_depth -= line.count("}")
        if seen_open and brace_depth <= 0 and index > start_index:
            return index + 1
    return last_content


def _strip_strings(line: str) -> str:
    return re.sub(r"('(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")", "", line)
