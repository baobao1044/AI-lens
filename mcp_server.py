#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from index_store import ensure_index, normalize_project_path, read_symbol_range, render_tree  # noqa: E402
from query import (  # noqa: E402
    format_query_result,
    query_architecture,
    query_call_chain,
    query_dependents,
    query_related,
    query_semantic,
    query_symbol,
)
from scan import format_scan_summary, scan_project  # noqa: E402
from dead_code import detect_dead_code, format_dead_code_report  # noqa: E402
from complexity import calculate_complexity, format_complexity_report  # noqa: E402
from impact import analyse_impact, format_impact_report  # noqa: E402

LOGGER = logging.getLogger("ai_lens.mcp")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[ai-lens] %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)

try:  # pragma: no cover - depends on optional runtime dependency
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None


def _require_absolute_project_path(project_path: str) -> Path:
    path = Path(project_path).expanduser()
    if not path.is_absolute():
        raise ValueError("project_path must be an absolute path")
    return normalize_project_path(path)


def _format_symbol_read(result: dict | None) -> str:
    if result is None:
        return "Symbol not found in indexed ranges."
    symbol = result["symbol"]
    return "\n".join(
        [
            f"### {result['path']}:{result['line_start']}-{result['line_end']}",
            f"```{result['language']}",
            result["content"],
            "```",
            f"Symbol: {symbol['type']} {symbol['name']}",
            f"Signature: {symbol['signature']}",
        ]
    )


async def _ensure_index(project_path: str) -> tuple[dict, Path, bool]:
    absolute = _require_absolute_project_path(project_path)
    return await asyncio.to_thread(ensure_index, absolute)


if FastMCP is not None:  # pragma: no branch
    server = FastMCP("ai-lens")

    @server.tool()
    async def ai_lens_scan(project_path: str, force: bool = False) -> str:
        """Scan a project and build or refresh the ai-lens index."""
        try:
            absolute = _require_absolute_project_path(project_path)
            manifest = await asyncio.to_thread(scan_project, str(absolute), force=force, quiet=True)
            return format_scan_summary(manifest)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("scan failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("scan unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during scan: {exc}"


    @server.tool()
    async def ai_lens_query_symbol(project_path: str, symbol_name: str) -> str:
        """Find a symbol by name and return ranked matches."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(query_symbol, project_path, symbol_name)
            return format_query_result(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("query_symbol failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("query_symbol unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during symbol query: {exc}"


    @server.tool()
    async def ai_lens_query_related(project_path: str, keyword: str, max_results: int = 10) -> str:
        """Find files or symbols related to a keyword or concept."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(query_related, project_path, keyword, top=max_results)
            return format_query_result(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("query_related failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("query_related unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during related query: {exc}"


    @server.tool()
    async def ai_lens_architecture(project_path: str) -> str:
        """Return a ranked architecture overview for a project."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(query_architecture, project_path)
            return format_query_result(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("architecture query failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("architecture unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during architecture query: {exc}"


    @server.tool()
    async def ai_lens_dependents(project_path: str, file_path: str) -> str:
        """Return files that depend on a given indexed file."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(query_dependents, project_path, file_path)
            return format_query_result(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("dependents query failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("dependents unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during dependents query: {exc}"


    @server.tool()
    async def ai_lens_read_symbol(project_path: str, file_path: str, symbol_name: str) -> str:
        """Read only the indexed line range for a single symbol."""
        try:
            manifest, _, _ = await _ensure_index(project_path)
            result = await asyncio.to_thread(
                read_symbol_range,
                project_path,
                file_path,
                symbol_name,
                manifest=manifest,
            )
            return _format_symbol_read(result)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("read_symbol failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("read_symbol unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during symbol read: {exc}"


    @server.tool()
    async def ai_lens_call_chain(
        project_path: str,
        symbol_name: str,
        depth: int = 3,
        direction: str = "down",
    ) -> str:
        """Trace the call chain of a symbol."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(
                query_call_chain,
                project_path,
                symbol_name,
                depth=depth,
                direction=direction,
            )
            return format_query_result(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("call_chain failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("call_chain unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during call chain query: {exc}"


    @server.tool()
    async def ai_lens_semantic_search(
        project_path: str,
        query: str,
        max_results: int = 10,
    ) -> str:
        """Find symbols by meaning instead of exact text match."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(
                query_semantic,
                project_path,
                query,
                top=max_results,
            )
            return format_query_result(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("semantic_search failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("semantic_search unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during semantic search: {exc}"

    @server.tool()
    async def ai_lens_dead_code(project_path: str, include_tests: bool = False) -> str:
        """Detect potentially dead code (unused symbols and unreferenced files)."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(detect_dead_code, project_path, include_tests=include_tests)
            return format_dead_code_report(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("dead_code failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("dead_code unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during dead code analysis: {exc}"


    @server.tool()
    async def ai_lens_complexity(
        project_path: str,
        max_results: int = 20,
        sort_by: str = "cyclomatic",
    ) -> str:
        """Calculate complexity metrics (cyclomatic, LOC, params) for functions."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(
                calculate_complexity, project_path, max_results=max_results, sort_by=sort_by,
            )
            return format_complexity_report(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("complexity failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("complexity unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during complexity analysis: {exc}"


    @server.tool()
    async def ai_lens_impact(
        project_path: str,
        changed_files: list[str],
        max_depth: int = 5,
    ) -> str:
        """Analyse the downstream impact of changing specific files."""
        try:
            await _ensure_index(project_path)
            payload = await asyncio.to_thread(
                analyse_impact, project_path, changed_files, max_depth=max_depth,
            )
            return format_impact_report(payload)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("impact failed: %s", exc)
            return f"Error: {exc}"
        except Exception as exc:
            LOGGER.error("impact unexpected error: %s", exc, exc_info=True)
            return f"Unexpected error during impact analysis: {exc}"


    @server.resource("ai-lens://index/{project_path}")
    async def get_index(project_path: str) -> str:
        """Return the current manifest as JSON text."""

        manifest, _, _ = await _ensure_index(project_path)
        return json.dumps(manifest, indent=2, ensure_ascii=False)


    @server.resource("ai-lens://tree/{project_path}")
    async def get_tree(project_path: str) -> str:
        """Return the current indexed directory tree."""

        manifest, _, _ = await _ensure_index(project_path)
        return render_tree(manifest.get("tree", {}))


def main() -> int:
    if FastMCP is None:
        LOGGER.error("The 'mcp' package is not installed. Install it with: pip install mcp")
        return 2
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
