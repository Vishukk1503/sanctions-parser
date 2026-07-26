from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

from .models import NormalizedData
from .reporting import alias_tag

NAME_ALIAS_SHEET = "name+alias"
NAME_ALIAS_COLUMNS = [
    "entity_id",
    "source",
    "record_type",
    "primary_name",
    "aliases",
    "alias_count",
]

ALIAS_TAG_PRIORITY = {
    "strong": 0,
    "weak": 1,
    "former": 2,
}


def _is_latin_name(value: str) -> bool:
    """Return true when every letter in a name belongs to the Latin script."""
    has_letter = False
    for character in value:
        if not character.isalpha():
            continue
        has_letter = True
        if "LATIN" not in unicodedata.name(character, ""):
            return False
    return has_letter


def _name_alias_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return one row per entity with tagged Latin aliases in display order."""
    entity_frame = frames["entity"][
        ["entity_id", "source", "record_type", "primary_name"]
    ].copy()
    alias_frame = frames["alias"][["entity_id", "alias", "quality", "language"]].copy()

    if alias_frame.empty:
        entity_frame["aliases"] = ""
        entity_frame["alias_count"] = 0
        return entity_frame[NAME_ALIAS_COLUMNS]

    primary_names = {
        str(row.entity_id): " ".join(str(row.primary_name or "").split()).casefold()
        for row in entity_frame.itertuples(index=False)
    }
    aliases_by_entity: dict[str, dict[str, tuple[int, str, str]]] = {}
    sequence = 0

    for row in alias_frame.itertuples(index=False):
        entity_id = str(row.entity_id)
        language = str(row.language or "").strip().casefold()
        if language == "original script":
            continue
        quality = str(row.quality or "")
        tag = alias_tag(quality)
        for raw_alias in re.split(r";\s+", str(row.alias or "")):
            alias = " ".join(raw_alias.split())
            if not alias or not _is_latin_name(alias):
                continue
            alias_key = alias.casefold()
            if alias_key == primary_names.get(entity_id, ""):
                continue

            entity_aliases = aliases_by_entity.setdefault(entity_id, {})
            existing = entity_aliases.get(alias_key)
            if existing is None:
                entity_aliases[alias_key] = (sequence, alias, tag)
                sequence += 1
            elif ALIAS_TAG_PRIORITY[tag] < ALIAS_TAG_PRIORITY[existing[2]]:
                entity_aliases[alias_key] = (existing[0], existing[1], tag)

    alias_summaries = []
    for entity_id, aliases in aliases_by_entity.items():
        ordered = sorted(
            aliases.values(),
            key=lambda item: (ALIAS_TAG_PRIORITY[item[2]], item[0]),
        )
        alias_summaries.append(
            {
                "entity_id": entity_id,
                "aliases": " | ".join(
                    f"{alias} [{tag}]" for _sequence, alias, tag in ordered
                ),
                "alias_count": len(ordered),
            }
        )
    summary_frame = pd.DataFrame(
        alias_summaries,
        columns=["entity_id", "aliases", "alias_count"],
    )

    combined = entity_frame.merge(
        summary_frame,
        how="left",
        on="entity_id",
    )
    combined["aliases"] = combined["aliases"].fillna("")
    combined["alias_count"] = combined["alias_count"].fillna(0).astype(int)
    return combined[NAME_ALIAS_COLUMNS]


def _format_name_alias_sheet(sheet: Worksheet) -> None:
    """Apply compact, readable formatting to the convenience sheet."""
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:F{max(1, sheet.max_row)}"
    sheet.sheet_view.showGridLines = False

    header_fill = PatternFill("solid", fgColor="0F4C81")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font

    for cell in sheet["A"][1:]:
        cell.alignment = Alignment(horizontal="left")

    for column, width in {
        "A": 16,
        "B": 12,
        "C": 16,
        "D": 42,
        "E": 72,
        "F": 13,
    }.items():
        sheet.column_dimensions[column].width = width

    for cell in sheet["F"][1:]:
        cell.number_format = "#,##0"

    if sheet.max_row > 1:
        table = Table(
            displayName="NameAliasTable",
            ref=f"A1:F{sheet.max_row}",
        )
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        sheet.add_table(table)


def export_data(
    data: NormalizedData,
    output_dir: Path,
    export_csv: bool = True,
    excel: bool = True,
    parquet: bool = True,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    tables = data.tables()
    schemas = data.schemas()
    frames: dict[str, pd.DataFrame] = {}
    csv_dir = output_dir / "csv"
    excel_dir = output_dir / "excel"
    parquet_dir = output_dir / "parquet"
    if export_csv:
        csv_dir.mkdir(parents=True, exist_ok=True)
    if excel:
        excel_dir.mkdir(parents=True, exist_ok=True)
    if parquet:
        parquet_dir.mkdir(parents=True, exist_ok=True)

    for name, rows in tables.items():
        frame = pd.DataFrame(rows, columns=schemas[name])
        frames[name] = frame
        if export_csv:
            frame.to_csv(
                csv_dir / f"{name}.csv",
                index=False,
                quoting=csv.QUOTE_MINIMAL,
                encoding="utf-8",
            )
    if excel:
        with pd.ExcelWriter(excel_dir / "sanctions.xlsx", engine="openpyxl") as writer:
            for name, frame in frames.items():
                frame.to_excel(writer, sheet_name=name[:31], index=False)
            name_alias_frame = _name_alias_frame(frames)
            name_alias_frame.to_excel(
                writer,
                sheet_name=NAME_ALIAS_SHEET,
                index=False,
            )
            _format_name_alias_sheet(writer.book[NAME_ALIAS_SHEET])
    if parquet:
        for name, frame in frames.items():
            frame.to_parquet(parquet_dir / f"{name}.parquet", index=False)
