from __future__ import annotations

import time
from pathlib import Path

from scripts.index_store import ensure_index
from scripts.scan import scan_project
from scripts.semantic import query_semantic, semantic_status


def test_ensure_index_refreshes_after_source_change(sample_project: Path) -> None:
    scan_project(str(sample_project), force=True, quiet=True)

    manifest, _, refreshed = ensure_index(sample_project, scan_project_func=scan_project)
    assert refreshed is False
    assert manifest["project"]["total_files"] >= 4

    service_file = sample_project / "service.py"
    original = service_file.read_text(encoding="utf-8")
    time.sleep(0.02)
    service_file.write_text(original + "\n# mutation for refresh test\n", encoding="utf-8")

    refreshed_manifest, _, refreshed = ensure_index(sample_project, scan_project_func=scan_project)
    assert refreshed is True
    assert refreshed_manifest["stats"]["changed_files"] >= 1


def test_semantic_query_works_in_lexical_mode_without_optional_deps(sample_project: Path) -> None:
    scan_project(str(sample_project), force=True, quiet=True)

    payload = query_semantic(sample_project, "authentication helper", engine="lexical", top_k=5)
    status = semantic_status(sample_project)

    assert payload["mode"] == "semantic"
    assert payload["engine"] == "lexical"
    assert payload["results"]
    assert any(result["path"] == "service.py" for result in payload["results"])
    assert "dependencies" in status
