from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from .models import Alias, Entity, NormalizedData
from .reporting import alias_tag

SSB_COLUMNS = [
    "PartyKey",
    "PartyType",
    "PartyId1Type",
    "PartyId1Value",
    "PartyId1IDCountry",
    "PartyId2Type",
    "PartyId2Value",
    "PartyId2IDCountry",
    "PartyId3Type",
    "PartyId3Value",
    "PartyId3IDCountry",
    "PrimaryFirstName",
    "PrimaryMiddleName",
    "PrimaryLastName",
    "PrimaryMaidenName",
    "PrimaryFullName",
    "PrimaryIsBrokenName",
    "Alias1FirstName",
    "Alias1MiddleName",
    "Alias1LastName",
    "Alias1MaidenName",
    "Alias1FullName",
    "Alias1IsBrokenName",
    "Alias2FirstName",
    "Alias2MiddleName",
    "Alias2LastName",
    "Alias2MaidenName",
    "Alias2FullName",
    "Alias2IsBrokenName",
    "Alias3FirstName",
    "Alias3MiddleName",
    "Alias3LastName",
    "Alias3MaidenName",
    "Alias3FullName",
    "Alias3IsBrokenName",
    "Address1Line1",
    "Address1Line2",
    "Address1City",
    "Address1ZipCode",
    "Address1Country",
    "Address1stateProvince",
    "Address2Line1",
    "Address2Line2",
    "Address2City",
    "Address2ZipCode",
    "Address2Country",
    "Address2stateProvince",
    "Address3Line1",
    "Address3Line2",
    "Address3City",
    "Address3ZipCode",
    "Address3Country",
    "Address3stateProvince",
    "DateOfBirth",
    "YearOfBirth",
    "BirthCountry",
    "BirthLocation",
    "NationalityCountry1",
    "NationalityCountry2",
    "NationalityCountry3",
    "Gender",
    "Title",
    "BusinessUnit",
    "CustomField1- EntityStatus",
    "CustomField2- DBA",
    "CustomField3",
    "CustomField4",
    "CustomField5",
    "CustomField6- IsChildEntityAvailable",
    "CustomField7",
    "CustomField8",
    "CustomField9",
    "CustomField10",
    "CustomField11",
    "CustomField12",
    "CustomField13",
    "CustomField14",
    "CustomField15",
    "CustomField16- SourceSystemCode",
    "CustomField17-Previous_eligibility_Status",
    "CustomField18- ParentEntityId",
    "CustomField19-Source System Desc",
    "CustomField20-Email",
]
SSB_HEADER = "|".join(SSB_COLUMNS)
SSB_NAME_COLUMNS = [
    "PrimaryFirstName",
    "PrimaryMiddleName",
    "PrimaryLastName",
    "PrimaryFullName",
    "PrimaryIsBrokenName",
    "Alias1FirstName",
    "Alias1MiddleName",
    "Alias1LastName",
    "Alias1FullName",
    "Alias1IsBrokenName",
    "Alias2FirstName",
    "Alias2MiddleName",
    "Alias2LastName",
    "Alias2FullName",
    "Alias2IsBrokenName",
    "Alias3FirstName",
    "Alias3MiddleName",
    "Alias3LastName",
    "Alias3FullName",
    "Alias3IsBrokenName",
]

_COLUMN_INDEX = {name: index for index, name in enumerate(SSB_COLUMNS)}
_ALIAS_PRIORITY = {"strong": 0, "weak": 1, "former": 2}
_INDIVIDUAL_TYPES = {"individual", "person"}
_ORGANIZATION_TYPES = {
    "company",
    "entity",
    "enterprise",
    "organization",
    "organisation",
}
_EXCLUDED_TYPES = {"aircraft", "ship", "vessel"}


@dataclass(frozen=True)
class SsbExportResult:
    source: str
    individual_path: Path
    organization_path: Path
    individuals: int
    organizations: int
    excluded: int
    aliases_omitted: int


def _clean(value: object) -> str:
    """Make one unquoted value safe for a physical pipe-delimited row."""
    return re.sub(r"[|\r\n\t]+", " ", str(value or "")).strip()


def _party_kind(record_type: str) -> str | None:
    normalized = _clean(record_type).casefold()
    if normalized in _INDIVIDUAL_TYPES:
        return "individual"
    if normalized in _ORGANIZATION_TYPES:
        return "organization"
    if normalized in _EXCLUDED_TYPES:
        return None
    return None


def ordered_aliases(data: NormalizedData) -> dict[str, list[Alias]]:
    primary_names = {
        entity.entity_id: _clean(entity.primary_name).casefold()
        for entity in data.entities
    }
    grouped: dict[str, dict[str, tuple[int, int, Alias]]] = defaultdict(dict)
    sequence = 0
    for item in data.aliases:
        has_parts = bool(item.first_name or item.middle_name or item.last_name)
        values = [item.alias] if has_parts else re.split(r";\s+", item.alias)
        for raw_value in values:
            value = " ".join(str(raw_value or "").split())
            if not value or value.casefold() == primary_names.get(item.entity_id, ""):
                continue
            tag = alias_tag(item.quality)
            candidate = replace(item, alias=value)
            key = value.casefold()
            existing = grouped[item.entity_id].get(key)
            if existing is None:
                grouped[item.entity_id][key] = (
                    _ALIAS_PRIORITY[tag],
                    sequence,
                    candidate,
                )
                sequence += 1
            elif _ALIAS_PRIORITY[tag] < existing[0]:
                grouped[item.entity_id][key] = (
                    _ALIAS_PRIORITY[tag],
                    existing[1],
                    candidate,
                )
    return {
        entity_id: [value[2] for value in sorted(values.values())]
        for entity_id, values in grouped.items()
    }


def _set(row: list[str], column: str, value: object) -> None:
    row[_COLUMN_INDEX[column]] = _clean(value)


def _name_has_parts(first: str, middle: str, last: str) -> bool:
    return bool(first.strip() or middle.strip() or last.strip())


def _map_primary(row: list[str], entity: Entity, party_kind: str) -> None:
    if party_kind == "organization":
        _set(row, "PrimaryFullName", entity.primary_name)
        return
    if _name_has_parts(
        entity.primary_first_name,
        entity.primary_middle_name,
        entity.primary_last_name,
    ):
        _set(row, "PrimaryFirstName", entity.primary_first_name)
        _set(row, "PrimaryMiddleName", entity.primary_middle_name)
        _set(row, "PrimaryLastName", entity.primary_last_name)
        _set(row, "PrimaryIsBrokenName", "true")
    else:
        _set(row, "PrimaryLastName", entity.primary_name)


def _map_alias(
    row: list[str],
    item: Alias,
    alias_number: int,
    party_kind: str,
) -> None:
    prefix = f"Alias{alias_number}"
    if party_kind == "organization":
        _set(row, f"{prefix}FullName", item.alias)
        return
    if _name_has_parts(item.first_name, item.middle_name, item.last_name):
        _set(row, f"{prefix}FirstName", item.first_name)
        _set(row, f"{prefix}MiddleName", item.middle_name)
        _set(row, f"{prefix}LastName", item.last_name)
        _set(row, f"{prefix}IsBrokenName", "true")
    else:
        _set(row, f"{prefix}LastName", item.alias)


def project_name_fields(entity: Entity, aliases: list[Alias]) -> dict[str, str]:
    """Return the exact primary and three-alias projection used by SSB."""
    row = [""] * len(SSB_COLUMNS)
    party_kind = _party_kind(entity.record_type) or "organization"
    _map_primary(row, entity, party_kind)
    for alias_number, item in enumerate(aliases[:3], start=1):
        _map_alias(row, item, alias_number, party_kind)
    return {column: row[_COLUMN_INDEX[column]] for column in SSB_NAME_COLUMNS}


def _render_row(
    entity: Entity,
    aliases: list[Alias],
    source_code: str,
    party_kind: str,
) -> str:
    row = [""] * len(SSB_COLUMNS)
    entity_id = _clean(entity.entity_id)
    if not entity_id:
        raise ValueError("SSB export cannot create a PartyKey from a blank entity ID")
    _set(row, "PartyKey", f"{source_code}_{entity_id}")
    _set(row, "PartyType", party_kind.title())
    _set(row, "PartyId1Type", source_code)
    _set(row, "PartyId1Value", entity_id)
    for column, value in project_name_fields(entity, aliases).items():
        _set(row, column, value)
    _set(row, "CustomField16- SourceSystemCode", source_code)
    line = "|".join(row)
    if len(row) != 83 or line.count("|") != 82:
        raise ValueError("Invalid SSB row: expected 83 fields and 82 delimiters")
    return line


def _write_file(path: Path, lines: list[str]) -> None:
    if len(SSB_COLUMNS) != 83 or SSB_HEADER.count("|") != 82:
        raise ValueError("Invalid SSB header: expected 83 fields and 82 delimiters")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(SSB_HEADER)
        handle.write("\n")
        for line in lines:
            if line.count("|") != 82:
                raise ValueError(f"Invalid SSB delimiter count for {path.name}")
            handle.write(line)
            handle.write("\n")


def export_ssb(
    data: NormalizedData,
    output_dir: Path,
    *,
    source: str | None = None,
) -> SsbExportResult:
    """Export two strict SSB files: individuals and organizations."""
    sources = {_clean(entity.source).casefold() for entity in data.entities}
    sources.discard("")
    requested_source = _clean(source).casefold()
    if requested_source and sources and sources != {requested_source}:
        raise ValueError("SSB export source does not match its entity records")
    if not requested_source and len(sources) != 1:
        raise ValueError("SSB export requires exactly one source per output directory")
    source_name = requested_source or next(iter(sources))
    source_code = source_name.upper()
    aliases_by_entity = ordered_aliases(data)
    entities = sorted(data.entities, key=lambda item: _clean(item.entity_id).casefold())
    rows: dict[str, list[str]] = {"individual": [], "organization": []}
    keys: set[str] = set()
    excluded = 0
    aliases_omitted = 0
    for entity in entities:
        party_kind = _party_kind(entity.record_type)
        if party_kind is None:
            excluded += 1
            continue
        party_key = f"{source_code}_{_clean(entity.entity_id)}"
        if party_key in keys:
            raise ValueError(f"Duplicate SSB PartyKey: {party_key}")
        keys.add(party_key)
        aliases = aliases_by_entity.get(entity.entity_id, [])
        aliases_omitted += max(0, len(aliases) - 3)
        rows[party_kind].append(
            _render_row(entity, aliases, source_code, party_kind)
        )

    individual_path = output_dir / f"{source_name}-individuals.csv"
    organization_path = output_dir / f"{source_name}-organizations.csv"
    _write_file(individual_path, rows["individual"])
    _write_file(organization_path, rows["organization"])
    return SsbExportResult(
        source=source_name,
        individual_path=individual_path,
        organization_path=organization_path,
        individuals=len(rows["individual"]),
        organizations=len(rows["organization"]),
        excluded=excluded,
        aliases_omitted=aliases_omitted,
    )
