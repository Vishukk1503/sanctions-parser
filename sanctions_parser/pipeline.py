from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config import SourceConfig
from .delta import (
    DELTA_SCHEMA_VERSION,
    DeltaSnapshot,
    DeltaSummary,
    build_snapshot,
    compare_snapshots,
    export_delta_report,
    load_baseline_ref,
    load_snapshot,
    refresh_unchanged_baseline,
    save_baseline,
)
from .downloader import DownloadResult, download_source, sha256_file
from .exporter import export_data
from .locking import source_lock
from .parsers import parse
from .reporting import ParsingStats, build_parsing_stats

LOGGER = logging.getLogger("sanctions_parser.pipeline")
PARSER_SCHEMA_VERSION = 10


@dataclass
class SourceOutcome:
    source: str
    status: str
    detail: str
    entities: int = 0
    report: ParsingStats | None = None


@dataclass
class DeltaOutcome:
    source: str
    status: str
    detail: str
    summary: DeltaSummary | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RawBaselineCandidate:
    source: str
    path: Path
    archive_date: str


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


def _required_exports(
    output_dir: Path,
    formats: set[str],
    tables: set[str],
    source_name: str,
) -> bool:
    if "excel" in formats and not (output_dir / "excel" / "sanctions.xlsx").is_file():
        return False
    if "csv" in formats and any(
        not (output_dir / "csv" / f"{table}.csv").is_file() for table in tables
    ):
        return False
    if "ssb" in formats and any(
        not (output_dir / "ssb" / f"{source_name}-{party_type}.csv").is_file()
        for party_type in ("individuals", "organizations")
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
        if (
            _required_exports(output_dir, formats, tables, source_name)
            and report is not None
        ):
            return (
                output_dir,
                int(manifest.get("tables", {}).get("entity", 0)),
                report,
            )
    return None


def _process_source_unlocked(
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
    formats = export_formats or {"csv", "excel", "parquet", "ssb"}
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
        ssb="ssb" in formats,
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


def process_source(
    source: SourceConfig,
    project_root: Path,
    export_formats: set[str] | None = None,
    show_download_progress: bool = True,
) -> SourceOutcome:
    with source_lock(project_root / ".state", source.name):
        return _process_source_unlocked(
            source,
            project_root,
            export_formats=export_formats,
            show_download_progress=show_download_progress,
        )


def _find_raw_by_checksum(
    project_root: Path,
    source_name: str,
    checksum: str,
) -> Path | None:
    source_root = project_root / "raw" / source_name
    if not source_root.exists():
        return None
    for candidate in source_root.rglob("*"):
        if not candidate.is_file() or candidate.name.startswith("."):
            continue
        try:
            if sha256_file(candidate) == checksum:
                return candidate
        except OSError:
            continue
    return None


def _raw_archive_date(path: Path, source_root: Path) -> str:
    try:
        relative_parts = path.relative_to(source_root).parts[:-1]
    except ValueError:
        relative_parts = ()
    for part in relative_parts:
        try:
            return datetime.strptime(part, "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).date().isoformat()


def find_existing_raw_baseline(
    source: SourceConfig,
    project_root: Path,
) -> RawBaselineCandidate | None:
    """Find the newest archived XML that can seed a missing delta checkpoint."""
    if load_baseline_ref(project_root, source.name) is not None:
        return None
    source_root = project_root / "raw" / source.name
    if not source_root.is_dir():
        return None
    candidates: list[tuple[tuple[str, int, str], RawBaselineCandidate]] = []
    for path in source_root.rglob("*"):
        if (
            not path.is_file()
            or path.suffix.casefold() != ".xml"
            or any(part.startswith(".") for part in path.parts)
        ):
            continue
        try:
            archive_date = _raw_archive_date(path, source_root)
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        candidate = RawBaselineCandidate(
            source=source.name,
            path=path,
            archive_date=archive_date,
        )
        candidates.append(
            (
                (archive_date, modified, str(path)),
                candidate,
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _raw_baseline_created_at(candidate: RawBaselineCandidate) -> str:
    try:
        archive_day = datetime.strptime(candidate.archive_date, "%Y-%m-%d")
        return archive_day.replace(tzinfo=UTC).isoformat()
    except ValueError:
        return datetime.fromtimestamp(candidate.path.stat().st_mtime, UTC).isoformat()


def _import_initial_delta_baseline(
    source: SourceConfig,
    project_root: Path,
    candidate: RawBaselineCandidate,
) -> None:
    if candidate.source != source.name:
        raise ValueError(
            f"Cannot use {candidate.source.upper()} raw XML as "
            f"{source.name.upper()} baseline"
        )
    source_root = (project_root / "raw" / source.name).resolve()
    raw_path = candidate.path.resolve()
    try:
        raw_path.relative_to(source_root)
    except ValueError as exc:
        raise ValueError(
            f"{source.name.upper()} baseline XML must be inside {source_root}"
        ) from exc
    if not raw_path.is_file() or raw_path.suffix.casefold() != ".xml":
        raise ValueError(f"{source.name.upper()} baseline XML is missing: {raw_path}")
    checksum = sha256_file(raw_path)
    created_at = _raw_baseline_created_at(candidate)
    snapshot = _snapshot_from_raw(
        source,
        raw_path,
        checksum,
        created_at=created_at,
    )
    save_baseline(project_root, snapshot, last_checked_at=created_at)
    LOGGER.info(
        "%s imported initial delta baseline from %s checksum=%s",
        source.name,
        raw_path,
        checksum,
    )


def _snapshot_from_raw(
    source: SourceConfig,
    raw_path: Path,
    checksum: str,
    *,
    created_at: str | None = None,
) -> DeltaSnapshot:
    data = parse(source.parser, raw_path)
    if not data.entities:
        raise ValueError(
            f"{source.name} parser produced zero entities; "
            "delta baseline was not changed"
        )
    return build_snapshot(
        data,
        source=source.name,
        checksum=checksum,
        parser_schema_version=PARSER_SCHEMA_VERSION,
        raw_path=raw_path,
        created_at=created_at,
    )


def _load_previous_delta_snapshot(
    source: SourceConfig,
    project_root: Path,
):
    baseline = load_baseline_ref(project_root, source.name)
    if baseline is None:
        return None, None
    if (
        baseline.parser_schema_version == PARSER_SCHEMA_VERSION
        and baseline.snapshot_path.is_file()
    ):
        try:
            stored = load_snapshot(baseline.snapshot_path)
            return baseline, DeltaSnapshot(
                source=stored.source,
                checksum=stored.checksum,
                parser_schema_version=stored.parser_schema_version,
                raw_path=str(baseline.raw_path),
                created_at=baseline.created_at,
                tables=stored.tables,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            LOGGER.warning(
                "%s delta snapshot is unreadable; rebuilding it from raw XML",
                source.name,
            )

    raw_path = baseline.raw_path
    if not raw_path.is_file():
        recovered = _find_raw_by_checksum(
            project_root,
            source.name,
            baseline.checksum,
        )
        if recovered is None:
            raise ValueError(
                f"{source.name.upper()} delta baseline raw XML is missing and could "
                "not be recovered; the previous checkpoint was preserved"
            )
        raw_path = recovered
    snapshot = _snapshot_from_raw(
        source,
        raw_path,
        baseline.checksum,
        created_at=baseline.created_at,
    )
    return baseline, snapshot


def _unique_delta_target(project_root: Path, source_name: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return project_root / "output" / "delta" / source_name / stamp


def run_delta_source(
    source: SourceConfig,
    project_root: Path,
    *,
    show_download_progress: bool = True,
    initial_baseline: RawBaselineCandidate | None = None,
) -> DeltaOutcome:
    """Run a source comparison and advance its baseline only after export succeeds."""
    with source_lock(project_root / ".state", source.name):
        if initial_baseline is not None and load_baseline_ref(
            project_root, source.name
        ) is None:
            _import_initial_delta_baseline(source, project_root, initial_baseline)
        result = download_source(
            source,
            project_root / "raw",
            project_root / ".state",
            show_progress=show_download_progress,
        )
        raw_path = (
            result.path
            if result.path is not None
            else _cached_raw_path(project_root, source.name)
        )
        baseline, previous = _load_previous_delta_snapshot(
            source,
            project_root,
        )
        checked_at = datetime.now(UTC).isoformat()

        if (
            previous is not None
            and previous.checksum == result.checksum
            and previous.parser_schema_version == PARSER_SCHEMA_VERSION
        ):
            current = DeltaSnapshot(
                source=previous.source,
                checksum=previous.checksum,
                parser_schema_version=previous.parser_schema_version,
                raw_path=str(raw_path),
                created_at=checked_at,
                tables=previous.tables,
            )
        else:
            current = _snapshot_from_raw(
                source,
                raw_path,
                result.checksum,
                created_at=checked_at,
            )

        comparison = compare_snapshots(previous, current, checked_at=checked_at)
        warnings: list[str] = []
        if previous is not None and previous.entity_count:
            reduction = previous.entity_count - current.entity_count
            if reduction > 0 and current.entity_count < previous.entity_count * 0.8:
                warnings.append(
                    "Current record count is more than 20% below the previous "
                    "checkpoint; review the report for an unusual source change"
                )

        target = _unique_delta_target(project_root, source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}-{uuid4().hex}.part"
        try:
            ssb_result = export_delta_report(comparison, temporary)
            manifest = {
                "delta_schema_version": DELTA_SCHEMA_VERSION,
                "parser_schema_version": PARSER_SCHEMA_VERSION,
                "source": source.name,
                "raw_path": str(raw_path),
                "summary": comparison.summary.to_dict(),
                "formats": ["csv", "excel", "parquet", "ssb"],
                "ssb_summary": {
                    "individuals": ssb_result.individuals,
                    "organizations": ssb_result.organizations,
                    "excluded": ssb_result.excluded,
                    "aliases_omitted_after_three": ssb_result.aliases_omitted,
                },
                "warnings": warnings,
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )
            temporary.replace(target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

        # This is deliberately last: a failed report must not move the checkpoint.
        if (
            baseline is not None
            and baseline.checksum == current.checksum
            and baseline.parser_schema_version == current.parser_schema_version
            and comparison.summary.result == "no_changes"
        ):
            refresh_unchanged_baseline(
                project_root,
                baseline,
                raw_path=raw_path,
                checked_at=checked_at,
            )
        else:
            save_baseline(project_root, current, last_checked_at=checked_at)
        status = comparison.summary.result
        LOGGER.info(
            "%s delta completed: status=%s new=%d updated=%d removed=%d output=%s",
            source.name,
            status,
            comparison.summary.new,
            comparison.summary.updated,
            comparison.summary.removed,
            target,
        )
        return DeltaOutcome(
            source.name,
            status,
            str(target),
            comparison.summary,
            tuple(warnings),
        )
