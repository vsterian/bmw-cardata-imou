import asyncio

import aiohttp

from app.bmw import BMWStream
from app.config import ConfigurationError, Settings
from test_automation import make_settings


def test_stream_disables_library_auto_reconnect(tmp_path):
    async def check():
        async with aiohttp.ClientSession() as session:
            client = BMWStream(session, make_settings(tmp_path))._build_client("id-token")
            assert client._reconnect_on_failure is False

    asyncio.run(check())


def test_bmw_reconnect_delay_has_safe_minimum(settings_env, monkeypatch):
    monkeypatch.setenv("BMW_RECONNECT_DELAY_SECONDS", "59")

    try:
        Settings.from_env()
    except ConfigurationError as exc:
        assert "at least 60 seconds" in str(exc)
    else:
        raise AssertionError("Expected reconnect delay validation failure")
