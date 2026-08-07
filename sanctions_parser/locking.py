from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class SourceBusyError(RuntimeError):
    """Raised when another process is already working on a source."""


@contextmanager
def source_lock(
    state_root: Path,
    source_name: str,
    *,
    stale_after_seconds: int = 12 * 60 * 60,
) -> Iterator[None]:
    """Prevent full and delta runs from mutating one source concurrently."""
    lock_root = state_root / "locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{source_name}.lock"

    for attempt in range(2):
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0
            if attempt == 0 and age > stale_after_seconds:
                lock_path.unlink(missing_ok=True)
                continue
            raise SourceBusyError(
                f"{source_name.upper()} is already being processed by another run"
            ) from exc
        else:
            break
    else:  # pragma: no cover - the loop either acquires or raises
        raise SourceBusyError(f"Could not acquire the {source_name} source lock")

    try:
        payload = {
            "pid": os.getpid(),
            "source": source_name,
            "started_at": datetime.now(UTC).isoformat(),
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        yield
    finally:
        lock_path.unlink(missing_ok=True)
