from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from archive_explorer.ingest import ArchiveSecurityError, classify_domain, ingest_archive


def _write_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_ingests_supported_files_and_preserves_provenance(tmp_path: Path):
    archive = tmp_path / "bmw-archive.zip"
    workspace = tmp_path / "workspace"
    _write_zip(
        archive,
        {
            "seat/events.csv": (
                "event_timestamp,adj_func_state,fusi_event_error_id,latitude,longitude\n"
                "2026-08-25T10:00:00Z,HardwareError,AntipinchMonitoringFailed,44.42,26.10\n"
                "2026-08-25T10:05:00Z,Successful,,44.43,26.11\n"
            ),
            "battery/status.json": '[{"recorded_at":"2026-08-25T09:59:00Z","voltage":12.1}]',
            "README.txt": "BMW archive metadata",
        },
    )

    result = ingest_archive(archive, workspace)

    assert result.file_count == 3
    assert result.parsed_files == 2
    assert result.failed_files == 0
    assert result.indexed_events == 3
    assert Path(result.database_path).stat().st_mode & 0o777 == 0o600
    assert Path(result.original_path).stat().st_mode & 0o777 == 0o400

    connection = duckdb.connect(result.database_path, read_only=True)
    try:
        statuses = dict(connection.execute("SELECT path, status FROM archive_files").fetchall())
        assert statuses == {
            "README.txt": "inventory-only",
            "battery/status.json": "parsed",
            "seat/events.csv": "parsed",
        }
        fields = {row[0] for row in connection.execute("SELECT column_name FROM archive_columns").fetchall()}
        assert {"event_timestamp", "adj_func_state", "fusi_event_error_id", "recorded_at", "voltage"} <= fields
        table_name = connection.execute(
            "SELECT table_name FROM archive_files WHERE path = 'seat/events.csv'"
        ).fetchone()[0]
        raw_row = connection.execute(
            f'SELECT adj_func_state FROM "{table_name}" WHERE __archive_explorer_row = 1'
        ).fetchone()
        assert raw_row == ("HardwareError",)
    finally:
        connection.close()

    preserved = Path(result.original_path)
    preserved.unlink()
    duplicate = ingest_archive(archive, workspace)
    assert duplicate.already_imported
    assert preserved.is_file()
    assert preserved.stat().st_mode & 0o777 == 0o400
    connection = duckdb.connect(result.database_path, read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM archives").fetchone()[0] == 1
    finally:
        connection.close()


def test_rejects_parent_path_before_extraction(tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    _write_zip(archive, {"../outside.txt": "nope"})

    with pytest.raises(ArchiveSecurityError, match="Unsafe ZIP member path"):
        ingest_archive(archive, tmp_path / "workspace")

    assert not (tmp_path / "outside.txt").exists()


def test_rejects_symbolic_link(tmp_path: Path):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(link, "../../target")

    with pytest.raises(ArchiveSecurityError, match="symbolic link"):
        ingest_archive(archive, tmp_path / "workspace")


def test_rejects_suspicious_compression_ratio(tmp_path: Path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("huge.csv", "0" * (2 * 1024 * 1024))

    with pytest.raises(ArchiveSecurityError, match="Suspicious compression ratio"):
        ingest_archive(archive, tmp_path / "workspace")


def test_domain_classification_covers_diagnostic_views():
    assert classify_domain("te_events_usagecounter_seat adj_func_state") == "seat-profile"
    assert classify_domain("diagnosticTroubleCodes raw fault") == "diagnostics"
    assert classify_domain("electricalSystem battery voltage") == "battery"


def test_streamlit_pages_render_with_imported_archive(tmp_path: Path, monkeypatch):
    archive = tmp_path / "ui-archive.zip"
    workspace = tmp_path / "workspace"
    _write_zip(
        archive,
        {
            "seat/events.csv": (
                "event_timestamp,adj_func_state,latitude,longitude\n2026-08-25T10:00:00Z,HardwareError,44.42,26.10\n"
            )
        },
    )
    ingest_archive(archive, workspace)
    second_archive = tmp_path / "ui-archive-two.zip"
    _write_zip(
        second_archive,
        {
            "seat/events.csv": (
                "event_timestamp,adj_func_state,latitude,longitude\n"
                "2026-08-26T10:00:00Z,Successful,44.43,26.11\n"
                "2026-08-26T10:05:00Z,HardwareError,44.44,26.12\n"
            )
        },
    )
    ingest_archive(second_archive, workspace)
    monkeypatch.setattr(sys, "argv", ["streamlit", "--workspace", str(workspace)])

    app_path = Path(__file__).parents[1] / "archive_explorer" / "ui.py"
    app = AppTest.from_file(app_path, default_timeout=20).run()
    assert not app.exception
    for page in (
        "Catalogue",
        "Timeline",
        "Domains",
        "Seat investigation",
        "Correlate",
        "Map",
        "Files & SQL",
        "Compare archives",
    ):
        app.sidebar.radio[0].set_value(page)
        app.run()
        assert not app.exception, f"{page}: {[exception.value for exception in app.exception]}"
