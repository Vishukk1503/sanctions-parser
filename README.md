# Sanctions Parser

Automated ingestion and relational normalization for the official OFAC, UN, EU,
and UK consolidated sanctions XML feeds.

## Simple Windows setup

Supports 64-bit Python 3.11, 3.12, 3.13, or 3.14.

1. Install a supported Python version from
   [python.org](https://www.python.org/downloads/windows/) if it is not already
   installed. Select **Add python.exe to PATH** during installation.
2. Double-click `SETUP.bat` once. It creates a private `.venv`, installs every
   required package, and verifies the configuration.
3. Double-click `RUN_APP.bat` whenever you want to use the application.

`RUN_APP.bat` automatically starts `SETUP.bat` when the private environment is
missing.

## Manual setup

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
python main.py
```

Linux/macOS activation is `source .venv/bin/activate`.

Running `python main.py` opens the interactive terminal interface. It provides:

- source and export-format selection
- complete download, validation, parsing and export runs
- animated Rich status displays and a run summary
- saved per-source parsing statistics available from a dedicated report menu
- a combined live uplink, source-freshness and local-data health dashboard
- local-versus-provider XML publication-date comparison
- direct access to the output folder

For unattended automation, provide flags or use `--non-interactive`:

```bash
python main.py --source ofac
python main.py --non-interactive
```

The program reads enabled sources from `config/sources.yaml`. Raw provider XML
is retained under `raw/<source>/<YYYY-MM-DD>/`. A SHA256 state record and output
manifest prevent unchanged content from being parsed again once a complete
export exists. If an XML file was previously downloaded without being parsed,
the next full run parses the cached XML instead of incorrectly skipping it.
Normalized files are written to timestamped directories under
`output/<source>/`. Each run stores CSV tables in `csv/`, the Excel workbook in
`excel/`, and Parquet tables in `parquet/`. Only folders for the selected export
formats are created.

Each complete export contains stable relational datasets for entities, aliases,
addresses, documents, nationalities, programs, relationships, dates and places
of birth, designations, regulations, contacts, and sanctions. Empty relations
are still emitted with their correct schema, making automated loading
predictable. Excel exports also include a `name+alias` convenience sheet with
one row per entity, Latin-script aliases tagged and ordered as strong, weak,
then former, and a distinct alias count. `manifest.json` records the source
checksum, parser schema version, selected formats, raw file and row count for
every relational table.

Each provider is isolated: a network, validation or parser error is logged and
the remaining enabled providers continue.

## Adding sources

Adding a source download requires only a new `sources.yaml` entry. To normalize
a provider whose schema differs from the four built-in formats, add a parser
function to `sanctions_parser/parsers.py` and register its configured name in
`PARSERS`. Configuration alone cannot define the semantics of an arbitrary XML
schema.

## Operational behavior

- streaming HTTP(S) downloads with redirect support
- three attempts by default with exponential backoff
- configurable timeout, chunk size and User-Agent
- automatic filename detection and sanitized local filenames
- XML and empty-response validation before files are committed
- SHA256 change detection and immutable historical raw storage
- source-specific OFAC, UN, EU and UK field mappings
- checksum- and parser-version-aware output manifests
- per-source error isolation, console progress and persistent logs

## Tests

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
black --check .
```
