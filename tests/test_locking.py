from pathlib import Path

import pytest

from sanctions_parser.locking import SourceBusyError, source_lock


def test_second_source_run_cannot_acquire_active_lock(tmp_path: Path) -> None:
    with (
        source_lock(tmp_path, "ofac"),
        pytest.raises(SourceBusyError, match="already being processed"),
        source_lock(tmp_path, "ofac"),
    ):
        pass

    assert not (tmp_path / "locks" / "ofac.lock").exists()
