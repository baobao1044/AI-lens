# ai-lens

`ai-lens` is a token-efficient codebase indexer for agent workflows. It scans a
project once, stores a compact index under `.ai-lens/`, and lets the caller ask
for ranked files, symbols, architecture, and dependents before reading source.

This workspace ships the CLI scanner/query tooling, semantic search helpers,
symbol call-chain tracing, an MCP stdio server, and an optional filesystem
watcher.

## What exists today

- `python scripts/scan.py .`
- `python scripts/query.py --index . --type architecture`
- `python scripts/query.py --index . --symbol parse_file`
- `python scripts/query.py --index . --related authentication`
- `python scripts/query.py --index . --dependents path/to/file.py`
- `python scripts/query.py --index . --pattern "routes/*"`
- `python scripts/query.py --index . --semantic "payment processing"`
- `python scripts/query.py --index . --call-chain handleAuth --depth 3`
- `python scripts/watch.py .`
- `python mcp_server.py`

Current `query.py` modes are:

- `--symbol`
- `--related`
- `--dependents`
- `--pattern`
- `--semantic`
- `--call-chain`
- `--type architecture|full`

## Install

Base install:

```bash
pip install .
```

With optional tree-sitter parsers:

```bash
pip install ".[tree-sitter]"
```

Planned MCP support:

```bash
pip install ".[mcp]"
```

Semantic-search extras:

```bash
pip install ".[semantic]"
```

Watch-mode extras:

```bash
pip install ".[watch]"
```

Developer and CI extras:

```bash
pip install ".[dev]"
```

## CLI Usage

Build or refresh the index:

```bash
python scripts/scan.py .
python scripts/scan.py . --force
python scripts/scan.py . --full-dump
```

Query the index:

```bash
python scripts/query.py --index . --type architecture
python scripts/query.py --index . --symbol startServer
python scripts/query.py --index . --related authentication
python scripts/query.py --index . --dependents src/models/user.ts
python scripts/query.py --index . --pattern "routes/*"
python scripts/query.py --index . --semantic "payment processing"
python scripts/query.py --index . --call-chain startServer --depth 3
```

Machine-readable output is available with `--json` on both commands.

## Quality Gates

Test suite:

```bash
pytest -q
```

Benchmark harness:

```bash
python benchmarks/run_benchmark.py --modules 100 --fanout 3 --json
```

Build artifacts:

```bash
python -m build
```

CI/CD and release operations:

- CI lives in `.github/workflows/ci.yml`
- Release automation lives in `.github/workflows/release.yml`
- Operational runbooks live in [docs/production.md](docs/production.md)

Watch mode:

```bash
python scripts/watch.py .
python scripts/watch.py . --install-hook
```

## MCP Usage

The top-level `mcp_server.py` exposes ai-lens over stdio. The package-level entry
point is:

```bash
ai-lens-mcp
```

### Claude Desktop

```json
{
  "mcpServers": {
    "ai-lens": {
      "command": "python",
      "args": ["/absolute/path/to/ai-lens/mcp_server.py"]
    }
  }
}
```

### Cursor

```json
{
  "mcpServers": {
    "ai-lens": {
      "command": "python",
      "args": ["/absolute/path/to/ai-lens/mcp_server.py"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add ai-lens python /absolute/path/to/ai-lens/mcp_server.py
```

The MCP server exposes:

- `ai_lens_scan`
- `ai_lens_query_symbol`
- `ai_lens_query_related`
- `ai_lens_architecture`
- `ai_lens_dependents`
- `ai_lens_read_symbol`
- `ai_lens_call_chain`
- `ai_lens_semantic_search`

## Notes

- The scanner respects `.gitignore` and skips common generated directories.
- Tree-sitter parsing is optional; the fallback parser still works when parser
  packages are not installed.
- `.ai-lens/manifest.json` is the main index entrypoint.
- `.ai-lens.json` is an optional merged export written by `--full-dump`.
- `.ai-lens/symbol_graph.json` stores symbol call relationships.
- `.ai-lens/semantic/` stores semantic-search caches built lazily on demand.
- Production operations and release notes live in [docs/production.md](docs/production.md).
