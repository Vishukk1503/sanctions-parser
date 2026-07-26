import json
from datetime import UTC, datetime
from pathlib import Path

import responses

from sanctions_parser.config import SourceConfig
from sanctions_parser.downloader import download_source
from sanctions_parser.health import check_uplink, compare_source_dates


@responses.activate
def test_unchanged_download_is_skipped(tmp_path: Path) -> None:
    url = "https://example.test/list"
    xml = b"<root><record>ok</record></root>"
    responses.add(
        responses.GET,
        url,
        body=xml,
        headers={"Content-Disposition": 'attachment; filename="list.xml"'},
        status=200,
    )
    source = SourceConfig("demo", url, "ofac")
    first = download_source(source, tmp_path / "raw", tmp_path / "state")
    assert first.changed is True
    responses.add(responses.GET, url, body=xml, status=200)
    second = download_source(source, tmp_path / "raw", tmp_path / "state")
    assert second.changed is False
    assert second.path is None


@responses.activate
def test_matching_download_restores_missing_cached_raw_file(tmp_path: Path) -> None:
    url = "https://example.test/list.xml"
    xml = b"<root><record>ok</record></root>"
    responses.add(responses.GET, url, body=xml, status=200)
    source = SourceConfig("demo", url, "ofac")

    first = download_source(source, tmp_path / "raw", tmp_path / "state")
    assert first.path is not None
    first.path.unlink()

    responses.add(responses.GET, url, body=xml, status=200)
    restored = download_source(source, tmp_path / "raw", tmp_path / "state")

    assert restored.changed is True
    assert restored.path is not None
    assert restored.path.is_file()
    assert restored.path.read_bytes() == xml
    assert restored.checksum == first.checksum
    state = json.loads((tmp_path / "state" / "demo.json").read_text(encoding="utf-8"))
    assert state["path"] == str(restored.path)


@responses.activate
def test_invalid_xml_is_rejected(tmp_path: Path) -> None:
    url = "https://example.test/list.xml"
    responses.add(responses.GET, url, body=b"not xml", status=200)
    source = SourceConfig("demo", url, "ofac")
    try:
        download_source(source, tmp_path / "raw", tmp_path / "state")
    except ValueError as exc:
        assert "valid XML" in str(exc)
    else:
        raise AssertionError("Invalid XML was accepted")


@responses.activate
def test_xml_error_document_is_rejected(tmp_path: Path) -> None:
    url = "https://example.test/list.xml"
    responses.add(
        responses.GET,
        url,
        body=b"<Error><Code>NoSuchKey</Code></Error>",
        status=200,
    )
    source = SourceConfig("demo", url, "ofac", expected_root="sdnList")
    try:
        download_source(source, tmp_path / "raw", tmp_path / "state")
    except ValueError as exc:
        assert "error document" in str(exc)
    else:
        raise AssertionError("XML error response was accepted")


@responses.activate
def test_unexpected_source_root_is_rejected(tmp_path: Path) -> None:
    url = "https://example.test/list.xml"
    responses.add(responses.GET, url, body=b"<wrongRoot/>", status=200)
    source = SourceConfig("demo", url, "ofac", expected_root="sdnList")
    try:
        download_source(source, tmp_path / "raw", tmp_path / "state")
    except ValueError as exc:
        assert "expected 'sdnList'" in str(exc)
    else:
        raise AssertionError("Wrong source schema was accepted")


@responses.activate
def test_uplink_probe_verifies_expected_xml_without_saving() -> None:
    url = "https://example.test/list.xml"
    responses.add(
        responses.GET,
        url,
        body=b"<?xml version='1.0'?><sdnList><entry/></sdnList>",
        status=200,
    )
    source = SourceConfig("ofac", url, "ofac", expected_root="sdnList", retries=1)

    result = check_uplink(source)

    assert result.state == "healthy"
    assert result.http_status == 200
    assert result.xml_root == "sdnList"


@responses.activate
def test_uplink_probe_reads_ofac_publication_date() -> None:
    url = "https://example.test/list.xml"
    responses.add(
        responses.GET,
        url,
        body=(
            b"<sdnList><publshInformation>"
            b"<Publish_Date>07/24/2026</Publish_Date>"
            b"</publshInformation><entry/></sdnList>"
        ),
        status=200,
    )
    source = SourceConfig("ofac", url, "ofac", expected_root="sdnList", retries=1)

    result = check_uplink(source)

    assert result.source_date == datetime(2026, 7, 24, tzinfo=UTC)


@responses.activate
def test_uplink_probe_reads_eu_generation_date_attribute() -> None:
    url = "https://example.test/list.xml"
    responses.add(
        responses.GET,
        url,
        body=b'<export generationDate="2026-07-25T10:05:36+02:00"><entry/></export>',
        status=200,
    )
    source = SourceConfig("eu", url, "eu", expected_root="export", retries=1)

    result = check_uplink(source)

    assert result.source_date == datetime(2026, 7, 25, 8, 5, 36, tzinfo=UTC)


def test_source_date_comparison_detects_update() -> None:
    local = datetime(2026, 7, 24, tzinfo=UTC)
    live = datetime(2026, 7, 25, tzinfo=UTC)

    result = compare_source_dates(local, live)

    assert result.state == "update"
    assert result.label == "UPDATE AVAILABLE"


@responses.activate
def test_uplink_probe_reports_wrong_payload_as_degraded() -> None:
    url = "https://example.test/list.xml"
    responses.add(
        responses.GET,
        url,
        body=b"<html><body>Login required</body></html>",
        status=200,
    )
    source = SourceConfig("eu", url, "eu", expected_root="export", retries=1)

    result = check_uplink(source)

    assert result.state == "degraded"
    assert result.xml_root == "html"
    assert "Expected XML root" in result.detail


@responses.activate
def test_uplink_probe_reports_http_error_as_degraded() -> None:
    url = "https://example.test/list.xml"
    responses.add(responses.GET, url, body=b"Forbidden", status=403)
    source = SourceConfig("eu", url, "eu", expected_root="export", retries=1)

    result = check_uplink(source)

    assert result.state == "degraded"
    assert result.http_status == 403
    assert result.detail == "Provider returned HTTP 403"
