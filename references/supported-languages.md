# Supported Languages

`ai-lens` prefers tree-sitter when the matching Python package is installed. When
that package is missing or parsing fails, it falls back to regex extraction.

## Coverage Matrix

| Language | Detection | Tree-sitter package | Main extracted nodes | Fallback |
| --- | --- | --- | --- | --- |
| Python | `.py` | `tree-sitter-python` | classes, functions, methods, imports, assignments | regex + comment/docstring heuristics |
| JavaScript | `.js`, `.cjs`, `.mjs`, `.jsx` | `tree-sitter-javascript` | functions, classes, imports, exports, top-level arrow functions | regex |
| TypeScript | `.ts`, `.tsx` | `tree-sitter-typescript` | functions, classes, interfaces, enums, imports, exports, type aliases | regex |
| Rust | `.rs` | `tree-sitter-rust` | functions, structs, enums, traits, impls, uses | regex |
| Go | `.go` | `tree-sitter-go` | functions, methods, types, imports, consts, vars | regex |
| Java | `.java` | `tree-sitter-java` | classes, interfaces, enums, methods, imports | regex |
| C | `.c`, `.h` | `tree-sitter-c` | functions, structs, typedefs, includes | regex |
| C++ | `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx` | `tree-sitter-cpp` | functions, classes, structs, typedefs, includes | regex |

## Query Patterns

- Architecture: entry points, config files, high-rank modules, dependency summary.
- Symbol lookup: exact or fuzzy match on symbol names, then show file/range/signature.
- Related lookup: keyword scoring over symbol names, paths, imports, exports, and file rank.
- Dependents: reverse edges from the dependency graph.
- Pattern lookup: glob match over indexed file paths.

## Known Limits

- Import resolution is best-effort; local relative imports are favored over full build-tool fidelity.
- Fallback regex favors speed and portability over syntax-perfect extraction.
- Query output is intentionally short; use file/range reads after narrowing the search.
