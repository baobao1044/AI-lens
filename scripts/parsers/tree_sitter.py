#!/usr/bin/env python3

"""Optional tree-sitter parser integration for ai-lens."""

from __future__ import annotations

import importlib
import re
from pathlib import Path


class ParserUnavailable(RuntimeError):
    """Raised when a tree-sitter parser cannot be loaded for a language."""


_PACKAGES = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "rust": "tree_sitter_rust",
    "go": "tree_sitter_go",
    "java": "tree_sitter_java",
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
}


def parse_file(filepath: str, language: str, max_doc_chars: int = 100) -> list[dict]:
    parser, source_bytes = _build_parser(filepath, language)
    tree = parser.parse(source_bytes)
    source_text = source_bytes.decode("utf-8", errors="replace")
    root = tree.root_node
    extractor = globals().get(f"_extract_{language}")
    if extractor is None:
        raise ParserUnavailable(f"No extractor implemented for language: {language}")
    return extractor(root, source_text, max_doc_chars)


def available_languages() -> dict[str, bool]:
    status: dict[str, bool] = {}
    for language in _PACKAGES:
        try:
            _load_language(language)
        except ParserUnavailable:
            status[language] = False
        else:
            status[language] = True
    return status


def _build_parser(filepath: str, language: str):
    try:
        tree_sitter = importlib.import_module("tree_sitter")
    except ImportError as exc:
        raise ParserUnavailable("tree_sitter is not installed") from exc

    source_bytes = Path(filepath).read_bytes()
    language_obj = _load_language(language)
    parser = tree_sitter.Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language_obj)
    else:
        parser.language = language_obj
    return parser, source_bytes


def _load_language(language: str):
    try:
        tree_sitter = importlib.import_module("tree_sitter")
    except ImportError as exc:
        raise ParserUnavailable("tree_sitter is not installed") from exc

    package_name = _PACKAGES.get(language)
    if package_name is None:
        raise ParserUnavailable(f"Unsupported tree-sitter language: {language}")

    try:
        module = importlib.import_module(package_name)
    except ImportError as exc:
        raise ParserUnavailable(f"{package_name} is not installed") from exc

    candidates = ["language", language]
    if language == "typescript":
        candidates = ["language_typescript", "typescript", "language"]

    for attr in candidates:
        if not hasattr(module, attr):
            continue
        value = getattr(module, attr)
        maybe_language = value() if callable(value) else value
        coerced = _coerce_language(tree_sitter, maybe_language)
        if coerced is not None:
            return coerced
    raise ParserUnavailable(f"Could not load language object from {package_name}")


def _coerce_language(tree_sitter, maybe_language):
    language_class = getattr(tree_sitter, "Language", None)
    if language_class is None:
        return None
    if isinstance(maybe_language, language_class):
        return maybe_language
    if isinstance(maybe_language, int):
        try:
            return language_class(maybe_language)
        except TypeError:
            return None
    try:
        return language_class(maybe_language)
    except TypeError:
        return None


def _extract_python(root, source_text: str, max_doc_chars: int) -> list[dict]:
    symbols: list[dict] = []
    for child in root.named_children:
        child = _unwrap_decorated(child)
        if child.type == "class_definition":
            class_symbol = _make_symbol(child, source_text, "class", max_doc_chars)
            methods: list[dict] = []
            body = child.child_by_field_name("body")
            if body is not None:
                for member in body.named_children:
                    member = _unwrap_decorated(member)
                    if member.type == "function_definition":
                        methods.append(_make_symbol(member, source_text, "method", max_doc_chars))
            if methods:
                class_symbol["children"] = methods
            symbols.append(class_symbol)
        elif child.type == "function_definition":
            symbols.append(_make_symbol(child, source_text, "function", max_doc_chars))
        elif child.type in {"import_statement", "import_from_statement"}:
            symbols.append(_make_symbol(child, source_text, "import", max_doc_chars))
        elif child.type == "assignment":
            name = _assignment_name(child, source_text)
            if name and name.isupper():
                symbols.append(_make_symbol(child, source_text, "constant", max_doc_chars, name_override=name))
    return _sorted_symbols(symbols)


def _extract_javascript(root, source_text: str, max_doc_chars: int) -> list[dict]:
    return _extract_js_family(root, source_text, max_doc_chars, is_typescript=False)


def _extract_typescript(root, source_text: str, max_doc_chars: int) -> list[dict]:
    return _extract_js_family(root, source_text, max_doc_chars, is_typescript=True)


def _extract_js_family(root, source_text: str, max_doc_chars: int, *, is_typescript: bool) -> list[dict]:
    mapping = {
        "function_declaration": "function",
        "class_declaration": "class",
        "import_statement": "import",
        "export_statement": "export",
        "lexical_declaration": "constant",
    }
    if is_typescript:
        mapping.update(
            {
                "interface_declaration": "interface",
                "type_alias_declaration": "type_alias",
                "enum_declaration": "enum",
            }
        )

    symbols: list[dict] = []
    for child in root.named_children:
        symbol_type = mapping.get(child.type)
        if symbol_type is None:
            continue
        if child.type == "class_declaration":
            class_symbol = _make_symbol(child, source_text, "class", max_doc_chars)
            body = child.child_by_field_name("body")
            methods: list[dict] = []
            if body is not None:
                for member in body.named_children:
                    if member.type in {"method_definition", "abstract_method_signature"}:
                        methods.append(_make_symbol(member, source_text, "method", max_doc_chars))
            if methods:
                class_symbol["children"] = methods
            symbols.append(class_symbol)
            continue
        symbols.append(_make_symbol(child, source_text, symbol_type, max_doc_chars))
        if child.type in {"lexical_declaration", "export_statement"}:
            symbols.extend(_extract_variable_functions(child, source_text, max_doc_chars))
        if child.type == "export_statement":
            for nested in child.named_children:
                if nested.type == "function_declaration":
                    symbols.append(_make_symbol(nested, source_text, "function", max_doc_chars))
                elif nested.type == "class_declaration":
                    symbols.append(_make_symbol(nested, source_text, "class", max_doc_chars))
    return _sorted_symbols(symbols)


def _extract_variable_functions(node, source_text: str, max_doc_chars: int) -> list[dict]:
    symbols: list[dict] = []
    for child in node.named_children:
        if child.type != "variable_declarator":
            continue
        value = child.child_by_field_name("value")
        if value is not None and value.type in {"arrow_function", "function", "function_expression"}:
            symbols.append(_make_symbol(child, source_text, "function", max_doc_chars))
    return symbols


def _extract_rust(root, source_text: str, max_doc_chars: int) -> list[dict]:
    return _extract_simple_mapping(
        root,
        source_text,
        max_doc_chars,
        {
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
            "impl_item": "class",
            "use_declaration": "import",
            "const_item": "constant",
            "static_item": "constant",
        },
    )


def _extract_go(root, source_text: str, max_doc_chars: int) -> list[dict]:
    return _extract_simple_mapping(
        root,
        source_text,
        max_doc_chars,
        {
            "function_declaration": "function",
            "method_declaration": "method",
            "type_declaration": "type_alias",
            "import_declaration": "import",
            "const_declaration": "constant",
            "var_declaration": "constant",
        },
    )


def _extract_java(root, source_text: str, max_doc_chars: int) -> list[dict]:
    symbols: list[dict] = []
    for child in root.named_children:
        if child.type in {"class_declaration", "interface_declaration", "enum_declaration"}:
            symbol_type = {
                "class_declaration": "class",
                "interface_declaration": "interface",
                "enum_declaration": "enum",
            }[child.type]
            container = _make_symbol(child, source_text, symbol_type, max_doc_chars)
            body = child.child_by_field_name("body")
            methods: list[dict] = []
            if body is not None:
                for member in body.named_children:
                    if member.type == "method_declaration":
                        methods.append(_make_symbol(member, source_text, "method", max_doc_chars))
            if methods:
                container["children"] = methods
            symbols.append(container)
        elif child.type == "import_declaration":
            symbols.append(_make_symbol(child, source_text, "import", max_doc_chars))
    return _sorted_symbols(symbols)


def _extract_c(root, source_text: str, max_doc_chars: int) -> list[dict]:
    return _extract_simple_mapping(
        root,
        source_text,
        max_doc_chars,
        {
            "function_definition": "function",
            "struct_specifier": "struct",
            "type_definition": "type_alias",
            "preproc_include": "import",
        },
    )


def _extract_cpp(root, source_text: str, max_doc_chars: int) -> list[dict]:
    return _extract_simple_mapping(
        root,
        source_text,
        max_doc_chars,
        {
            "function_definition": "function",
            "struct_specifier": "struct",
            "class_specifier": "class",
            "type_definition": "type_alias",
            "preproc_include": "import",
        },
    )


def _extract_simple_mapping(root, source_text: str, max_doc_chars: int, mapping: dict[str, str]) -> list[dict]:
    symbols: list[dict] = []
    for child in root.named_children:
        symbol_type = mapping.get(child.type)
        if symbol_type:
            symbols.append(_make_symbol(child, source_text, symbol_type, max_doc_chars))
    return _sorted_symbols(symbols)


def _make_symbol(node, source_text: str, symbol_type: str, max_doc_chars: int, name_override: str | None = None) -> dict:
    text = _node_text(node, source_text)
    signature = _signature_from_text(text, symbol_type)
    name = name_override or _node_name(node, source_text, signature)
    symbol = {
        "type": symbol_type,
        "name": name,
        "signature": signature,
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
    }
    doc = _symbol_doc(node, source_text, max_doc_chars)
    if doc:
        symbol["doc"] = doc
    return symbol


def _signature_from_text(text: str, symbol_type: str) -> str:
    raw = text.replace("\r", "")
    if symbol_type in {"import", "export"}:
        first_line = raw.splitlines()[0].strip() if raw.splitlines() else raw.strip()
        return first_line.rstrip(";")
    return _trim_signature(raw).rstrip(":")


def _trim_signature(text: str) -> str:
    collected: list[str] = []
    depth = 0
    for char in text:
        if char == "{":
            if depth == 0:
                break
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        collected.append(char)
        if char == "\n" and depth == 0 and "".join(collected).strip():
            recent = "".join(collected).strip()
            if recent.endswith(":"):
                break
    return " ".join("".join(collected).split()).rstrip(";").strip()


def _node_name(node, source_text: str, signature: str) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_text).strip()
    match = re.search(r"\b([A-Za-z_][\w$:]*)\b(?=\s*(?:\(|:|=|$))", signature)
    return match.group(1) if match else signature.split()[0]


def _symbol_doc(node, source_text: str, max_doc_chars: int) -> str | None:
    doc = _python_docstring(node, source_text) or _preceding_comment(node, source_text)
    if not doc:
        return None
    return " ".join(doc.split())[:max_doc_chars]


def _python_docstring(node, source_text: str) -> str | None:
    if node.type not in {"function_definition", "class_definition"}:
        return None
    body = node.child_by_field_name("body")
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    if first.type != "expression_statement" or not first.named_children:
        return None
    raw = _node_text(first.named_children[0], source_text).strip()
    if len(raw) >= 2 and raw[0] in {'"', "'"}:
        return raw.strip("\"' ")
    return None


def _preceding_comment(node, source_text: str) -> str | None:
    lines = source_text.splitlines()
    index = node.start_point[0] - 1
    collected: list[str] = []
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped:
            if collected:
                break
            index -= 1
            continue
        if stripped.startswith(("#", "//", "/*", "*", "--")):
            collected.append(re.sub(r"^(#|//|/\*+|\*+|--)\s?", "", stripped).rstrip("*/ ").strip())
            index -= 1
            continue
        break
    if not collected:
        return None
    return " ".join(reversed([item for item in collected if item]))


def _assignment_name(node, source_text: str) -> str | None:
    left = node.child_by_field_name("left")
    if left is not None:
        return _node_text(left, source_text).strip().split(",")[0]
    if node.named_children:
        return _node_text(node.named_children[0], source_text).strip().split(",")[0]
    return None


def _unwrap_decorated(node):
    if node.type != "decorated_definition":
        return node
    definition = node.child_by_field_name("definition")
    if definition is not None:
        return definition
    for child in node.named_children:
        if child.type in {"class_definition", "function_definition"}:
            return child
    return node


def _node_text(node, source_text: str) -> str:
    return source_text[node.start_byte : node.end_byte]


def _sorted_symbols(symbols: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, int]] = set()
    result: list[dict] = []
    for symbol in sorted(symbols, key=lambda item: (item["line_start"], item["name"])):
        key = (symbol["type"], symbol["name"], symbol["line_start"])
        if key in seen:
            continue
        seen.add(key)
        result.append(symbol)
    return result
