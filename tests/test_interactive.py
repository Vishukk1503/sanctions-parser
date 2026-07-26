from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from sanctions_parser.config import SourceConfig
from sanctions_parser.health import LocalHealth, ProviderHealth, UplinkHealth
from sanctions_parser.interactive import InteractiveCLI
from sanctions_parser.pipeline import SourceOutcome
from sanctions_parser.reporting import ParsingStats


def test_completed_processing_returns_to_menu_signal(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "sanctions_parser.interactive.questionary.confirm",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: True),
    )
    monkeypatch.setattr(
        "sanctions_parser.interactive.process_source",
        lambda *args, **kwargs: SourceOutcome(
            "ofac", "unchanged", "Checksum matched previous file"
        ),
    )
    console = Console(file=StringIO(), force_terminal=False)
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")
    cli = InteractiveCLI(tmp_path, {"ofac": source}, console=console)

    assert cli.process(["ofac"], {"csv"}) is True


def test_all_source_and_format_choices(monkeypatch, tmp_path: Path) -> None:
    answers = iter([["__all__"], ["__all__"]])
    monkeypatch.setattr(
        "sanctions_parser.interactive.questionary.checkbox",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: next(answers)),
    )
    sources = {
        name: SourceConfig(name, f"https://example.test/{name}.xml", name)
        for name in ("ofac", "un", "eu", "uk")
    }
    cli = InteractiveCLI(
        tmp_path,
        sources,
        console=Console(file=StringIO(), force_terminal=False),
    )

    assert cli.choose_sources() == ["ofac", "un", "eu", "uk"]
    assert cli.choose_formats() == {"csv", "excel", "parquet"}


def test_back_choices_return_to_main_menu(monkeypatch, tmp_path: Path) -> None:
    answers = iter([["__back__"], ["__back__"]])
    monkeypatch.setattr(
        "sanctions_parser.interactive.questionary.checkbox",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: next(answers)),
    )
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")
    cli = InteractiveCLI(
        tmp_path,
        {"ofac": source},
        console=Console(file=StringIO(), force_terminal=False),
    )

    assert cli.choose_sources() is None
    assert cli.choose_formats() is None


def test_health_dashboard_renders_uplink_and_local_status(
    monkeypatch, tmp_path: Path
) -> None:
    result = ProviderHealth(
        "ofac",
        UplinkHealth(
            "healthy",
            200,
            0.4,
            1,
            "sdnList",
            "https://example.test/final.xml",
            "Endpoint and XML payload verified",
            datetime(2026, 7, 25, tzinfo=UTC),
        ),
        LocalHealth(
            "healthy",
            "Valid · 1.0 MB",
            "healthy",
            "19,254 entities",
            "healthy",
            "13 tables · CSV/XLSX/PQ",
            "2026-07-25 02:00",
            (),
            datetime(2026, 7, 25, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(
        "sanctions_parser.interactive.check_provider_health",
        lambda *args, **kwargs: result,
    )
    console_output = StringIO()
    cli = InteractiveCLI(
        tmp_path,
        {
            "ofac": SourceConfig(
                "ofac",
                "https://example.test/ofac.xml",
                "ofac",
                expected_root="sdnList",
            )
        },
        console=Console(file=console_output, force_terminal=False, width=140),
    )

    cli.show_health_dashboard()

    rendered = console_output.getvalue()
    assert "SANCTIONS SYSTEM HEALTH" in rendered
    assert "UPLINK MAP" in rendered
    assert "Source freshness" in rendered
    assert "NO NEWER DATE" in rendered
    assert "19,254" in rendered
    assert "All provider uplinks and local datasets are healthy" in rendered


def test_main_menu_no_longer_contains_source_status(
    monkeypatch, tmp_path: Path
) -> None:
    captured_choices: list[str] = []

    def fake_select(*args, **kwargs):
        captured_choices.extend(choice.title for choice in kwargs["choices"])
        return SimpleNamespace(ask=lambda: "exit")

    monkeypatch.setattr(
        "sanctions_parser.interactive.questionary.select",
        fake_select,
    )
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")
    cli = InteractiveCLI(
        tmp_path,
        {"ofac": source},
        console=Console(file=StringIO(), force_terminal=False),
    )

    assert cli.run() == 0
    assert "View source status" not in captured_choices
    assert "Download and validate only" not in captured_choices
    assert "View parsing reports" in captured_choices


def test_parsing_report_renders_compact_statistics(tmp_path: Path) -> None:
    report = ParsingStats(
        entities=100,
        individuals=60,
        organizations=30,
        vessels=8,
        aircraft=2,
        other_entities=0,
        aliases=250,
        strong_aliases=200,
        weak_aliases=30,
        former_aliases=20,
        entities_with_aliases=75,
        addresses=120,
        documents=80,
        nationalities=55,
        programs=40,
        dates_of_birth=50,
        places_of_birth=35,
        relationships=10,
        designations=5,
        regulations=4,
        contacts=3,
        sanctions=2,
        blank_primary_names=0,
        orphaned_aliases=0,
    )
    output = StringIO()
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")
    cli = InteractiveCLI(
        tmp_path,
        {"ofac": source},
        console=Console(file=output, force_terminal=False, width=160),
    )

    cli.show_parsing_report([SourceOutcome("ofac", "processed", "output", 100, report)])

    rendered = output.getvalue()
    assert "Parsing summary" in rendered
    assert "Data coverage" in rendered
    assert "200 / 30 / 20" in rendered
    assert "250 aliases" in rendered
    assert "0 name issues" in rendered


def test_processing_result_only_renders_report_totals(tmp_path: Path) -> None:
    report = ParsingStats(
        entities=100,
        individuals=60,
        organizations=30,
        vessels=8,
        aircraft=2,
        other_entities=0,
        aliases=250,
        strong_aliases=200,
        weak_aliases=30,
        former_aliases=20,
        entities_with_aliases=75,
        addresses=120,
        documents=80,
        nationalities=55,
        programs=40,
        dates_of_birth=50,
        places_of_birth=35,
        relationships=10,
        designations=5,
        regulations=4,
        contacts=3,
        sanctions=2,
        blank_primary_names=0,
        orphaned_aliases=0,
    )
    output = StringIO()
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")
    cli = InteractiveCLI(
        tmp_path,
        {"ofac": source},
        console=Console(file=output, force_terminal=False),
    )

    cli.show_processing_report_totals(
        [SourceOutcome("ofac", "processed", "output", 100, report)]
    )

    rendered = output.getvalue()
    assert "100 entities" in rendered
    assert "250 aliases" in rendered
    assert "Full statistics: View parsing reports" in rendered
    assert "Data coverage" not in rendered


def test_parsing_reports_back_returns_to_main_menu(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "sanctions_parser.interactive.questionary.select",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: "__back__"),
    )
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")
    cli = InteractiveCLI(
        tmp_path,
        {"ofac": source},
        console=Console(file=StringIO(), force_terminal=False),
    )

    assert cli.parsing_reports() is False


def test_troubleshoot_health_choice_runs_dashboard(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "sanctions_parser.interactive.questionary.select",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: "health"),
    )
    called: list[bool] = []
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")
    cli = InteractiveCLI(
        tmp_path,
        {"ofac": source},
        console=Console(file=StringIO(), force_terminal=False),
    )
    monkeypatch.setattr(cli, "show_health_dashboard", lambda: called.append(True))

    assert cli.troubleshoot() is True
    assert called == [True]
