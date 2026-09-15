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
from .metrics import Metrics
from .state import StateStore


async def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    metrics = Metrics()
    metrics.update(service_up=1)
    async with aiohttp.ClientSession() as session:
        camera = ImouClient(session, settings, metrics)
        automation = LocationAutomation(
            settings,
            StateStore(settings.bmw_token_file.parent / "state.json"),
            camera,
            metrics,
        )
        extractor = LocationExtractor(settings.location_pair_window_seconds)
        stream = BMWStream(session, settings, metrics)
        try:
            async for payload in stream.messages():
                location = extractor.extract(payload, expected_vin=settings.bmw_vin)
                if location is None:
                    continue
                metrics.increment("bmw_location_updates_total")
                try:
                    started = asyncio.get_running_loop().time()
                    await automation.process(location)
                    metrics.success(asyncio.get_running_loop().time() - started)
                except Exception:
                    metrics.failure(asyncio.get_running_loop().time() - started)
                    logging.getLogger(__name__).exception("Location transition handling failed")
        finally:
            metrics.set("service_up", 0)
            await stream.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped")


if __name__ == "__main__":
    main()
