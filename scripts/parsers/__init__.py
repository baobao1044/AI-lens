#!/usr/bin/env python3

from .fallback import parse_file as parse_with_fallback
from .tree_sitter import ParserUnavailable, parse_file as parse_with_tree_sitter

__all__ = [
    "ParserUnavailable",
    "parse_with_fallback",
    "parse_with_tree_sitter",
]
