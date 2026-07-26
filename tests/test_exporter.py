from pathlib import Path

import pyarrow.parquet as pq
from openpyxl import load_workbook

from sanctions_parser.exporter import export_data
from sanctions_parser.models import Alias, Entity, NormalizedData


def test_empty_relations_are_exported_with_stable_schemas(tmp_path: Path) -> None:
    data = NormalizedData(entities=[Entity("1", "ofac", "Entity", "Test entity")])

    export_data(data, tmp_path)

    assert (tmp_path / "csv" / "alias.csv").read_text(encoding="utf-8").strip() == (
        "entity_id,alias,quality,language"
    )
    assert pq.ParquetFile(tmp_path / "parquet" / "alias.parquet").metadata.num_rows == 0
    workbook = load_workbook(
        tmp_path / "excel" / "sanctions.xlsx", read_only=True, data_only=True
    )
    try:
        assert workbook["alias"].max_row == 1
        assert workbook["entity"].max_row == 2
        assert workbook["name+alias"].max_row == 2
        assert list(workbook["name+alias"].values) == [
            (
                "entity_id",
                "source",
                "record_type",
                "primary_name",
                "aliases",
                "alias_count",
            ),
            ("1", "ofac", "Entity", "Test entity", None, 0),
        ]
    finally:
        workbook.close()


def test_excel_combines_multiple_aliases_by_entity_id(tmp_path: Path) -> None:
    data = NormalizedData(
        entities=[
            Entity("1", "ofac", "Individual", "Primary Person"),
            Entity("2", "ofac", "Entity", "Primary Company"),
        ],
        aliases=[
            Alias("1", "Weak Alias", "Low"),
            Alias("1", "Old Name", "f.k.a."),
            Alias("1", "Strong Alias", "Good"),
            Alias("1", "Regular Alias", "a.k.a."),
            Alias("1", "Strong Alias", "weak"),
            Alias("1", "PRIMARY PERSON", "strong"),
            Alias("1", "اسم", "Good", "original script"),
            Alias("2", "Company Alias", "strong"),
        ],
    )

    export_data(data, tmp_path, export_csv=False, parquet=False)

    workbook = load_workbook(
        tmp_path / "excel" / "sanctions.xlsx", read_only=False, data_only=True
    )
    try:
        sheet = workbook["name+alias"]
        assert list(sheet.values) == [
            (
                "entity_id",
                "source",
                "record_type",
                "primary_name",
                "aliases",
                "alias_count",
            ),
            (
                "1",
                "ofac",
                "Individual",
                "Primary Person",
                (
                    "Strong Alias [strong] | Regular Alias [strong] | "
                    "Weak Alias [weak] | Old Name [former]"
                ),
                4,
            ),
            (
                "2",
                "ofac",
                "Entity",
                "Primary Company",
                "Company Alias [strong]",
                1,
            ),
        ]
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == "A1:F3"
        assert sheet["A1"].fill.fgColor.rgb == "000F4C81"
        assert sheet["A1"].font.bold is True
        assert sheet["A1"].font.color.rgb == "00FFFFFF"
        assert sheet["A2"].alignment.horizontal == "left"
        assert sheet["F2"].number_format == "#,##0"
        assert sheet.tables["NameAliasTable"].ref == "A1:F3"
    finally:
        workbook.close()

    assert not (tmp_path / "csv").exists()
    assert not (tmp_path / "parquet").exists()
    assert not (tmp_path / "excel" / "name+alias.csv").exists()
    assert not (tmp_path / "excel" / "name+alias.parquet").exists()
