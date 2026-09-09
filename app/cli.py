"""Manual camera command for end-to-end checks."""

from __future__ import annotations

import argparse
import asyncio

import aiohttp

from .config import Settings
from .imou import ImouClient


async def run(action: str) -> None:
    settings = Settings.from_env()
    async with aiohttp.ClientSession() as session:
        response = await ImouClient(session, settings).trigger(action)
        print(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["ZoomOut", "Car", "ZoomIn", "Right", "Up", "Down"])
    args = parser.parse_args()
    asyncio.run(run(args.action))


if __name__ == "__main__":
    main()
