import json
from pathlib import Path

from sanctions_parser.config import SourceConfig
from sanctions_parser.downloader import DownloadResult
from sanctions_parser.models import Entity, NormalizedData
from sanctions_parser.pipeline import process_source


def test_unchanged_source_is_parsed_when_current_export_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw" / "ofac.xml"
    raw.parent.mkdir()
    raw.write_text("<sdnList/>", encoding="utf-8")
    state = tmp_path / ".state"
    state.mkdir()
    (state / "ofac.json").write_text(
        json.dumps({"checksum": "abc", "path": str(raw)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sanctions_parser.pipeline.download_source",
        lambda *args, **kwargs: DownloadResult("ofac", None, "abc", False, 10),
    )
    monkeypatch.setattr(
        "sanctions_parser.pipeline.parse",
        lambda *args, **kwargs: NormalizedData(
            entities=[Entity("1", "ofac", "Entity", "Test")]
        ),
    )
    source = SourceConfig("ofac", "https://example.test/ofac.xml", "ofac")

    first = process_source(
        source,
        tmp_path,
        export_formats={"excel"},
        show_download_progress=False,
    )

    assert first.status == "processed"
    assert first.entities == 1
    assert first.report is not None
    assert first.report.entities == 1
    assert (Path(first.detail) / "manifest.json").is_file()
    manifest = json.loads(
        (Path(first.detail) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["parsing_summary"]["entities"] == 1

    monkeypatch.setattr(
        "sanctions_parser.pipeline.parse",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("current unchanged export should not be parsed again")
        ),
    )
    second = process_source(
        source,
        tmp_path,
        export_formats={"excel"},
        show_download_progress=False,
    )

    assert second.status == "unchanged"
    assert second.entities == 1
    assert second.report == first.report
