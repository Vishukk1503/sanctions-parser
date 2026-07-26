from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sanctions_parser.tls import enable_native_trust_store

# Configure HTTPS before importing modules that initialize Requests/urllib3.
# This lets managed Windows installations trust enterprise CAs installed by IT.
enable_native_trust_store()

from sanctions_parser.config import load_sources
from sanctions_parser.interactive import InteractiveCLI
from sanctions_parser.pipeline import SourceOutcome, process_source

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("sanctions_parser.main")


def configure_logging(verbose: bool = False, interactive: bool = False) -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_dir / "run.log", encoding="utf-8")
    ]
    if not interactive:
        handlers.insert(0, logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and normalize sanctions lists"
    )
    parser.add_argument(
        "--source", action="append", help="Source key; repeat to select several"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch the interactive terminal interface",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Process all enabled sources without opening the menu",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    interactive = args.interactive or (len(sys.argv) == 1 and not args.non_interactive)
    configure_logging(args.verbose, interactive=interactive)
    sources = load_sources(args.config)
    if interactive:
        return InteractiveCLI(ROOT, sources).run()
    selected = set(args.source or [])
    unknown = selected.difference(sources)
    if unknown:
        print(f"Unknown source(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    outcomes: list[SourceOutcome] = []
    failed = False
    for name, source in sources.items():
        if not source.enabled or (selected and name not in selected):
            continue
        try:
            outcomes.append(process_source(source, ROOT))
        except Exception as exc:  # Isolation between providers is intentional.
            LOGGER.exception("Source %s failed", name)
            outcomes.append(SourceOutcome(name, "failed", str(exc)))
            failed = True
    for outcome in outcomes:
        print(
            f"{outcome.source:8} {outcome.status:10} "
            f"entities={outcome.entities:<7} {outcome.detail}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
