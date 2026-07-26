import json
from pathlib import Path

from sanctions_parser.models import (
    Address,
    Alias,
    Document,
    Entity,
    NormalizedData,
    Program,
)
from sanctions_parser.reporting import (
    ParsingStats,
    build_parsing_stats,
    load_latest_parsing_report,
)


def test_build_parsing_stats_profiles_entities_aliases_and_coverage() -> None:
    data = NormalizedData(
        entities=[
            Entity("1", "demo", "Individual", "Person"),
            Entity("2", "demo", "enterprise", "Company"),
            Entity("3", "demo", "Ship", ""),
            Entity("4", "demo", "Aircraft", "Plane"),
            Entity("5", "demo", "Unknown", "Other"),
        ],
        aliases=[
            Alias("1", "Strong name", "Good"),
            Alias("1", "Weak name", "Low"),
            Alias("2", "Old name", "f.k.a."),
            Alias("missing", "Orphan", "strong"),
        ],
        addresses=[Address("1", "Street")],
        documents=[Document("1", "123")],
        programs=[Program("2", "Program")],
    )

    report = build_parsing_stats(data)

    assert report.entities == 5
    assert report.individuals == 1
    assert report.organizations == 1
    assert report.vessels == 1
    assert report.aircraft == 1
    assert report.other_entities == 1
    assert report.aliases == 4
    assert report.strong_aliases == 2
    assert report.weak_aliases == 1
    assert report.former_aliases == 1
    assert report.entities_with_aliases == 2
    assert report.blank_primary_names == 1
    assert report.orphaned_aliases == 1
    assert report.name_issues == 2
    assert ParsingStats.from_dict(report.to_dict()) == report


def test_latest_parsing_report_loads_current_manifest(tmp_path: Path) -> None:
    stats = build_parsing_stats(
        NormalizedData(entities=[Entity("1", "ofac", "Individual", "Person")])
    )
    output = tmp_path / "output" / "ofac" / "20260726T120000Z"
    output.mkdir(parents=True)
    raw = tmp_path / "raw" / "ofac.xml"
    raw.parent.mkdir()
    raw.write_text("<sdnList/>", encoding="utf-8")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "checksum": "abc",
                "parser_schema_version": 5,
                "raw_path": str(raw),
                "parsing_summary": stats.to_dict(),
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / ".state"
    state.mkdir()
    (state / "ofac.json").write_text(
        json.dumps({"checksum": "abc", "path": str(raw)}),
        encoding="utf-8",
    )

    report = load_latest_parsing_report(tmp_path, "ofac", 5)

    assert report is not None
    assert report.current is True
    assert report.stats == stats
    assert report.output_dir == output
