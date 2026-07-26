from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
from lxml import etree
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

from .config import SourceConfig

LOGGER = logging.getLogger("sanctions_parser.download")


@dataclass(frozen=True)
class DownloadResult:
    source: str
    path: Path | None
    checksum: str
    changed: bool
    bytes_downloaded: int


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(value).name)
    return cleaned or "sanctions.xml"


def detect_filename(response: requests.Response, url: str) -> str:
    disposition = response.headers.get("Content-Disposition")
    if disposition:
        message = Message()
        message["content-disposition"] = disposition
        candidate = message.get_filename()
        if candidate:
            return _safe_filename(unquote(candidate))
    parsed = urlparse(response.url or url)
    query_filename = parse_qs(parsed.query).get("filename")
    if query_filename:
        return _safe_filename(query_filename[0])
    candidate = Path(unquote(parsed.path)).name
    return _safe_filename(candidate if "." in candidate else "sanctions.xml")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_xml(path: Path, expected_root: str | None = None) -> None:
    try:
        root_name = ""
        for _event, element in etree.iterparse(str(path), events=("start",)):
            root_name = etree.QName(element).localname
            break
    except (etree.XMLSyntaxError, OSError) as exc:
        raise ValueError(f"Downloaded file is not valid XML: {exc}") from exc
    if root_name.lower() == "error":
        raise ValueError("Provider returned an XML error document")
    if expected_root and root_name != expected_root:
        raise ValueError(
            f"Unexpected XML root {root_name!r}; expected {expected_root!r}"
        )


def _session(retries: int) -> requests.Session:
    retry = Retry(
        total=max(retries - 1, 0),
        connect=max(retries - 1, 0),
        read=max(retries - 1, 0),
        status=max(retries - 1, 0),
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def download_source(
    source: SourceConfig,
    raw_root: Path,
    state_root: Path,
    show_progress: bool = True,
) -> DownloadResult:
    """Stream, validate and commit a source file only when its content changes."""
    state_root.mkdir(parents=True, exist_ok=True)
    state_path = state_root / f"{source.name}.json"
    previous: dict[str, str] = {}
    if state_path.exists():
        previous = json.loads(state_path.read_text(encoding="utf-8"))

    day_dir = raw_root / source.name / datetime.now().astimezone().date().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    temporary = day_dir / ".download.part"
    session = _session(source.retries)
    try:
        LOGGER.info("Downloading %s from %s", source.name, source.url)
        with session.get(
            source.url,
            stream=True,
            timeout=(15, source.timeout_seconds),
            headers={"User-Agent": source.user_agent, "Accept": "application/xml,*/*"},
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length", 0))
            filename = detect_filename(response, source.url)
            digest = hashlib.sha256()
            downloaded = 0
            with (
                temporary.open("wb") as handle,
                tqdm(
                    total=content_length or None,
                    unit="B",
                    unit_scale=True,
                    desc=source.name.upper(),
                    leave=False,
                    disable=not show_progress,
                ) as progress,
            ):
                for chunk in response.iter_content(chunk_size=source.chunk_size):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    progress.update(len(chunk))
        if downloaded == 0:
            raise ValueError("Provider returned an empty response")
        validate_xml(temporary, source.expected_root)
        checksum = digest.hexdigest()
        previous_path_value = previous.get("path")
        previous_path = Path(previous_path_value) if previous_path_value else None
        if (
            checksum == previous.get("checksum")
            and previous_path is not None
            and previous_path.is_file()
        ):
            temporary.unlink(missing_ok=True)
            LOGGER.info("%s is unchanged; cached raw file retained", source.name)
            return DownloadResult(source.name, None, checksum, False, downloaded)
        if checksum == previous.get("checksum"):
            LOGGER.warning(
                "%s checksum is unchanged but its cached raw file is missing; "
                "restoring the download",
                source.name,
            )

        destination = day_dir / filename
        if destination.exists():
            destination = (
                day_dir / f"{destination.stem}-{checksum[:8]}{destination.suffix}"
            )
        temporary.replace(destination)
        state_path.write_text(
            json.dumps(
                {"checksum": checksum, "path": str(destination), "url": source.url},
                indent=2,
            ),
            encoding="utf-8",
        )
        LOGGER.info("Stored %s (%d bytes)", destination, downloaded)
        return DownloadResult(source.name, destination, checksum, True, downloaded)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        session.close()
