# Production Operations

## Local validation

Run the baseline checks before opening a PR:

```bash
python -m py_compile mcp_server.py scripts/__init__.py scripts/index_store.py scripts/scan.py scripts/query.py scripts/semantic.py scripts/symbol_graph.py scripts/watch.py scripts/parsers/fallback.py scripts/parsers/tree_sitter.py
pytest -q
python benchmarks/run_benchmark.py --modules 24 --fanout 2 --json
python -m build
```

## CI

GitHub Actions live under `.github/workflows/`.

- `ci.yml`: push/PR validation on Ubuntu and Windows
- `release.yml`: build wheel/sdist, checksum them, and publish a GitHub Release on `v*` tags
- `dependabot.yml`: weekly dependency and Actions updates

The CI baseline is intentionally portable:

- no tree-sitter packages required
- no `mcp` runtime required to import the package
- no semantic ML dependencies required for tests

## Release flow

1. Update version in `pyproject.toml`.
2. Run the local validation commands above.
3. Create and push a tag like `v0.3.0`.
4. Let `release.yml` build artifacts and attach them to the GitHub Release.

## Benchmarking

`benchmarks/run_benchmark.py` generates a synthetic Python repo, runs scan/query workloads, and prints text or JSON output.

Example:

```bash
python benchmarks/run_benchmark.py --modules 100 --fanout 3 --json
```

Track at least:

- scan latency
- symbol/related/call-chain/semantic query latency
- `.ai-lens/` artifact size

## Operational notes

- `mcp_server.py` exits with a clear error when `mcp` is not installed; install `.[mcp]` for MCP usage.
- `scripts/watch.py` requires `watchdog`; install `.[watch]` for watch mode.
- `scripts/semantic.py` degrades to lexical matching when semantic dependencies are unavailable.
