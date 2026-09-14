"""Archive explorer command-line interface."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .ingest import ingest_archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explore BMW CarData archives locally")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Validate and import one archive")
    ingest.add_argument("archive")
    ingest.add_argument("--workspace", default="data/archive-explorer")

    serve = subparsers.add_parser("serve", help="Start local browser explorer")
    serve.add_argument("--workspace", default="data/archive-explorer")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8501)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "ingest":
        result = ingest_archive(args.archive, args.workspace)
        print(json.dumps(asdict(result), indent=2))
        return

    ui = Path(__file__).with_name("ui.py")
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ui),
        f"--server.address={args.host}",
        f"--server.port={args.port}",
        "--server.headless=true",
        "--server.maxUploadSize=512",
        "--browser.gatherUsageStats=false",
        "--",
        "--workspace",
        args.workspace,
    ]
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
