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

        absolute = _require_absolute_project_path(project_path)
        manifest = await asyncio.to_thread(scan_project, str(absolute), force=force, quiet=True)
        return format_scan_summary(manifest)


    @server.tool()
    async def ai_lens_query_symbol(project_path: str, symbol_name: str) -> str:
        """Find a symbol by name and return ranked matches."""

        await _ensure_index(project_path)
        payload = await asyncio.to_thread(query_symbol, project_path, symbol_name)
        return format_query_result(payload)


    @server.tool()
    async def ai_lens_query_related(project_path: str, keyword: str, max_results: int = 10) -> str:
        """Find files or symbols related to a keyword or concept."""

        await _ensure_index(project_path)
        payload = await asyncio.to_thread(query_related, project_path, keyword, top=max_results)
        return format_query_result(payload)


    @server.tool()
    async def ai_lens_architecture(project_path: str) -> str:
        """Return a ranked architecture overview for a project."""

        await _ensure_index(project_path)
        payload = await asyncio.to_thread(query_architecture, project_path)
        return format_query_result(payload)


    @server.tool()
    async def ai_lens_dependents(project_path: str, file_path: str) -> str:
        """Return files that depend on a given indexed file."""

        await _ensure_index(project_path)
        payload = await asyncio.to_thread(query_dependents, project_path, file_path)
        return format_query_result(payload)


    @server.tool()
    async def ai_lens_read_symbol(project_path: str, file_path: str, symbol_name: str) -> str:
        """Read only the indexed line range for a single symbol."""

        manifest, _, _ = await _ensure_index(project_path)
        result = await asyncio.to_thread(
            read_symbol_range,
            project_path,
            file_path,
            symbol_name,
            manifest=manifest,
        )
        return _format_symbol_read(result)


    @server.tool()
    async def ai_lens_call_chain(
        project_path: str,
        symbol_name: str,
        depth: int = 3,
        direction: str = "down",
    ) -> str:
        """Trace the call chain of a symbol."""

        await _ensure_index(project_path)
        payload = await asyncio.to_thread(
            query_call_chain,
            project_path,
            symbol_name,
            depth=depth,
            direction=direction,
        )
        return format_query_result(payload)


    @server.tool()
    async def ai_lens_semantic_search(
        project_path: str,
        query: str,
        max_results: int = 10,
    ) -> str:
        """Find symbols by meaning instead of exact text match."""

        await _ensure_index(project_path)
        payload = await asyncio.to_thread(
            query_semantic,
            project_path,
            query,
            top=max_results,
        )
        return format_query_result(payload)


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
