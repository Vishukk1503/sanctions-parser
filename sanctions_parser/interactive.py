from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import questionary
from questionary import Choice
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .config import SourceConfig
from .delta import (
    StoredDeltaReport,
    load_baseline_ref,
    load_delta_preview,
    load_latest_delta_report,
)
from .health import (
    LocalHealth,
    ProviderHealth,
    UplinkHealth,
    check_provider_health,
    compare_source_dates,
    read_source_date,
)
from .pipeline import (
    PARSER_SCHEMA_VERSION,
    DeltaOutcome,
    RawBaselineCandidate,
    SourceOutcome,
    find_existing_raw_baseline,
    process_source,
    run_delta_source,
)
from .reporting import StoredParsingReport, load_latest_parsing_report

SOURCE_LABELS = {
    "ofac": "OFAC — United States",
    "un": "United Nations",
    "eu": "European Union",
    "uk": "United Kingdom",
}
STATUS_STYLES = {
    "processed": "bold green",
    "downloaded": "bold cyan",
    "unchanged": "yellow",
    "failed": "bold red",
}
DELTA_STATUS_STYLES = {
    "baseline_created": "bold cyan",
    "changes": "bold yellow",
    "no_changes": "bold green",
    "failed": "bold red",
}


def _label(name: str) -> str:
    return SOURCE_LABELS.get(name, name.upper())


class InteractiveCLI:
    """Keyboard-driven terminal interface around the ingestion pipeline."""

    def __init__(
        self,
        project_root: Path,
        sources: dict[str, SourceConfig],
        console: Console | None = None,
    ) -> None:
        self.project_root = project_root
        self.sources = sources
        self.enabled = {
            name: source for name, source in sources.items() if source.enabled
        }
        self.console = console or Console()

    def run(self) -> int:
        self._header()
        returned_from_run = False
        while True:
            action = questionary.select(
                "What would you like to do?",
                choices=[
                    Choice("Run all enabled sources", "run_all"),
                    Choice("Select sources to process", "select"),
                    Choice("Run delta check", "delta"),
                    Choice("View reports", "reports"),
                    Choice("Troubleshoot", "troubleshoot"),
                    Choice("Open output folder", "open_output"),
                    Choice("Exit", "exit"),
                ],
                default="exit" if returned_from_run else "run_all",
                use_shortcuts=True,
                qmark="›",
            ).ask()
            returned_from_run = False
            if action in (None, "exit"):
                self.console.print("\n[dim]Goodbye.[/dim]")
                return 0
            if action == "troubleshoot":
                if self.troubleshoot():
                    returned_from_run = True
                else:
                    self._redraw()
            elif action == "reports":
                self.reports()
                self._redraw()
            elif action == "delta":
                if self.delta_run():
                    self._redraw()
                    returned_from_run = True
                else:
                    self._redraw()
            elif action == "open_output":
                self.open_output()
            else:
                selected = (
                    list(self.enabled) if action == "run_all" else self.choose_sources()
                )
                if selected is None:
                    self._redraw()
                    continue
                formats = self.choose_formats()
                if formats is None:
                    self._redraw()
                    continue
                if self.process(selected, formats):
                    self._redraw()
                    returned_from_run = True

    def reports(self) -> bool:
        action = questionary.select(
            "View reports:",
            choices=[
                Choice("Parsing reports", "parsing"),
                Choice("Weekly name + alias changes", "delta"),
                Choice("← Back to main menu", "__back__"),
            ],
            default="parsing",
            use_shortcuts=True,
            qmark="›",
        ).ask()
        if action in (None, "__back__"):
            return False
        if action == "parsing":
            self.parsing_reports()
        else:
            self.delta_reports()
        return True

    def delta_run(self) -> bool:
        action = questionary.select(
            "Run delta check:",
            choices=[
                Choice("All enabled sources", "__all__"),
                Choice("Select sources", "__select__"),
                Choice("← Back to main menu", "__back__"),
            ],
            default="__all__",
            use_shortcuts=True,
            qmark="›",
        ).ask()
        if action in (None, "__back__"):
            return False
        selected = list(self.enabled) if action == "__all__" else self.choose_sources()
        if not selected:
            return False
        initial_baselines: dict[str, RawBaselineCandidate] = {}
        missing_raw: list[str] = []
        for name in selected:
            try:
                if load_baseline_ref(self.project_root, name) is not None:
                    continue
                candidate = find_existing_raw_baseline(
                    self.enabled[name],
                    self.project_root,
                )
            except ValueError as exc:
                self.console.print(f"[bold red]{exc}[/bold red]")
                continue
            if candidate is None:
                missing_raw.append(name)
            else:
                initial_baselines[name] = candidate
        details = (
            "[bold]Mode:[/bold] compare latest lists with delta checkpoints\n"
            f"[bold]Sources:[/bold] "
            f"{', '.join(_label(name) for name in selected)}\n"
            "[dim]Reports: CSV, Excel and Parquet[/dim]"
        )
        if initial_baselines:
            baseline_lines = "\n".join(
                f"  {_label(name)}: {candidate.archive_date} · "
                f"{candidate.path.name}"
                for name, candidate in initial_baselines.items()
            )
            details += (
                "\n\n[bold cyan]Existing raw extracts will become the initial "
                f"baseline:[/bold cyan]\n{baseline_lines}"
            )
        if missing_raw:
            details += (
                "\n\n[yellow]No previous raw extract found for: "
                f"{', '.join(_label(name) for name in missing_raw)}. "
                "The latest download will create the first baseline.[/yellow]"
            )
        self.console.print(Panel(details, title="Delta run plan", border_style="blue"))
        prompt = (
            "Use the existing raw extracts as baseline and start delta check?"
            if initial_baselines
            else "Start delta check?"
        )
        confirmed = questionary.confirm(
            prompt, default=True, qmark="›"
        ).ask()
        if not confirmed:
            self.console.print("[dim]Delta run cancelled.[/dim]\n")
            return False
        self.process_delta(selected, initial_baselines)
        return True

    def process_delta(
        self,
        selected: list[str],
        initial_baselines: dict[str, RawBaselineCandidate] | None = None,
    ) -> list[DeltaOutcome]:
        outcomes: list[DeltaOutcome] = []
        durations: dict[str, float] = {}
        initial_baselines = initial_baselines or {}
        for name in selected:
            started = time.monotonic()
            with Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=self.console,
                transient=True,
            ) as progress:
                progress.add_task(f"Checking {_label(name)} delta…", total=None)
                try:
                    outcome = run_delta_source(
                        self.enabled[name],
                        self.project_root,
                        show_download_progress=False,
                        initial_baseline=initial_baselines.get(name),
                    )
                except Exception as exc:  # noqa: BLE001
                    outcome = DeltaOutcome(name, "failed", str(exc))
            durations[name] = time.monotonic() - started
            outcomes.append(outcome)
            style = DELTA_STATUS_STYLES.get(outcome.status, "white")
            label = outcome.status.replace("_", " ")
            self.console.print(f"[{style}]● {name.upper()} {label}[/{style}]")
        self.show_delta_summary(outcomes, durations)
        return outcomes

    def show_delta_summary(
        self,
        outcomes: list[DeltaOutcome],
        durations: dict[str, float] | None = None,
    ) -> None:
        table = Table(
            title="Name + alias delta check",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Source", no_wrap=True)
        table.add_column("Result", no_wrap=True)
        table.add_column("New", justify="right")
        table.add_column("Updated", justify="right")
        table.add_column("Removed", justify="right")
        table.add_column("Unchanged", justify="right")
        if durations is not None:
            table.add_column("Duration", justify="right")
        for outcome in outcomes:
            summary = outcome.summary
            result_label = outcome.status.replace("_", " ").title()
            values = [
                _label(outcome.source),
                Text(
                    result_label,
                    style=DELTA_STATUS_STYLES.get(outcome.status, "white"),
                ),
                f"{summary.new:,}" if summary else "—",
                f"{summary.updated:,}" if summary else "—",
                f"{summary.removed:,}" if summary else "—",
                f"{summary.unchanged:,}" if summary else "—",
            ]
            if durations is not None:
                values.append(f"{durations.get(outcome.source, 0):.1f}s")
            table.add_row(*values)
        self.console.print()
        self.console.print(table)
        for outcome in outcomes:
            for warning in outcome.warnings:
                self.console.print(
                    f"[bold yellow]Warning — {_label(outcome.source)}:[/bold yellow] "
                    f"{warning}"
                )
        failures = sum(outcome.status == "failed" for outcome in outcomes)
        if failures:
            self.console.print(
                f"[red]{failures} source(s) failed.[/red] "
                "[dim]Their previous delta checkpoints were preserved.[/dim]\n"
            )
        else:
            self.console.print(
                "[green]Delta check complete.[/green] "
                "[dim]Full reports are available under output/delta.[/dim]\n"
            )

    def _header(self) -> None:
        title = Text("SANCTIONS DATA MANAGER", style="bold white")
        subtitle = Text(
            "Automated acquisition • validation • normalization", style="cyan"
        )
        self.console.print(
            Panel.fit(
                Text.assemble(title, "\n", subtitle),
                border_style="bright_blue",
                padding=(1, 4),
            )
        )
        self.console.print(
            f"[dim]{len(self.enabled)} enabled sources • "
            f"{self.project_root}[/dim]\n"
        )

    def _redraw(self) -> None:
        self.console.clear()
        self._header()

    def choose_sources(self) -> list[str] | None:
        selected = questionary.checkbox(
            "Select sanctions sources:",
            choices=[
                Choice("← Back to main menu", "__back__"),
                Choice("All enabled sources", "__all__"),
                *[Choice(_label(name), name) for name in self.enabled],
            ],
            validate=lambda answer: bool(answer) or "Select at least one source",
            instruction="(Arrow keys move, Space selects, Enter confirms)",
            qmark="›",
        ).ask()
        if selected is None:
            return None
        if "__back__" in selected:
            return None
        return list(self.enabled) if "__all__" in selected else selected

    def choose_formats(self) -> set[str] | None:
        selected = questionary.checkbox(
            "Select export formats:",
            choices=[
                Choice("← Back to main menu", "__back__"),
                Choice("All formats", "__all__"),
                Choice("CSV", "csv"),
                Choice("Excel workbook", "excel"),
                Choice("Parquet", "parquet"),
            ],
            validate=lambda answer: bool(answer) or "Select at least one export format",
            instruction="(Arrow keys move, Space selects, Enter confirms)",
            qmark="›",
        ).ask()
        if selected is None:
            return None
        if "__back__" in selected:
            return None
        if "__all__" in selected:
            return {"csv", "excel", "parquet"}
        return set(selected)

    def process(self, selected: list[str], formats: set[str]) -> bool:
        details = (
            "[bold]Mode:[/bold] download, parse and export\n"
            f"[bold]Sources:[/bold] {', '.join(_label(name) for name in selected)}"
        )
        details += f"\n[bold]Formats:[/bold] {', '.join(sorted(formats))}"
        self.console.print(Panel(details, title="Run plan", border_style="blue"))
        confirmed = questionary.confirm(
            "Start processing?", default=True, qmark="›"
        ).ask()
        if not confirmed:
            self.console.print("[dim]Run cancelled.[/dim]\n")
            return False

        outcomes: list[SourceOutcome] = []
        durations: dict[str, float] = {}
        for name in selected:
            started = time.monotonic()
            with Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=self.console,
                transient=True,
            ) as progress:
                progress.add_task(f"Processing {_label(name)}…", total=None)
                try:
                    outcome = process_source(
                        self.enabled[name],
                        self.project_root,
                        export_formats=formats,
                        show_download_progress=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Provider isolation is a core pipeline requirement.
                    outcome = SourceOutcome(name, "failed", str(exc))
            durations[name] = time.monotonic() - started
            outcomes.append(outcome)
            style = STATUS_STYLES.get(outcome.status, "white")
            self.console.print(f"[{style}]● {name.upper()} {outcome.status}[/{style}]")
        self.show_summary(outcomes, durations)
        self.show_processing_report_totals(outcomes)
        return True

    def show_summary(
        self, outcomes: list[SourceOutcome], durations: dict[str, float]
    ) -> None:
        table = Table(
            title="Run complete",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Source")
        table.add_column("Result")
        table.add_column("Entities", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Details", overflow="fold")
        for outcome in outcomes:
            table.add_row(
                _label(outcome.source),
                Text(
                    outcome.status.title(),
                    style=STATUS_STYLES.get(outcome.status, "white"),
                ),
                f"{outcome.entities:,}" if outcome.entities else "—",
                f"{durations[outcome.source]:.1f}s",
                outcome.detail,
            )
        self.console.print()
        self.console.print(table)
        failures = sum(item.status == "failed" for item in outcomes)
        if failures:
            self.console.print(
                f"[red]{failures} source(s) failed.[/red] "
                "[dim]See logs/run.log for details.[/dim]\n"
            )
        else:
            self.console.print("[green]All selected sources completed.[/green]\n")

    def show_processing_report_totals(self, outcomes: list[SourceOutcome]) -> None:
        reported = [outcome for outcome in outcomes if outcome.report is not None]
        if not reported:
            return
        total_entities = sum(
            outcome.report.entities
            for outcome in reported
            if outcome.report is not None
        )
        total_aliases = sum(
            outcome.report.aliases for outcome in reported if outcome.report is not None
        )
        self.console.print(
            f"[bold green]● {len(reported)} source(s) reported[/bold green] "
            f"[dim]• {total_entities:,} entities • {total_aliases:,} aliases[/dim]\n"
            "[dim]Full statistics: View parsing reports[/dim]\n"
        )

    def show_parsing_report(self, outcomes: list[SourceOutcome]) -> None:
        reported = [outcome for outcome in outcomes if outcome.report is not None]
        if not reported:
            return

        summary = Table(
            title="Parsing summary",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        summary.add_column("Source", no_wrap=True)
        summary.add_column("Entities", justify="right")
        summary.add_column("Individual", justify="right")
        summary.add_column("Org", justify="right")
        summary.add_column("Vessel", justify="right")
        summary.add_column("Aircraft", justify="right")
        summary.add_column("Aliases", justify="right")
        summary.add_column("Strong / Weak / Former", justify="right")
        for outcome in reported:
            report = outcome.report
            assert report is not None
            summary.add_row(
                outcome.source.upper(),
                f"{report.entities:,}",
                f"{report.individuals:,}",
                f"{report.organizations:,}",
                f"{report.vessels:,}",
                f"{report.aircraft:,}",
                f"{report.aliases:,}",
                (
                    f"{report.strong_aliases:,} / "
                    f"{report.weak_aliases:,} / "
                    f"{report.former_aliases:,}"
                ),
            )
        self.console.print(summary)

        coverage = Table(
            title="Data coverage",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        coverage.add_column("Source", no_wrap=True)
        coverage.add_column("With aliases", justify="right")
        coverage.add_column("Addresses", justify="right")
        coverage.add_column("Documents", justify="right")
        coverage.add_column("Nationalities", justify="right")
        coverage.add_column("Programs", justify="right")
        coverage.add_column("Birth dates", justify="right")
        coverage.add_column("Name issues", justify="right")
        for outcome in reported:
            report = outcome.report
            assert report is not None
            issues = (
                f"[bold red]{report.name_issues:,}[/bold red]"
                if report.name_issues
                else "[green]0[/green]"
            )
            coverage.add_row(
                outcome.source.upper(),
                f"{report.entities_with_aliases:,}",
                f"{report.addresses:,}",
                f"{report.documents:,}",
                f"{report.nationalities:,}",
                f"{report.programs:,}",
                f"{report.dates_of_birth:,}",
                issues,
            )
        self.console.print(coverage)

        total_entities = sum(
            outcome.report.entities
            for outcome in reported
            if outcome.report is not None
        )
        total_aliases = sum(
            outcome.report.aliases for outcome in reported if outcome.report is not None
        )
        total_issues = sum(
            outcome.report.name_issues
            for outcome in reported
            if outcome.report is not None
        )
        issue_style = "green" if total_issues == 0 else "yellow"
        self.console.print(
            f"[bold green]● {len(reported)} source(s) reported[/bold green] "
            f"[dim]• {total_entities:,} entities • {total_aliases:,} aliases •[/dim] "
            f"[{issue_style}]{total_issues:,} name issues[/{issue_style}]\n"
        )

    def _load_reports(self, source_names: list[str]) -> list[StoredParsingReport]:
        return [
            report
            for name in source_names
            if (
                report := load_latest_parsing_report(
                    self.project_root,
                    name,
                    PARSER_SCHEMA_VERSION,
                )
            )
            is not None
        ]

    @staticmethod
    def _report_outcome(report: StoredParsingReport) -> SourceOutcome:
        return SourceOutcome(
            report.source,
            "current" if report.current else "outdated",
            str(report.output_dir),
            report.stats.entities,
            report.stats,
        )

    def parsing_reports(self) -> bool:
        action = questionary.select(
            "Parsing reports:",
            choices=[
                Choice("All sources overview", "__all__"),
                *[
                    Choice(f"{_label(name)} — latest report", name)
                    for name in self.enabled
                ],
                Choice("← Back to main menu", "__back__"),
            ],
            default="__all__",
            use_shortcuts=True,
            qmark="›",
        ).ask()
        if action in (None, "__back__"):
            return False

        if action == "__all__":
            reports = self._load_reports(list(self.enabled))
            if not reports:
                self.console.print(
                    Panel(
                        "No current parsing reports are available.\n"
                        "Process one or more sources first.",
                        title="Parsing reports",
                        border_style="yellow",
                    )
                )
                return True
            self.show_parsing_report(
                [self._report_outcome(report) for report in reports]
            )
            missing = [
                _label(name)
                for name in self.enabled
                if name not in {report.source for report in reports}
            ]
            if missing:
                self.console.print(
                    f"[yellow]No current report:[/yellow] {', '.join(missing)}\n"
                )
            return True

        report = load_latest_parsing_report(
            self.project_root,
            action,
            PARSER_SCHEMA_VERSION,
        )
        if report is None:
            self.console.print(
                Panel(
                    f"No current {_label(action)} parsing report is available.\n"
                    "Process this source first.",
                    title="Parsing report",
                    border_style="yellow",
                )
            )
            return True
        self.show_source_report(report)
        return True

    def show_source_report(self, report: StoredParsingReport) -> None:
        source_date = None
        if report.raw_path and report.raw_path.is_file():
            try:
                source_date = read_source_date(report.raw_path, report.source)
            except OSError:
                source_date = None
        try:
            parsed_at = (
                datetime.strptime(report.output_dir.name, "%Y%m%dT%H%M%SZ")
                .replace(tzinfo=UTC)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M")
            )
        except ValueError:
            parsed_at = report.output_dir.name
        status = (
            "[bold green]CURRENT[/bold green]"
            if report.current
            else "[bold yellow]OUTDATED[/bold yellow]"
        )
        self.console.print(
            Panel(
                f"[bold]Source:[/bold] {_label(report.source)}\n"
                f"[bold]Report status:[/bold] {status}\n"
                f"[bold]Source XML date:[/bold] {self._source_date(source_date)}\n"
                f"[bold]Parsed:[/bold] {parsed_at}\n"
                f"[bold]Parser schema:[/bold] {report.parser_schema_version}\n"
                f"[bold]Output:[/bold] {report.output_dir}",
                title="Latest parsing report",
                border_style="bright_blue",
            )
        )
        self.show_parsing_report([self._report_outcome(report)])

        stats = report.stats
        details = Table(
            title="Additional relations",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        details.add_column("Other entities", justify="right")
        details.add_column("Relationships", justify="right")
        details.add_column("Birth places", justify="right")
        details.add_column("Designations", justify="right")
        details.add_column("Regulations", justify="right")
        details.add_column("Contacts", justify="right")
        details.add_column("Sanctions", justify="right")
        details.add_row(
            f"{stats.other_entities:,}",
            f"{stats.relationships:,}",
            f"{stats.places_of_birth:,}",
            f"{stats.designations:,}",
            f"{stats.regulations:,}",
            f"{stats.contacts:,}",
            f"{stats.sanctions:,}",
        )
        self.console.print(details)

    def _load_delta_reports(self, source_names: list[str]) -> list[StoredDeltaReport]:
        return [
            report
            for name in source_names
            if (report := load_latest_delta_report(self.project_root, name)) is not None
        ]

    @staticmethod
    def _delta_outcome(report: StoredDeltaReport) -> DeltaOutcome:
        return DeltaOutcome(
            report.source,
            report.summary.result,
            str(report.output_dir),
            report.summary,
        )

    def delta_reports(self) -> bool:
        action = questionary.select(
            "Weekly name + alias changes:",
            choices=[
                Choice("Latest overview — all sources", "__all__"),
                *[
                    Choice(f"{_label(name)} — latest changes", name)
                    for name in self.enabled
                ],
                Choice("← Back to main menu", "__back__"),
            ],
            default="__all__",
            use_shortcuts=True,
            qmark="›",
        ).ask()
        if action in (None, "__back__"):
            return False
        if action == "__all__":
            reports = self._load_delta_reports(list(self.enabled))
            if not reports:
                self.console.print(
                    Panel(
                        "No delta reports are available.\n"
                        "Run a delta check to create the initial checkpoints.",
                        title="Weekly name + alias changes",
                        border_style="yellow",
                    )
                )
                return True
            self.show_delta_summary([self._delta_outcome(report) for report in reports])
            missing = [
                _label(name)
                for name in self.enabled
                if name not in {report.source for report in reports}
            ]
            if missing:
                self.console.print(
                    f"[yellow]No delta report:[/yellow] {', '.join(missing)}\n"
                )
            return True

        report = load_latest_delta_report(self.project_root, action)
        if report is None:
            self.console.print(
                Panel(
                    f"No {_label(action)} delta report is available.\n"
                    "Run a delta check for this source first.",
                    title="Weekly name + alias changes",
                    border_style="yellow",
                )
            )
            return True
        self.show_source_delta_report(report)
        return True

    @staticmethod
    def _short_checkpoint(value: str) -> str:
        if not value:
            return "First checkpoint"
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value

    def show_source_delta_report(self, report: StoredDeltaReport) -> None:
        summary = report.summary
        status = Text(
            summary.result.replace("_", " ").title(),
            style=DELTA_STATUS_STYLES.get(summary.result, "white"),
        )
        details = Text.assemble(
            ("Source: ", "bold"),
            _label(report.source),
            "\n",
            ("Result: ", "bold"),
            status,
            "\n",
            ("Previous checkpoint: ", "bold"),
            self._short_checkpoint(summary.previous_checkpoint),
            "\n",
            ("Current check: ", "bold"),
            self._short_checkpoint(summary.current_check),
            "\n",
            ("Changes: ", "bold"),
            (
                f"{summary.new:,} new • {summary.updated:,} updated • "
                f"{summary.removed:,} removed"
            ),
            "\n",
            ("Report: ", "bold"),
            str(report.output_dir),
        )
        self.console.print(
            Panel(
                details,
                title="Latest name + alias delta report",
                border_style="bright_blue",
            )
        )
        for warning in report.warnings:
            self.console.print(f"[bold yellow]Warning:[/bold yellow] {warning}")

        preview_rows = load_delta_preview(report, limit=10)
        if not preview_rows:
            self.console.print("[green]No changed records in this report.[/green]\n")
            return
        preview = Table(
            title="Changed records preview",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        preview.add_column("Status", no_wrap=True)
        preview.add_column("Entity ID", no_wrap=True)
        preview.add_column("Primary name", overflow="fold")
        preview.add_column("Aliases", overflow="fold")
        preview.add_column("What changed", overflow="fold")
        for row in preview_rows:
            status_value = row.get("status", "")
            style = {
                "NEW": "bold green",
                "UPDATED": "bold yellow",
                "REMOVED": "bold red",
            }.get(status_value, "white")
            preview.add_row(
                Text(status_value, style=style),
                row.get("entity_id", ""),
                row.get("primary_name", ""),
                row.get("aliases", ""),
                row.get("what_changed", ""),
            )
        self.console.print(preview)
        if summary.changed > len(preview_rows):
            self.console.print(
                f"[dim]Showing 10 of {summary.changed:,} changed records. "
                "Open the Excel or CSV report for the complete list.[/dim]\n"
            )

    def troubleshoot(self) -> bool:
        action = questionary.select(
            "Troubleshoot:",
            choices=[
                Choice("Health & Uplink Dashboard", "health"),
                Choice("← Back to main menu", "back"),
            ],
            default="health",
            use_shortcuts=True,
            qmark="›",
        ).ask()
        if action != "health":
            return False
        self.show_health_dashboard()
        return True

    @staticmethod
    def _health_icon(state: str) -> str:
        return {
            "healthy": "[bold green]●[/bold green]",
            "warning": "[bold yellow]●[/bold yellow]",
            "degraded": "[bold yellow]●[/bold yellow]",
            "failed": "[bold red]●[/bold red]",
        }.get(state, "[dim]○[/dim]")

    @staticmethod
    def _signal(result: ProviderHealth) -> tuple[str, str]:
        uplink = result.uplink
        if uplink.state == "failed":
            return "[red]▱▱▱▱[/red]", "Offline"
        style = "yellow" if uplink.state == "degraded" else "green"
        if uplink.latency_seconds < 1:
            bars, quality = "▰▰▰▰", "Excellent"
        elif uplink.latency_seconds < 2.5:
            bars, quality = "▰▰▰▱", "Good"
        elif uplink.latency_seconds < 5:
            bars, quality = "▰▰▱▱", "Slow"
        else:
            bars, quality = "▰▱▱▱", "Very slow"
        if uplink.state == "degraded":
            quality = "Degraded"
        return f"[{style}]{bars}[/{style}]", quality

    @staticmethod
    def _source_date(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d") if value else "—"

    @staticmethod
    def _freshness_label(state: str, label: str) -> str:
        style = {
            "current": "bold green",
            "update": "bold yellow",
            "ahead": "bold yellow",
            "unknown": "dim",
        }.get(state, "white")
        return f"[{style}]{label}[/{style}]"

    def show_health_dashboard(self) -> None:
        started = time.monotonic()
        results: dict[str, ProviderHealth] = {}
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        ) as progress:
            tasks = {
                name: progress.add_task(
                    f"Checking {_label(name)} uplink and local data…", total=None
                )
                for name in self.enabled
            }
            with ThreadPoolExecutor(max_workers=max(len(self.enabled), 1)) as pool:
                futures = {
                    pool.submit(check_provider_health, source, self.project_root): name
                    for name, source in self.enabled.items()
                }
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        result = ProviderHealth(
                            name,
                            UplinkHealth("failed", None, 0, 0, "", "", str(exc)),
                            LocalHealth(
                                "failed",
                                "Check failed",
                                "failed",
                                "Check failed",
                                "failed",
                                "Check failed",
                                "Never",
                                (str(exc),),
                            ),
                        )
                    results[name] = result
                    state = (
                        "healthy"
                        if result.uplink.state == "healthy"
                        and result.local.raw_state == "healthy"
                        and result.local.parser_state == "healthy"
                        and result.local.output_state == "healthy"
                        else "warning"
                    )
                    progress.update(
                        tasks[name],
                        description=(
                            f"{self._health_icon(state)} {_label(name)} checked"
                        ),
                        completed=1,
                        total=1,
                    )

        ordered = [results[name] for name in self.enabled if name in results]
        freshness = {
            result.source: compare_source_dates(
                result.local.source_date,
                result.uplink.source_date,
            )
            for result in ordered
        }
        issue_count = sum(
            result.uplink.state != "healthy"
            or bool(result.local.issues)
            or freshness[result.source].state != "current"
            for result in ordered
        )
        overall = (
            "[bold green]● HEALTHY[/bold green]"
            if issue_count == 0
            else f"[bold yellow]● {issue_count} PROVIDER(S) NEED ATTENTION[/bold yellow]"
        )
        elapsed = time.monotonic() - started
        checked = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self.console.print(
            Panel(
                f"[bold]Overall:[/bold] {overall}\n"
                f"[dim]Live + local checks · {checked} · {elapsed:.1f} seconds[/dim]",
                title="SANCTIONS SYSTEM HEALTH",
                border_style="bright_blue",
            )
        )

        uplink_tree = Tree("[bold cyan]Local CLI[/bold cyan]")
        for result in ordered:
            signal, quality = self._signal(result)
            uplink = result.uplink
            http = str(uplink.http_status) if uplink.http_status else "—"
            root = uplink.xml_root or "no XML"
            state_style = (
                "green"
                if uplink.state == "healthy"
                else ("yellow" if uplink.state == "degraded" else "red")
            )
            redirects = f" · ↪{uplink.redirects}" if uplink.redirects else ""
            uplink_tree.add(
                f"[{state_style}]━━[/{state_style}] {signal} "
                f"[bold]{result.source.upper()}[/bold]  "
                f"[{state_style}]{quality}[/{state_style}] · "
                f"[dim]HTTP {http} · {uplink.latency_seconds:.1f}s · "
                f"{root}{redirects}[/dim]"
            )
        self.console.print(Panel(uplink_tree, title="UPLINK MAP", border_style="cyan"))

        freshness_table = Table(
            title="Source freshness",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        freshness_table.add_column("Source", no_wrap=True)
        freshness_table.add_column("Local XML date", no_wrap=True)
        freshness_table.add_column("Live XML date", no_wrap=True)
        freshness_table.add_column("Status", no_wrap=True)
        for result in ordered:
            comparison = freshness[result.source]
            freshness_table.add_row(
                result.source.upper(),
                self._source_date(result.local.source_date),
                self._source_date(result.uplink.source_date),
                self._freshness_label(comparison.state, comparison.label),
            )
        self.console.print(freshness_table)

        table = Table(
            title="System health",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Source", no_wrap=True)
        table.add_column("Raw XML", no_wrap=True)
        table.add_column("Parser", no_wrap=True)
        table.add_column("Output", no_wrap=True)
        table.add_column("Last run", no_wrap=True)
        for result in ordered:
            raw_detail = result.local.raw_detail.replace("Valid · ", "")
            parser_detail = result.local.parser_detail.replace(" entities", "")
            output_detail = result.local.output_detail.replace(" tables", "")
            last_run = (
                result.local.last_run[5:]
                if len(result.local.last_run) == 16
                else result.local.last_run
            )
            table.add_row(
                result.source.upper(),
                f"{self._health_icon(result.local.raw_state)} {raw_detail}",
                f"{self._health_icon(result.local.parser_state)} {parser_detail}",
                f"{self._health_icon(result.local.output_state)} " f"{output_detail}",
                last_run,
            )
        self.console.print(table)

        issue_lines: list[str] = []
        for result in ordered:
            if result.uplink.state != "healthy":
                cached_ready = (
                    result.local.raw_state == "healthy"
                    and result.local.output_state == "healthy"
                )
                suffix = " Cached data remains ready." if cached_ready else ""
                issue_lines.append(
                    f"[bold]{_label(result.source)} uplink:[/bold] "
                    f"{result.uplink.detail}.{suffix}"
                )
            comparison = freshness[result.source]
            if comparison.state != "current":
                issue_lines.append(
                    f"[bold]{_label(result.source)} freshness:[/bold] "
                    f"{comparison.detail}."
                )
            issue_lines.extend(
                f"[bold]{_label(result.source)}:[/bold] {issue}"
                for issue in result.local.issues
            )
        if issue_lines:
            self.console.print(
                Panel(
                    "\n".join(f"• {line}" for line in issue_lines),
                    title="Issues detected",
                    border_style="yellow",
                )
            )
        else:
            self.console.print(
                "[bold green]● All provider uplinks and local datasets are healthy."
                "[/bold green]\n"
            )
        self.console.print(
            "[dim]▰▰▰▰ Excellent  ▰▰▰▱ Good  ▰▰▱▱ Slow  "
            "▰▱▱▱ Very slow  ▱▱▱▱ Offline[/dim]\n"
        )

    def open_output(self) -> None:
        output = self.project_root / "output"
        output.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(output)  # type: ignore[attr-defined]
            self.console.print(f"[green]Opened:[/green] {output}\n")
        except OSError as exc:
            self.console.print(f"[red]Could not open output folder:[/red] {exc}\n")
