"""Securely ingest BMW CarData ZIP archives into DuckDB."""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import duckdb

MAX_FILES = 20_000
MAX_FILE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 10 * 1024**3
MAX_COMPRESSION_RATIO = 250
ROW_NUMBER_COLUMN = "__archive_explorer_row"

DOMAIN_KEYWORDS = {
    "seat-profile": ("seat", "profile", "comfortexit", "comfort_exit", "memoryposition"),
    "diagnostics": ("diagnostic", "troublecode", "dtc", "fault", "checkcontrol", "fusi", "error"),
    "charging": ("charging", "charger", "chargehistory", "plug"),
    "battery": ("battery", "voltage", "stateofcharge", "state_of_charge", "energy"),
    "maintenance": ("maintenance", "service", "teleservice", "conditionbased", "cbs", "repair"),
    "location-trip": ("latitude", "longitude", "location", "position", "trip", "journey", "mileage", "odometer"),
    "climate": ("climate", "temperature", "ventilation", "heating", "aircondition"),
    "access-body": ("door", "window", "lock", "trunk", "boot", "tailgate", "hood"),
    "infotainment": ("infotainment", "navigation", "media", "telephone"),
    "data-sharing": ("consent", "clearance", "permission", "transaction", "recipient"),
}


class ArchiveSecurityError(ValueError):
    """Raised when an archive violates extraction safety limits."""


@dataclass(frozen=True)
class IngestResult:
    """Summary of one idempotent archive import."""

    archive_id: str
    sha256: str
    file_count: int
    parsed_files: int
    failed_files: int
    indexed_events: int
    database_path: str
    original_path: str
    already_imported: bool = False


def database_path(workspace: str | Path) -> Path:
    """Return explorer database path for a workspace."""

    return Path(workspace).expanduser().resolve() / "archive-explorer.duckdb"


def initialize_workspace(workspace: str | Path) -> Path:
    """Create private workspace directories and database schema."""

    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    for directory in ("originals", "extracted", "uploads"):
        path = root / directory
        path.mkdir(exist_ok=True, mode=0o700)
        if path.is_symlink() or not path.is_dir():
            raise ArchiveSecurityError(f"Workspace path must be a directory, not a link: {path}")
        path.chmod(0o700)
    if database_path(root).is_symlink():
        raise ArchiveSecurityError("Explorer database path cannot be a symbolic link")
    connection = duckdb.connect(str(database_path(root)))
    try:
        _initialize_schema(connection)
    finally:
        connection.close()
    database_path(root).chmod(0o600)
    return root


def _initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS archives (
            archive_id VARCHAR PRIMARY KEY,
            sha256 VARCHAR NOT NULL UNIQUE,
            filename VARCHAR NOT NULL,
            original_path VARCHAR NOT NULL,
            extracted_path VARCHAR NOT NULL,
            imported_at TIMESTAMPTZ NOT NULL,
            file_count BIGINT NOT NULL,
            total_uncompressed_bytes UBIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS archive_files (
            archive_id VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            size_bytes UBIGINT NOT NULL,
            compressed_bytes UBIGINT NOT NULL,
            sha256 VARCHAR NOT NULL,
            media_type VARCHAR,
            file_format VARCHAR NOT NULL,
            domain VARCHAR NOT NULL,
            table_name VARCHAR,
            row_count BIGINT,
            status VARCHAR NOT NULL,
            error VARCHAR,
            PRIMARY KEY (archive_id, path)
        );
        CREATE TABLE IF NOT EXISTS archive_columns (
            archive_id VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            table_name VARCHAR NOT NULL,
            column_index INTEGER NOT NULL,
            column_name VARCHAR NOT NULL,
            column_type VARCHAR NOT NULL,
            is_timestamp BOOLEAN NOT NULL,
            domain VARCHAR NOT NULL,
            PRIMARY KEY (archive_id, path, column_index)
        );
        CREATE TABLE IF NOT EXISTS archive_events (
            archive_id VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            table_name VARCHAR NOT NULL,
            row_number BIGINT NOT NULL,
            event_time TIMESTAMPTZ NOT NULL,
            timestamp_column VARCHAR NOT NULL,
            timestamp_value VARCHAR,
            domain VARCHAR NOT NULL
        );
        """
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(name: str) -> Path:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or not path.parts or path == PurePosixPath(".") or path.is_absolute() or ".." in path.parts:
        raise ArchiveSecurityError(f"Unsafe ZIP member path: {name!r}")
    if path.parts and ":" in path.parts[0]:
        raise ArchiveSecurityError(f"Unsafe ZIP member path: {name!r}")
    return Path(*path.parts)


def _validate_archive(archive: Path) -> list[tuple[zipfile.ZipInfo, Path]]:
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise ArchiveSecurityError("Input is not a valid ZIP archive")

    validated: list[tuple[zipfile.ZipInfo, Path]] = []
    names: set[str] = set()
    total_size = 0
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_FILES:
            raise ArchiveSecurityError(f"ZIP contains more than {MAX_FILES} entries")
        for member in members:
            relative = _safe_relative_path(member.filename)
            normalized = relative.as_posix()
            if normalized in names:
                raise ArchiveSecurityError(f"ZIP contains duplicate path: {normalized}")
            names.add(normalized)

            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ArchiveSecurityError(f"ZIP contains symbolic link: {normalized}")
            file_type = stat.S_IFMT(mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ArchiveSecurityError(f"ZIP contains unsupported special file: {normalized}")
            if member.flag_bits & 0x1:
                raise ArchiveSecurityError(f"Encrypted ZIP member is unsupported: {normalized}")
            if member.file_size > MAX_FILE_BYTES:
                raise ArchiveSecurityError(f"ZIP member exceeds size limit: {normalized}")
            if member.file_size > 1024 * 1024:
                ratio = member.file_size / max(member.compress_size, 1)
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ArchiveSecurityError(f"Suspicious compression ratio for: {normalized}")
            total_size += member.file_size
            if total_size > MAX_TOTAL_BYTES:
                raise ArchiveSecurityError("ZIP uncompressed size exceeds workspace limit")
            validated.append((member, relative))
    return validated


def _extract_archive(archive: Path, destination: Path, members: list[tuple[zipfile.ZipInfo, Path]]) -> None:
    staging = destination.parent / f".{destination.name}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, mode=0o700)
    try:
        with zipfile.ZipFile(archive) as source:
            for member, relative in members:
                target = staging / relative
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source.open(member) as compressed, target.open("xb") as extracted:
                    shutil.copyfileobj(compressed, extracted, length=1024 * 1024)
                target.chmod(0o600)
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _file_format(path: Path) -> str:
    name = path.name.lower()
    if name.endswith((".csv", ".csv.gz", ".tsv", ".tsv.gz")):
        return "csv"
    if name.endswith((".json", ".json.gz", ".jsonl", ".jsonl.gz", ".ndjson", ".ndjson.gz")):
        return "json"
    if name.endswith(".parquet"):
        return "parquet"
    if name.endswith(".xml"):
        return "xml"
    if name.endswith((".txt", ".md")):
        return "text"
    return "binary"


def classify_domain(text: str) -> str:
    """Classify file or field text into one broad vehicle-data domain."""

    normalized = re.sub(r"[^a-z0-9]+", "", text.lower())
    scores = {
        domain: sum(normalized.count(keyword.replace("_", "")) for keyword in keywords)
        for domain, keywords in DOMAIN_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def _table_name(archive_id: str, relative_path: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", Path(relative_path).stem.lower()).strip("_")[:36] or "data"
    suffix = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:8]
    return f"raw_{archive_id}_{stem}_{suffix}"


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_identifier(value: str) -> str:
    """Quote a DuckDB identifier."""

    return '"' + value.replace('"', '""') + '"'


def _load_raw_table(connection: duckdb.DuckDBPyConnection, source: Path, file_format: str, table_name: str) -> int:
    literal = _sql_literal(str(source))
    if file_format == "csv":
        reader = f"read_csv_auto({literal}, sample_size=-1)"
    elif file_format == "json":
        reader = f"read_json_auto({literal}, maximum_depth=-1)"
    elif file_format == "parquet":
        reader = f"read_parquet({literal})"
    else:
        raise ValueError(f"Unsupported tabular format: {file_format}")

    table = quote_identifier(table_name)
    connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute(
        f"CREATE TABLE {table} AS "
        f"SELECT row_number() OVER ()::BIGINT AS {quote_identifier(ROW_NUMBER_COLUMN)}, source.* FROM {reader} source"
    )
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _looks_like_timestamp(name: str, column_type: str) -> bool:
    lowered = name.lower()
    type_match = "TIMESTAMP" in column_type.upper() or column_type.upper() == "DATE"
    name_match = any(token in lowered for token in ("timestamp", "datetime", "recorded_at", "created_at", "updated_at"))
    name_match = name_match or lowered in {"date", "time", "event_time", "eventdate", "eventtime"}
    return type_match or name_match


def _index_table(
    connection: duckdb.DuckDBPyConnection,
    archive_id: str,
    relative_path: str,
    table_name: str,
) -> tuple[list[tuple[int, str, str, bool, str]], int, str]:
    table = quote_identifier(table_name)
    described = connection.execute(f"DESCRIBE {table}").fetchall()
    searchable = " ".join([relative_path, *(str(row[0]) for row in described)])
    table_domain = classify_domain(searchable)
    columns: list[tuple[int, str, str, bool, str]] = []
    events_before = int(
        connection.execute("SELECT count(*) FROM archive_events WHERE archive_id = ?", [archive_id]).fetchone()[0]
    )

    for index, row in enumerate(described):
        name, column_type = str(row[0]), str(row[1])
        if name == ROW_NUMBER_COLUMN:
            continue
        timestamp = _looks_like_timestamp(name, column_type)
        domain = classify_domain(f"{relative_path} {name}")
        if domain == "other":
            domain = table_domain
        columns.append((index, name, column_type, timestamp, domain))
        if not timestamp:
            continue
        column = quote_identifier(name)
        try:
            connection.execute(
                f"""
                INSERT INTO archive_events
                SELECT ?, ?, ?, {quote_identifier(ROW_NUMBER_COLUMN)}, try_cast({column} AS TIMESTAMPTZ),
                       ?, cast({column} AS VARCHAR), ?
                FROM {table}
                WHERE try_cast({column} AS TIMESTAMPTZ) IS NOT NULL
                """,
                [archive_id, relative_path, table_name, name, domain],
            )
        except duckdb.Error:
            continue
    events_after = int(
        connection.execute("SELECT count(*) FROM archive_events WHERE archive_id = ?", [archive_id]).fetchone()[0]
    )
    return columns, events_after - events_before, table_domain


def _existing_result(connection: duckdb.DuckDBPyConnection, sha256: str, db_path: Path) -> IngestResult | None:
    row = connection.execute(
        """
        SELECT a.archive_id, a.sha256, a.file_count, a.original_path,
               count(*) FILTER (WHERE f.status = 'parsed'),
               count(*) FILTER (WHERE f.status = 'error'),
               (SELECT count(*) FROM archive_events e WHERE e.archive_id = a.archive_id)
        FROM archives a
        LEFT JOIN archive_files f USING (archive_id)
        WHERE a.sha256 = ?
        GROUP BY a.archive_id, a.sha256, a.file_count, a.original_path
        """,
        [sha256],
    ).fetchone()
    if row is None:
        return None
    return IngestResult(
        archive_id=row[0],
        sha256=row[1],
        file_count=int(row[2]),
        parsed_files=int(row[4]),
        failed_files=int(row[5]),
        indexed_events=int(row[6]),
        database_path=str(db_path),
        original_path=str(row[3]),
        already_imported=True,
    )


def ingest_archive(archive: str | Path, workspace: str | Path = "data/archive-explorer") -> IngestResult:
    """Validate, preserve, extract, and index one ZIP archive."""

    source = Path(archive).expanduser().resolve()
    members = _validate_archive(source)
    sha256 = _sha256(source)
    archive_id = sha256[:16]
    root = initialize_workspace(workspace)
    db_path = database_path(root)
    connection = duckdb.connect(str(db_path))
    try:
        existing = _existing_result(connection, sha256, db_path)
        if existing is not None:
            preserved = Path(existing.original_path)
            if preserved.exists():
                if preserved.is_symlink() or not preserved.is_file() or _sha256(preserved) != sha256:
                    raise ArchiveSecurityError("Preserved archive path does not contain expected ZIP")
            else:
                shutil.copyfile(source, preserved)
                preserved.chmod(0o400)
            return existing

        connection.execute("DELETE FROM archive_events WHERE archive_id = ?", [archive_id])
        stale_tables = connection.execute(
            "SELECT table_name FROM archive_files WHERE archive_id = ? AND table_name IS NOT NULL", [archive_id]
        ).fetchall()
        for (table_name,) in stale_tables:
            connection.execute(f"DROP TABLE IF EXISTS {quote_identifier(str(table_name))}")
        connection.execute("DELETE FROM archive_columns WHERE archive_id = ?", [archive_id])
        connection.execute("DELETE FROM archive_files WHERE archive_id = ?", [archive_id])

        extracted_path = root / "extracted" / archive_id
        if extracted_path.exists():
            if extracted_path.is_symlink():
                raise ArchiveSecurityError("Archive extraction path cannot be a symbolic link")
            shutil.rmtree(extracted_path)
        _extract_archive(source, extracted_path, members)

        original_path = root / "originals" / f"{sha256}.zip"
        if original_path.exists():
            if original_path.is_symlink() or not original_path.is_file() or _sha256(original_path) != sha256:
                raise ArchiveSecurityError("Preserved archive path does not contain expected ZIP")
        else:
            shutil.copyfile(source, original_path)
            original_path.chmod(0o400)

        member_sizes = {
            relative.as_posix(): member.compress_size for member, relative in members if not member.is_dir()
        }
        parsed_files = 0
        failed_files = 0
        indexed_events = 0
        file_count = 0
        total_size = 0

        for extracted in sorted(path for path in extracted_path.rglob("*") if path.is_file()):
            relative = extracted.relative_to(extracted_path).as_posix()
            size = extracted.stat().st_size
            total_size += size
            file_count += 1
            file_format = _file_format(extracted)
            media_type = mimetypes.guess_type(extracted.name)[0]
            table_name: str | None = None
            row_count: int | None = None
            status = "inventory-only"
            error: str | None = None
            domain = classify_domain(relative)

            if file_format in {"csv", "json", "parquet"}:
                table_name = _table_name(archive_id, relative)
                try:
                    row_count = _load_raw_table(connection, extracted, file_format, table_name)
                    columns, event_count, detected_domain = _index_table(connection, archive_id, relative, table_name)
                    domain = detected_domain
                    indexed_events += event_count
                    for column_index, column_name, column_type, is_timestamp, column_domain in columns:
                        connection.execute(
                            "INSERT INTO archive_columns VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            [
                                archive_id,
                                relative,
                                table_name,
                                column_index,
                                column_name,
                                column_type,
                                is_timestamp,
                                column_domain,
                            ],
                        )
                    status = "parsed"
                    parsed_files += 1
                except duckdb.Error as exc:
                    connection.execute(
                        "DELETE FROM archive_events WHERE archive_id = ? AND path = ?", [archive_id, relative]
                    )
                    connection.execute(
                        "DELETE FROM archive_columns WHERE archive_id = ? AND path = ?", [archive_id, relative]
                    )
                    connection.execute(f"DROP TABLE IF EXISTS {quote_identifier(table_name)}")
                    table_name = None
                    status = "error"
                    error = str(exc)[:2000]
                    failed_files += 1

            connection.execute(
                "INSERT INTO archive_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    archive_id,
                    relative,
                    size,
                    member_sizes.get(relative, size),
                    _sha256(extracted),
                    media_type,
                    file_format,
                    domain,
                    table_name,
                    row_count,
                    status,
                    error,
                ],
            )

        connection.execute(
            "INSERT INTO archives VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                archive_id,
                sha256,
                source.name,
                str(original_path),
                str(extracted_path),
                datetime.now(timezone.utc),
                file_count,
                total_size,
            ],
        )
        return IngestResult(
            archive_id=archive_id,
            sha256=sha256,
            file_count=file_count,
            parsed_files=parsed_files,
            failed_files=failed_files,
            indexed_events=indexed_events,
            database_path=str(db_path),
            original_path=str(original_path),
        )
    finally:
        connection.close()
