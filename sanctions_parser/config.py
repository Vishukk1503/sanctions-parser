from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    parser: str
    expected_root: str | None = None
    enabled: bool = True
    timeout_seconds: int = 120
    retries: int = 3
    user_agent: str = "sanctions-parser/1.0"
    chunk_size: int = 1024 * 1024


def load_sources(path: Path) -> dict[str, SourceConfig]:
    """Load enabled and disabled sources, merging file-level defaults."""
    with path.open("r", encoding="utf-8") as handle:
        document: dict[str, Any] = yaml.safe_load(handle) or {}
    defaults = document.get("defaults", {})
    raw_sources = document.get("sources", document)
    result: dict[str, SourceConfig] = {}
    for name, values in raw_sources.items():
        if name == "defaults" or not isinstance(values, dict):
            continue
        merged = {**defaults, **values}
        if not merged.get("url"):
            raise ValueError(f"Source {name!r} has no URL")
        result[name] = SourceConfig(
            name=name,
            url=str(merged["url"]),
            parser=str(merged.get("parser", name)),
            expected_root=(
                str(merged["expected_root"]) if merged.get("expected_root") else None
            ),
            enabled=bool(merged.get("enabled", True)),
            timeout_seconds=int(merged.get("timeout_seconds", 120)),
            retries=int(merged.get("retries", 3)),
            user_agent=str(merged.get("user_agent", "sanctions-parser/1.0")),
            chunk_size=int(merged.get("chunk_size", 1024 * 1024)),
        )
    return result
