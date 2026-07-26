from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import SourceConfig
from .downloader import DownloadResult, download_source
from .exporter import export_data
from .parsers import parse
from .reporting import ParsingStats, build_parsing_stats

LOGGER = logging.getLogger("sanctions_parser.pipeline")
PARSER_SCHEMA_VERSION = 6


@dataclass
class SourceOutcome:
    source: str
    status: str
    detail: str
    entities: int = 0
    report: ParsingStats | None = None


def _cached_raw_path(project_root: Path, source_name: str) -> Path:
    state_path = project_root / ".state" / f"{source_name}.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        raw_path = Path(state["path"])
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{source_name} is unchanged but its cached raw-file state is invalid"
        ) from exc
    if not raw_path.is_file():
        raise ValueError(
            f"{source_name} is unchanged but cached raw file is missing: {raw_path}"
        )
    return raw_path


def _required_exports(output_dir: Path, formats: set[str], tables: set[str]) -> bool:
    if "excel" in formats and not (output_dir / "excel" / "sanctions.xlsx").is_file():
        return False
    if "csv" in formats and any(
        not (output_dir / "csv" / f"{table}.csv").is_file() for table in tables
    ):
        return False
    return not (
        "parquet" in formats
        and any(
            not (output_dir / "parquet" / f"{table}.parquet").is_file()
            for table in tables
        )
    )


def _reusable_output(
    project_root: Path,
    source_name: str,
    checksum: str,
    formats: set[str],
) -> tuple[Path, int, ParsingStats] | None:
    source_root = project_root / "output" / source_name
    if not source_root.exists():
        return None
    for output_dir in sorted(
        (item for item in source_root.iterdir() if item.is_dir()),
        key=lambda item: item.name,
        reverse=True,
    ):
        manifest_path = output_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            manifest.get("checksum") != checksum
            or manifest.get("parser_schema_version") != PARSER_SCHEMA_VERSION
            or not formats.issubset(set(manifest.get("formats", [])))
        ):
            continue
        tables = set(manifest.get("tables", {}))
        report = ParsingStats.from_dict(manifest.get("parsing_summary"))
        if _required_exports(output_dir, formats, tables) and report is not None:
            return (
                output_dir,
                int(manifest.get("tables", {}).get("entity", 0)),
                report,
            )
    return None


def process_source(
    source: SourceConfig,
    project_root: Path,
    export_formats: set[str] | None = None,
    show_download_progress: bool = True,
) -> SourceOutcome:
    started = time.monotonic()
    result: DownloadResult = download_source(
        source,
        project_root / "raw",
        project_root / ".state",
        show_progress=show_download_progress,
    )
    formats = export_formats or {"csv", "excel", "parquet"}
    if not result.changed:
        reusable = _reusable_output(project_root, source.name, result.checksum, formats)
        if reusable is not None:
            output_dir, entities, report = reusable
            return SourceOutcome(
                source.name,
                "unchanged",
                f"Checksum matched; exports already current: {output_dir}",
                entities=entities,
                report=report,
            )
        raw_path = _cached_raw_path(project_root, source.name)
        LOGGER.info(
            "%s is unchanged but has no current complete export; parsing %s",
            source.name,
            raw_path,
        )
    elif result.path is not None:
        raw_path = result.path
    else:
        raise ValueError(f"{source.name} download did not provide a raw XML file")

    data = parse(source.parser, raw_path)
    if not data.entities:
        raise ValueError(
            f"{source.name} parser produced zero entities; export was stopped"
        )
    report = build_parsing_stats(data)
    run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = project_root / "output" / source.name / run_stamp
    export_data(
        data,
        target,
        export_csv="csv" in formats,
        excel="excel" in formats,
        parquet="parquet" in formats,
    )
    table_counts = {name: len(rows) for name, rows in data.tables().items()}
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "source": source.name,
                "checksum": result.checksum,
                "parser_schema_version": PARSER_SCHEMA_VERSION,
                "formats": sorted(formats),
                "raw_path": str(raw_path),
                "tables": table_counts,
                "parsing_summary": report.to_dict(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    elapsed = time.monotonic() - started
    LOGGER.info(
        "%s completed: entities=%d aliases=%d addresses=%d seconds=%.2f output=%s",
        source.name,
        len(data.entities),
        len(data.aliases),
        len(data.addresses),
        elapsed,
        target,
    )
    return SourceOutcome(
        source.name,
        "processed",
        str(target),
        entities=len(data.entities),
        report=report,
    )
