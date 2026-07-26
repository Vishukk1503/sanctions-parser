from pathlib import Path

from sanctions_parser.config import load_sources


def test_defaults_are_merged(tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_text(
        """
defaults:
  retries: 2
sources:
  demo:
    enabled: true
    url: https://example.test/list.xml
    parser: ofac
""",
        encoding="utf-8",
    )
    source = load_sources(config)["demo"]
    assert source.retries == 2
    assert source.url.endswith("list.xml")
