from __future__ import annotations

from pathlib import Path

from scripts.index_store import read_symbol_range
from scripts.query import query_architecture, query_call_chain, query_dependents, query_symbol
from scripts.scan import scan_project


def test_scan_project_writes_index_and_symbol_graph(sample_project: Path) -> None:
    manifest = scan_project(str(sample_project), force=True, quiet=True)

    assert manifest["project"]["name"] == "sample_project"
    assert manifest["project"]["total_files"] >= 4
    assert (sample_project / ".ai-lens" / "manifest.json").exists()
    assert (sample_project / ".ai-lens" / "symbol_graph.json").exists()
    assert manifest["symbol_graph"]["summary"]["node_count"] >= 4


def test_symbol_and_dependents_queries_return_expected_paths(sample_project: Path) -> None:
    scan_project(str(sample_project), force=True, quiet=True)

    symbol_payload = query_symbol(sample_project, "authenticate")
    dependent_payload = query_dependents(sample_project, "service.py")

    assert symbol_payload["mode"] == "symbol"
    assert any(result["path"] == "service.py" for result in symbol_payload["results"])
    assert dependent_payload["mode"] == "dependents"
    assert {result["path"] for result in dependent_payload["results"]} == {"main.py", "report.py"}


def test_call_chain_and_symbol_range_cover_function_body(sample_project: Path) -> None:
    scan_project(str(sample_project), force=True, quiet=True)

    call_chain = query_call_chain(sample_project, "authenticate", depth=2)
    implementation = read_symbol_range(sample_project, "service.py", "authenticate")

    assert call_chain["mode"] == "call_chain"
    assert call_chain["results"]
    root_tree = call_chain["results"][0]["tree"]
    assert any(child["name"] == "helper" for child in root_tree["children"])
    assert implementation is not None
    assert implementation["line_end"] > implementation["line_start"]
    assert "cleaned = helper(token)" in implementation["content"]


def test_architecture_query_exposes_ranked_files(sample_project: Path) -> None:
    scan_project(str(sample_project), force=True, quiet=True)

    payload = query_architecture(sample_project, top=5)

    assert payload["mode"] == "architecture"
    assert payload["graph_stats"]["nodes"] >= 4
    assert any(item["path"] == "main.py" for item in payload["top_files"])
