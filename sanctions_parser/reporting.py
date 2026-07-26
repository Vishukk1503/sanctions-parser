from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import NormalizedData


@dataclass(frozen=True)
class ParsingStats:
    entities: int
    individuals: int
    organizations: int
    vessels: int
    aircraft: int
    other_entities: int
    aliases: int
    strong_aliases: int
    weak_aliases: int
    former_aliases: int
    entities_with_aliases: int
    addresses: int
    documents: int
    nationalities: int
    programs: int
    dates_of_birth: int
    places_of_birth: int
    relationships: int
    designations: int
    regulations: int
    contacts: int
    sanctions: int
    blank_primary_names: int
    orphaned_aliases: int

    @property
    def name_issues(self) -> int:
        return self.blank_primary_names + self.orphaned_aliases

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: object) -> ParsingStats | None:
        if not isinstance(values, dict):
            return None
        try:
            return cls(
                **{field: int(values[field]) for field in cls.__dataclass_fields__}
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class StoredParsingReport:
    source: str
    stats: ParsingStats
    output_dir: Path
    raw_path: Path | None
    checksum: str
    parser_schema_version: int
    current: bool


def load_latest_parsing_report(
    project_root: Path,
    source_name: str,
    parser_schema_version: int,
) -> StoredParsingReport | None:
    output_root = project_root / "output" / source_name
    if not output_root.exists():
        return None

    current_checksum = ""
    try:
        state = json.loads(
            (project_root / ".state" / f"{source_name}.json").read_text(
                encoding="utf-8"
            )
        )
        current_checksum = str(state.get("checksum", ""))
    except (OSError, json.JSONDecodeError):
        pass

    candidates = sorted(
        (item for item in output_root.iterdir() if item.is_dir()),
        key=lambda item: item.name,
        reverse=True,
    )
    for output_dir in candidates:
        try:
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            version = int(manifest.get("parser_schema_version", 0))
            stats = ParsingStats.from_dict(manifest.get("parsing_summary"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if version != parser_schema_version or stats is None:
            continue
        checksum = str(manifest.get("checksum", ""))
        raw_value = str(manifest.get("raw_path", "")).strip()
        return StoredParsingReport(
            source=source_name,
            stats=stats,
            output_dir=output_dir,
            raw_path=Path(raw_value) if raw_value else None,
            checksum=checksum,
            parser_schema_version=version,
            current=bool(current_checksum and checksum == current_checksum),
        )
    return None


def alias_tag(quality: str) -> str:
    """Normalize provider-specific alias quality into three report categories."""
    normalized = " ".join(quality.casefold().split())
    if (
        normalized in {"f.k.a.", "fka", "former", "former name", "previous name"}
        or "f.k.a" in normalized
    ):
        return "former"
    if normalized in {"low", "weak", "weak alias"}:
        return "weak"
    return "strong"


def _entity_type(record_type: str) -> str:
    normalized = " ".join(record_type.casefold().split())
    if normalized in {"individual", "person"}:
        return "individual"
    if normalized in {
        "entity",
        "enterprise",
        "organization",
        "organisation",
        "company",
    }:
        return "organization"
    if normalized in {"vessel", "ship"}:
        return "vessel"
    if normalized == "aircraft":
        return "aircraft"
    return "other"


def build_parsing_stats(data: NormalizedData) -> ParsingStats:
    entity_types = Counter(_entity_type(item.record_type) for item in data.entities)
    alias_types = Counter(alias_tag(item.quality) for item in data.aliases)
    entity_ids = {item.entity_id for item in data.entities}
    entities_with_aliases = {
        item.entity_id
        for item in data.aliases
        if item.entity_id in entity_ids and item.alias.strip()
    }
    orphaned_aliases = sum(item.entity_id not in entity_ids for item in data.aliases)

    return ParsingStats(
        entities=len(data.entities),
        individuals=entity_types["individual"],
        organizations=entity_types["organization"],
        vessels=entity_types["vessel"],
        aircraft=entity_types["aircraft"],
        other_entities=entity_types["other"],
        aliases=len(data.aliases),
        strong_aliases=alias_types["strong"],
        weak_aliases=alias_types["weak"],
        former_aliases=alias_types["former"],
        entities_with_aliases=len(entities_with_aliases),
        addresses=len(data.addresses),
        documents=len(data.documents),
        nationalities=len(data.nationalities),
        programs=len(data.programs),
        dates_of_birth=len(data.dates_of_birth),
        places_of_birth=len(data.places_of_birth),
        relationships=len(data.relationships),
        designations=len(data.designations),
        regulations=len(data.regulations),
        contacts=len(data.contacts),
        sanctions=len(data.sanctions),
        blank_primary_names=sum(
            not item.primary_name.strip() for item in data.entities
        ),
        orphaned_aliases=orphaned_aliases,
    )
