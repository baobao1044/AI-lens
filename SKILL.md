---
name: ai-lens
description: >
  Read and understand a codebase with minimal token use. Use this skill for Claude
  Code or Codex whenever you need to explore a repo, understand project structure,
  explain architecture, locate symbols, navigate modules, or answer questions that
  need broad codebase context before reading files. Triggers include: "read my
  project", "understand this codebase", "explore this repo", "what does this
  project do", "help me with this codebase", "navigate this code", "explain this
  architecture", "find this function", "tim hieu project", "doc code", "hieu
  codebase", and similar requests.
---

# AI Lens

Use `ai-lens` before reading files one by one. The default workflow is:

1. `scan`: build or refresh the local project index.
2. `query`: ask the index for the files, symbols, and ranges that matter.
3. `read`: open only the relevant file chunks after the index narrows scope.

## Host Compatibility

- Claude Code: use this skill folder directly and run the bundled Python scripts.
- Codex: use the same skill folder; `agents/openai.yaml` adds UI metadata only.

## Workflow

### 1. Scan once per project session

Run from the project root:

```bash
python scripts/scan.py .
```

Use `--force` for a full rebuild and `--full-dump` if you also want `.ai-lens.json`.

What `scan` does:

- Respects `.gitignore` and common generated directories.
- Builds `.ai-lens/manifest.json` plus per-file cache records.
- Uses tree-sitter when language packages are present.
- Falls back to regex parsing when tree-sitter is unavailable or fails.

### 2. Query before reading files

Common queries:

```bash
python scripts/query.py --index . --type architecture
python scripts/query.py --index . --symbol startServer
python scripts/query.py --index . --related authentication
python scripts/query.py --index . --dependents src/models/user.ts
python scripts/query.py --index . --pattern "routes/*"
```

Use `--json` when another tool or automation needs structured output.

### 3. Read only the needed ranges

After `query`, open only the relevant lines from the highest-ranked files. Prefer:

- symbol range from query output
- nearby imports/exports if more context is needed
- dependents when understanding call paths

Avoid reading entire large files unless the narrowed ranges are still insufficient.

## Token Discipline

- Do not start by reading the whole repo.
- Do not read full files when signature-level summaries are enough.
- Prefer ranked files over arbitrary traversal.
- Prefer line-range reads over full-file reads.
- Re-run `scan` after large code changes or when `query` reports a stale index.

## Heuristics

- `--type architecture`: use for first-pass repo understanding.
- `--symbol NAME`: use when a function, class, method, or type is named.
- `--related TEXT`: use for bugs, features, or error strings.
- `--dependents PATH`: use to see impact and callers/importers.
- `--pattern GLOB`: use when the module area is known already.

## Notes

- Tree-sitter support is optional. Missing packages are not fatal.
- The index is local, deterministic, and intended to stay small enough for fast reuse.
- Language support details live in [references/supported-languages.md](references/supported-languages.md).
