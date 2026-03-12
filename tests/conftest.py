from __future__ import annotations

from pathlib import Path
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


@pytest.fixture()
def sample_project(tmp_path: Path) -> Path:
    root = tmp_path / "sample_project"
    root.mkdir()

    _write(
        root / "main.py",
        """
        from service import authenticate
        from report import build_report


        def run_app(token: str) -> bool:
            build_report(token)
            return authenticate(token)
        """,
    )
    _write(
        root / "service.py",
        """
        def authenticate(token: str) -> bool:
            cleaned = helper(token)
            return cleaned == "ok"


        def helper(value: str) -> str:
            return value.strip().lower()
        """,
    )
    _write(
        root / "report.py",
        """
        from service import helper


        def build_report(token: str) -> str:
            return helper(token)
        """,
    )
    _write(
        root / "README.md",
        """
        # Sample Project

        Demo repo for ai-lens tests.
        """,
    )
    return root
