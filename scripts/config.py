#!/usr/bin/env python3

"""Project-level configuration for ai-lens.

Reads `.ai-lens.config.json` from the project root to allow per-project customization:
- Extra ignore patterns (skip_dirs, skip_files, skip_extensions)
- Extra entrypoint files
- Max file size override
- Parallel scanning workers
- Custom language extension mappings
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("ai_lens.config")

DEFAULT_CONFIG: dict[str, Any] = {
    "skip_dirs": [],
    "skip_files": [],
    "skip_extensions": [],
    "extra_entrypoints": [],
    "extra_extensions": {},
    "max_file_bytes": None,
    "max_workers": None,
    "embedding_model": None,
}

_CONFIG_FILENAME = ".ai-lens.config.json"


def load_project_config(project_root: str | Path) -> dict[str, Any]:
    """Load project-level config from `.ai-lens.config.json`.

    Returns DEFAULT_CONFIG merged with whatever is found on disk.
    Missing keys use defaults; unknown keys are preserved for forward compat.
    """
    root = Path(project_root)
    config_path = root / _CONFIG_FILENAME
    config = dict(DEFAULT_CONFIG)

    if not config_path.exists():
        return config

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            LOGGER.warning("Config file %s is not a JSON object, ignoring.", config_path)
            return config

        # Merge list fields (append, don't replace)
        for list_key in ("skip_dirs", "skip_files", "skip_extensions", "extra_entrypoints"):
            if list_key in raw and isinstance(raw[list_key], list):
                config[list_key] = raw[list_key]

        # Merge dict fields (update, don't replace)
        if "extra_extensions" in raw and isinstance(raw["extra_extensions"], dict):
            config["extra_extensions"] = raw["extra_extensions"]

        # Scalar fields (override)
        for scalar_key in ("max_file_bytes", "max_workers", "embedding_model"):
            if scalar_key in raw:
                config[scalar_key] = raw[scalar_key]

        # Preserve any unknown keys for forward compat
        for key, value in raw.items():
            if key not in config:
                config[key] = value

        LOGGER.info("Loaded project config from %s", config_path)
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Failed to read config %s: %s", config_path, exc)

    return config


def apply_config_to_scan(
    config: dict[str, Any],
    skip_dirs: set[str],
    skip_files: set[str],
    entrypoint_files: set[str],
    extension_language: dict[str, str],
    max_file_bytes: int,
) -> tuple[set[str], set[str], set[str], dict[str, str], int]:
    """Apply project config overrides to scan parameters.

    Returns updated (skip_dirs, skip_files, entrypoint_files, extension_language, max_file_bytes).
    """
    # Add extra skip dirs
    for d in config.get("skip_dirs", []):
        skip_dirs.add(d)

    # Add extra skip files
    for f in config.get("skip_files", []):
        skip_files.add(f)

    # Add extra entrypoints
    for e in config.get("extra_entrypoints", []):
        entrypoint_files.add(e)

    # Add extra extension mappings
    for ext, lang in config.get("extra_extensions", {}).items():
        if not ext.startswith("."):
            ext = f".{ext}"
        extension_language[ext] = lang

    # Override max file bytes
    if config.get("max_file_bytes") is not None:
        max_file_bytes = int(config["max_file_bytes"])

    return skip_dirs, skip_files, entrypoint_files, extension_language, max_file_bytes
