"""Application entry point."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from .automation import LocationAutomation
from .bmw import BMWStream
from .config import Settings
from .imou import ImouClient
from .location import LocationExtractor
from .state import StateStore


async def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    async with aiohttp.ClientSession() as session:
        camera = ImouClient(session, settings)
        automation = LocationAutomation(settings, StateStore(settings.bmw_token_file.parent / "state.json"), camera)
        extractor = LocationExtractor(settings.location_pair_window_seconds)
        stream = BMWStream(session, settings)
        try:
            async for payload in stream.messages():
                location = extractor.extract(payload, expected_vin=settings.bmw_vin)
                if location is None:
                    continue
                try:
                    await automation.process(location)
                except Exception:
                    logging.getLogger(__name__).exception("Location transition handling failed")
        finally:
            await stream.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped")


if __name__ == "__main__":
    main()
