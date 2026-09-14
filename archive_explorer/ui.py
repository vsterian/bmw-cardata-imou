"""Streamlit user interface for local BMW CarData archive exploration."""

from __future__ import annotations

import argparse
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from archive_explorer.ingest import database_path, ingest_archive, initialize_workspace, quote_identifier

VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
PAGES = (
    "Overview",
    "Catalogue",
    "Timeline",
    "Domains",
    "Seat investigation",
    "Correlate",
    "Map",
    "Files & SQL",
    "Compare archives",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", default="data/archive-explorer")
    return parser.parse_known_args()[0]


def _query(workspace: Path, sql: str, parameters: list[object] | None = None) -> pd.DataFrame:
    connection = duckdb.connect(str(database_path(workspace)), read_only=True)
    try:
        return connection.execute(sql, parameters or []).fetchdf()
    finally:
        connection.close()


def _scalar(workspace: Path, sql: str, parameters: list[object] | None = None) -> object | None:
    connection = duckdb.connect(str(database_path(workspace)), read_only=True)
    try:
        row = connection.execute(sql, parameters or []).fetchone()
        return None if row is None else row[0]
    finally:
        connection.close()


def _where_domains(domains: list[str], parameters: list[object]) -> str:
    if not domains:
        return ""
    parameters.extend(domains)
    return " AND domain IN (" + ", ".join("?" for _ in domains) + ")"


def _redact(frame: pd.DataFrame) -> pd.DataFrame:
    redacted = frame.copy()
    for column in redacted.columns:
        lowered = str(column).lower()
        if "latitude" in lowered or "longitude" in lowered:
            numeric = pd.to_numeric(redacted[column], errors="coerce")
            redacted[column] = numeric.round(2).where(numeric.notna(), "[LOCATION_REDACTED]")
            continue
        if "address" in lowered or lowered in {"location", "position"}:
            redacted[column] = "[LOCATION_REDACTED]"
            continue
        if "vin" in lowered:
            redacted[column] = redacted[column].map(
                lambda value: f"[VIN_REDACTED:{str(value)[-4:]}]" if pd.notna(value) else value
            )
            continue
        if redacted[column].dtype == object or pd.api.types.is_string_dtype(redacted[column]):
            redacted[column] = redacted[column].map(
                lambda value: VIN_PATTERN.sub("[VIN_REDACTED]", str(value)) if pd.notna(value) else value
            )
    return redacted


def _download(frame: pd.DataFrame, filename: str) -> None:
    mode = st.radio("Export privacy", ("Redacted", "Raw"), horizontal=True, key=f"privacy-{filename}")
    exported = _redact(frame) if mode == "Redacted" else frame
    if mode == "Raw":
        st.warning("Raw export may contain VIN, precise location, and personal telemetry.")
    st.download_button(
        "Download displayed rows as CSV",
        exported.to_csv(index=False).encode("utf-8"),
        filename,
        "text/csv",
        key=f"download-{filename}-{mode}",
    )


def _event_drilldown(workspace: Path, events: pd.DataFrame, key: str) -> None:
    if events.empty:
        return
    choices = list(range(min(len(events), 200)))
    selected = st.selectbox(
        "Inspect source record",
        choices,
        format_func=lambda index: (
            f"{events.iloc[index]['event_time']} · {events.iloc[index]['domain']} · "
            f"{events.iloc[index]['path']} · row {events.iloc[index]['row_number']}"
        ),
        key=key,
    )
    event = events.iloc[selected]
    table = quote_identifier(str(event["table_name"]))
    record = _query(
        workspace,
        f"SELECT * FROM {table} WHERE {quote_identifier('__archive_explorer_row')} = ?",
        [int(event["row_number"])],
    )
    st.dataframe(record, width="stretch", hide_index=True)


def _render_upload(workspace: Path) -> None:
    st.subheader("Import archive")
    st.caption("Archive stays on this host. Original ZIP is preserved by SHA-256 under ignored data storage.")
    uploaded = st.file_uploader("BMW CarData ZIP", type=["zip"], accept_multiple_files=False)
    if uploaded is None or not st.button("Validate and import", type="primary"):
        return
    temporary = workspace / "uploads" / f"{uuid.uuid4().hex}.zip"
    try:
        with temporary.open("xb") as destination:
            destination.write(uploaded.getbuffer())
        with st.spinner("Validating, inventorying, and indexing archive..."):
            result = ingest_archive(temporary, workspace)
        suffix = " Archive was already imported." if result.already_imported else ""
        st.success(
            f"Imported {result.file_count} files; {result.parsed_files} tabular files parsed; "
            f"{result.indexed_events} timestamped records indexed.{suffix}"
        )
        st.rerun()
    except (OSError, ValueError, duckdb.Error) as exc:
        st.error(f"Import failed: {exc}")
    finally:
        temporary.unlink(missing_ok=True)


def _overview(workspace: Path, archive_id: str) -> None:
    summary = _query(
        workspace,
        """
        SELECT a.filename, a.sha256, a.imported_at, a.file_count, a.total_uncompressed_bytes,
               count(*) FILTER (WHERE f.status = 'parsed') AS parsed_files,
               count(*) FILTER (WHERE f.status = 'error') AS failed_files,
               coalesce(sum(f.row_count), 0) AS rows,
               (SELECT count(*) FROM archive_events e WHERE e.archive_id = a.archive_id) AS events,
               (SELECT min(event_time) FROM archive_events e WHERE e.archive_id = a.archive_id) AS first_event,
               (SELECT max(event_time) FROM archive_events e WHERE e.archive_id = a.archive_id) AS last_event
        FROM archives a LEFT JOIN archive_files f USING (archive_id)
        WHERE a.archive_id = ?
        GROUP BY ALL
        """,
        [archive_id],
    ).iloc[0]
    metric_columns = st.columns(5)
    metric_columns[0].metric("Files", int(summary["file_count"]))
    metric_columns[1].metric("Parsed", int(summary["parsed_files"]))
    metric_columns[2].metric("Rows", f"{int(summary['rows']):,}")
    metric_columns[3].metric("Events", f"{int(summary['events']):,}")
    metric_columns[4].metric("Size", f"{int(summary['total_uncompressed_bytes']) / 1024**2:.1f} MiB")
    st.caption(f"SHA-256 `{summary['sha256']}` · imported {summary['imported_at']}")
    if pd.notna(summary["first_event"]):
        st.write(f"Detected time coverage: **{summary['first_event']}** → **{summary['last_event']}**")

    left, right = st.columns(2)
    formats = _query(
        workspace,
        "SELECT file_format, count(*) AS files FROM archive_files WHERE archive_id = ? GROUP BY 1 ORDER BY 2 DESC",
        [archive_id],
    )
    domains = _query(
        workspace,
        "SELECT domain, count(*) AS files FROM archive_files WHERE archive_id = ? GROUP BY 1 ORDER BY 2 DESC",
        [archive_id],
    )
    with left:
        st.subheader("Formats")
        st.bar_chart(formats, x="file_format", y="files")
    with right:
        st.subheader("Detected domains")
        st.bar_chart(domains, x="domain", y="files")

    inventory = _query(
        workspace,
        """
        SELECT path, file_format, domain, status, size_bytes, row_count, error
        FROM archive_files WHERE archive_id = ? ORDER BY path
        """,
        [archive_id],
    )
    st.subheader("File inventory")
    st.dataframe(inventory, width="stretch", hide_index=True)


def _catalogue(workspace: Path, archive_id: str) -> None:
    search = st.text_input("Search paths, fields, types, and domains")
    pattern = f"%{search.lower()}%"
    catalogue = _query(
        workspace,
        """
        SELECT c.path, c.domain, c.column_name, c.column_type, c.is_timestamp, f.row_count, c.table_name
        FROM archive_columns c
        JOIN archive_files f USING (archive_id, path)
        WHERE c.archive_id = ?
          AND (? = '%%' OR lower(c.path || ' ' || c.column_name || ' ' || c.column_type || ' ' || c.domain) LIKE ?)
        ORDER BY c.path, c.column_index
        """,
        [archive_id, pattern, pattern],
    )
    st.write(f"{len(catalogue):,} matching fields")
    st.dataframe(catalogue, width="stretch", hide_index=True)


def _timeline(workspace: Path, archive_id: str) -> None:
    available = _query(
        workspace, "SELECT DISTINCT domain FROM archive_events WHERE archive_id = ? ORDER BY domain", [archive_id]
    )["domain"].tolist()
    domains = st.multiselect("Domains", available)
    limit = st.slider("Maximum events", 100, 10_000, 2_000, step=100)
    parameters: list[object] = [archive_id]
    domain_clause = _where_domains(domains, parameters)
    events = _query(
        workspace,
        f"""
        SELECT event_time, domain, path, timestamp_column, timestamp_value, table_name, row_number
        FROM archive_events WHERE archive_id = ? {domain_clause}
        ORDER BY event_time DESC LIMIT {limit}
        """,
        parameters,
    )
    histogram = _query(
        workspace,
        f"""
        SELECT date_trunc('day', event_time) AS day, count(*) AS events
        FROM archive_events WHERE archive_id = ? {domain_clause}
        GROUP BY 1 ORDER BY 1
        """,
        parameters,
    )
    if not histogram.empty:
        st.line_chart(histogram, x="day", y="events")
    st.dataframe(events.drop(columns=["table_name"]), width="stretch", hide_index=True)
    _event_drilldown(workspace, events, "timeline-drilldown")


def _domains(workspace: Path, archive_id: str) -> None:
    available = _query(
        workspace,
        """
        SELECT domain FROM archive_files WHERE archive_id = ?
        UNION SELECT domain FROM archive_columns WHERE archive_id = ?
        ORDER BY domain
        """,
        [archive_id, archive_id],
    )["domain"].tolist()
    if not available:
        st.info("Archive contains no datasets to classify.")
        return
    selected = st.selectbox("Vehicle-data domain", available)
    files = _query(
        workspace,
        "SELECT path, file_format, status, row_count FROM archive_files WHERE archive_id = ? AND domain = ? ORDER BY path",
        [archive_id, selected],
    )
    fields = _query(
        workspace,
        """
        SELECT path, column_name, column_type, is_timestamp
        FROM archive_columns WHERE archive_id = ? AND domain = ? ORDER BY path, column_index
        """,
        [archive_id, selected],
    )
    recent = _query(
        workspace,
        """
        SELECT event_time, path, timestamp_column, timestamp_value, table_name, row_number, domain
        FROM archive_events WHERE archive_id = ? AND domain = ? ORDER BY event_time DESC LIMIT 1000
        """,
        [archive_id, selected],
    )
    st.subheader("Datasets")
    st.dataframe(files, width="stretch", hide_index=True)
    st.subheader("Fields")
    st.dataframe(fields, width="stretch", hide_index=True)
    st.subheader("Timestamped records")
    st.dataframe(recent.drop(columns=["table_name"]), width="stretch", hide_index=True)
    _event_drilldown(workspace, recent, "domain-drilldown")


def _seat_investigation(workspace: Path, archive_id: str) -> None:
    columns = _query(
        workspace,
        """
        SELECT path, table_name, column_name, column_type, is_timestamp
        FROM archive_columns
        WHERE archive_id = ?
          AND (domain = 'seat-profile' OR lower(path || ' ' || column_name) LIKE '%seat%')
        ORDER BY path, column_index
        """,
        [archive_id],
    )
    if columns.empty:
        st.info("No seat or profile datasets were detected. Catalogue and domain views still expose all archive data.")
        return

    st.subheader("Detected seat/profile fields")
    st.dataframe(columns.drop(columns=["table_name"]), width="stretch", hide_index=True)
    signal_tokens = (
        "state",
        "status",
        "error",
        "constraint",
        "contraint",
        "source",
        "command",
        "affected",
        "profile",
        "position",
        "operation",
        "mode",
        "function",
    )
    statements: list[str] = []
    parameters: list[object] = []
    for (path, table_name), group in columns.groupby(["path", "table_name"]):
        timestamps = group[group["is_timestamp"]]["column_name"].tolist()
        timestamp = str(timestamps[0]) if timestamps else None
        for signal in group["column_name"].tolist():
            if not any(token in str(signal).lower() for token in signal_tokens):
                continue
            table = quote_identifier(str(table_name))
            field = quote_identifier(str(signal))
            if timestamp:
                timestamp_field = quote_identifier(timestamp)
                time_expression = f"try_cast({timestamp_field} AS TIMESTAMPTZ)"
                value_expression = f"cast({timestamp_field} AS VARCHAR)"
            else:
                time_expression = "NULL::TIMESTAMPTZ"
                value_expression = "NULL::VARCHAR"
            statements.append(
                f"""
                SELECT {time_expression} AS event_time, 'seat-profile' AS domain, ? AS path,
                       ? AS timestamp_column, {value_expression} AS timestamp_value,
                       ? AS table_name, {quote_identifier("__archive_explorer_row")} AS row_number,
                       ? AS signal, cast({field} AS VARCHAR) AS signal_value
                FROM {table} WHERE {field} IS NOT NULL
                """
            )
            parameters.extend([str(path), timestamp or "", str(table_name), str(signal)])

    if not statements:
        st.info("Seat datasets exist, but no state, error, source, profile, position, or function fields were found.")
        return
    signals = _query(
        workspace,
        "SELECT * FROM (" + " UNION ALL ".join(statements) + ") ORDER BY event_time DESC NULLS LAST LIMIT 20000",
        parameters,
    )
    suspicious_pattern = r"error|fail|cancel|constraint|blocked|invalid|wrong|unsuccess|hardware"
    suspicious = signals[signals["signal_value"].str.contains(suspicious_pattern, case=False, na=False)]
    left, right = st.columns(2)
    left.metric("Seat/profile signals", f"{len(signals):,}")
    right.metric("Potential failure signals", f"{len(suspicious):,}")
    if not suspicious.empty:
        st.subheader("Potential failures and constraints")
        st.dataframe(suspicious.drop(columns=["table_name"]), width="stretch", hide_index=True)
        _event_drilldown(workspace, suspicious, "seat-failure-drilldown")

    distribution = (
        signals.groupby(["path", "signal", "signal_value"], dropna=False)
        .size()
        .reset_index(name="occurrences")
        .sort_values(["path", "signal", "occurrences"], ascending=[True, True, False])
    )
    st.subheader("All observed seat/profile values")
    st.dataframe(distribution, width="stretch", hide_index=True)
    st.subheader("Seat/profile signal timeline")
    st.dataframe(signals.drop(columns=["table_name"]), width="stretch", hide_index=True)
    _event_drilldown(workspace, signals, "seat-signal-drilldown")
    _download(signals.drop(columns=["table_name"]), "seat-investigation.csv")


def _correlate(workspace: Path, archive_id: str) -> None:
    latest = _scalar(workspace, "SELECT max(event_time) FROM archive_events WHERE archive_id = ?", [archive_id])
    if latest is None:
        st.info("No timestamp fields detected in parsed datasets.")
        return
    default_anchor = latest.isoformat() if hasattr(latest, "isoformat") else str(latest)
    anchor_text = st.text_input("Anchor time (UTC, ISO 8601)", default_anchor)
    window_minutes = st.number_input("Window before and after anchor (minutes)", 1, 10_080, 30)
    available = _query(
        workspace, "SELECT DISTINCT domain FROM archive_events WHERE archive_id = ? ORDER BY domain", [archive_id]
    )["domain"].tolist()
    domains = st.multiselect("Domains", available, default=available)
    try:
        anchor = datetime.fromisoformat(anchor_text.replace("Z", "+00:00"))
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
    except ValueError:
        st.error("Anchor must be valid ISO 8601 time.")
        return
    parameters: list[object] = [archive_id, anchor, int(window_minutes), anchor, int(window_minutes)]
    domain_clause = _where_domains(domains, parameters)
    events = _query(
        workspace,
        f"""
        SELECT event_time, domain, path, timestamp_column, timestamp_value, table_name, row_number
        FROM archive_events
        WHERE archive_id = ?
          AND event_time BETWEEN ? - (? * INTERVAL '1 minute') AND ? + (? * INTERVAL '1 minute')
          {domain_clause}
        ORDER BY event_time
        LIMIT 10000
        """,
        parameters,
    )
    if events.empty:
        st.info("No events found in selected window.")
        return
    counts = events.groupby("domain", dropna=False).size().reset_index(name="events")
    st.bar_chart(counts, x="domain", y="events")
    st.dataframe(events.drop(columns=["table_name"]), width="stretch", hide_index=True)
    _event_drilldown(workspace, events, "correlation-drilldown")


def _map(workspace: Path, archive_id: str) -> None:
    columns = _query(
        workspace,
        "SELECT path, table_name, column_name FROM archive_columns WHERE archive_id = ? ORDER BY path, column_index",
        [archive_id],
    )
    candidates: list[tuple[str, str, str, str]] = []
    for (path, table_name), group in columns.groupby(["path", "table_name"]):
        names = group["column_name"].tolist()
        latitude = next((name for name in names if "latitude" in name.lower() or name.lower() == "lat"), None)
        longitude = next(
            (name for name in names if "longitude" in name.lower() or name.lower() in {"lon", "lng"}), None
        )
        if latitude and longitude:
            candidates.append((str(path), str(table_name), str(latitude), str(longitude)))
    if not candidates:
        st.info("No table containing both latitude and longitude fields was detected.")
        return
    selected = st.selectbox("Location dataset", range(len(candidates)), format_func=lambda index: candidates[index][0])
    path, table_name, latitude, longitude = candidates[selected]
    table = quote_identifier(table_name)
    lat = quote_identifier(latitude)
    lon = quote_identifier(longitude)
    points = _query(
        workspace,
        f"""
        SELECT try_cast({lat} AS DOUBLE) AS latitude, try_cast({lon} AS DOUBLE) AS longitude,
               {quote_identifier("__archive_explorer_row")} AS row_number
        FROM {table}
        WHERE try_cast({lat} AS DOUBLE) BETWEEN -90 AND 90
          AND try_cast({lon} AS DOUBLE) BETWEEN -180 AND 180
        LIMIT 50000
        """,
    )
    st.caption(f"{path} · {len(points):,} mapped rows")
    if points.empty:
        st.info("Coordinate fields exist, but no valid numeric coordinate pairs were found.")
        return
    st.map(points, latitude="latitude", longitude="longitude")
    st.dataframe(_redact(points), width="stretch", hide_index=True)


def _valid_read_query(sql: str) -> bool:
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        return False
    return bool(re.match(r"^(SELECT|WITH|SHOW|DESCRIBE|SUMMARIZE)\b", stripped, re.IGNORECASE))


def _files_and_sql(workspace: Path, archive_id: str) -> None:
    files = _query(
        workspace,
        """
        SELECT path, file_format, domain, status, row_count, table_name, error
        FROM archive_files WHERE archive_id = ? ORDER BY path
        """,
        [archive_id],
    )
    if files.empty:
        st.info("Archive contains no files.")
        return
    selected_index = st.selectbox(
        "Archive file",
        range(len(files)),
        format_func=lambda index: f"{files.iloc[index]['path']} · {files.iloc[index]['status']}",
    )
    selected = files.iloc[selected_index]
    st.dataframe(pd.DataFrame([selected.drop(labels=["table_name"])]), width="stretch", hide_index=True)
    table_name = selected["table_name"]
    if pd.isna(table_name):
        if selected["file_format"] in {"text", "xml"}:
            root = Path(
                str(_scalar(workspace, "SELECT extracted_path FROM archives WHERE archive_id = ?", [archive_id]))
            ).resolve()
            target = (root / str(selected["path"])).resolve()
            if target.is_relative_to(root):
                preview = target.read_text(encoding="utf-8", errors="replace")[:200_000]
                st.code(preview)
        else:
            st.info("File inventoried but not tabular. Original remains available in preserved ZIP.")
        return

    schema = _query(
        workspace,
        """
        SELECT column_name, column_type, is_timestamp, domain
        FROM archive_columns WHERE archive_id = ? AND table_name = ? ORDER BY column_index
        """,
        [archive_id, table_name],
    )
    st.subheader("Schema")
    st.dataframe(schema, width="stretch", hide_index=True)
    page_size = st.select_slider("Rows per page", [50, 100, 250, 500, 1000], value=250)
    rows = int(selected["row_count"] or 0)
    pages = max(1, (rows + page_size - 1) // page_size)
    page = st.number_input("Page", 1, pages, 1)
    table = quote_identifier(str(table_name))
    frame = _query(workspace, f"SELECT * FROM {table} LIMIT ? OFFSET ?", [page_size, (page - 1) * page_size])
    st.dataframe(frame, width="stretch", hide_index=True)
    _download(frame, f"{Path(str(selected['path'])).stem}-page-{page}.csv")

    with st.expander("Read-only SQL"):
        sql = st.text_area("Query", f"SELECT * FROM {table} LIMIT 100")
        if st.button("Run SQL"):
            if not _valid_read_query(sql):
                st.error("Only one read-only SELECT, WITH, SHOW, DESCRIBE, or SUMMARIZE statement is allowed.")
            else:
                try:
                    result = _query(workspace, sql)
                    st.dataframe(result, width="stretch", hide_index=True)
                    _download(result, "query-results.csv")
                except duckdb.Error as exc:
                    st.error(str(exc))


def _compare(workspace: Path, archives: pd.DataFrame) -> None:
    if len(archives) < 2:
        st.info("Import another archive to compare coverage and schemas.")
        return
    options = archives["archive_id"].tolist()
    labels = dict(zip(archives["archive_id"], archives["label"]))
    left, right = st.columns(2)
    first = left.selectbox("Archive A", options, format_func=labels.get, key="compare-a")
    second = right.selectbox("Archive B", options, index=1, format_func=labels.get, key="compare-b")
    comparison = _query(
        workspace,
        """
        WITH a AS (
            SELECT f.path, f.file_format, f.row_count, f.status,
                   string_agg(c.column_name || ':' || c.column_type, ',' ORDER BY c.column_index) AS schema
            FROM archive_files f LEFT JOIN archive_columns c USING (archive_id, path)
            WHERE f.archive_id = ? GROUP BY f.path, f.file_format, f.row_count, f.status
        ), b AS (
            SELECT f.path, f.file_format, f.row_count, f.status,
                   string_agg(c.column_name || ':' || c.column_type, ',' ORDER BY c.column_index) AS schema
            FROM archive_files f LEFT JOIN archive_columns c USING (archive_id, path)
            WHERE f.archive_id = ? GROUP BY f.path, f.file_format, f.row_count, f.status
        )
        SELECT coalesce(a.path, b.path) AS path,
               CASE
                   WHEN a.path IS NULL THEN 'added in B'
                   WHEN b.path IS NULL THEN 'removed in B'
                   WHEN a.schema IS DISTINCT FROM b.schema THEN 'schema changed'
                   WHEN a.row_count IS DISTINCT FROM b.row_count THEN 'row count changed'
                   WHEN a.status IS DISTINCT FROM b.status THEN 'parse status changed'
                   ELSE 'same'
               END AS change,
               a.row_count AS rows_a, b.row_count AS rows_b,
               a.file_format AS format_a, b.file_format AS format_b,
               a.schema AS schema_a, b.schema AS schema_b
        FROM a FULL OUTER JOIN b USING (path)
        ORDER BY change, path
        """,
        [first, second],
    )
    changed_only = st.checkbox("Hide unchanged datasets", value=True)
    displayed = comparison[comparison["change"] != "same"] if changed_only else comparison
    st.dataframe(displayed, width="stretch", hide_index=True)


def main() -> None:
    args = _arguments()
    workspace = initialize_workspace(args.workspace)
    st.set_page_config(page_title="BMW CarData Archive Explorer", page_icon="🚙", layout="wide")
    st.title("BMW CarData Archive Explorer")
    st.caption("Local, read-only exploration of preserved BMW CarData archive contents")

    archives = _query(
        workspace,
        """
        SELECT archive_id, filename, imported_at,
               filename || ' · ' || cast(imported_at AS VARCHAR) || ' · ' || archive_id AS label
        FROM archives ORDER BY imported_at DESC
        """,
    )
    page = st.sidebar.radio("Explore", PAGES)
    st.sidebar.caption(f"Workspace: {workspace}")
    if archives.empty:
        _render_upload(workspace)
        return

    labels = dict(zip(archives["archive_id"], archives["label"]))
    archive_id = st.sidebar.selectbox("Archive", archives["archive_id"].tolist(), format_func=labels.get)

    if page == "Overview":
        _render_upload(workspace)
        _overview(workspace, archive_id)
    elif page == "Catalogue":
        _catalogue(workspace, archive_id)
    elif page == "Timeline":
        _timeline(workspace, archive_id)
    elif page == "Domains":
        _domains(workspace, archive_id)
    elif page == "Seat investigation":
        _seat_investigation(workspace, archive_id)
    elif page == "Correlate":
        _correlate(workspace, archive_id)
    elif page == "Map":
        _map(workspace, archive_id)
    elif page == "Files & SQL":
        _files_and_sql(workspace, archive_id)
    else:
        _compare(workspace, archives)

    st.sidebar.warning("Private vehicle data. Keep app bound to localhost; review raw exports before sharing.")


main()
