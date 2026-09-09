"""Imou OpenAPI camera control client."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

import aiohttp

from .config import Settings

_LOGGER = logging.getLogger(__name__)


class ImouError(RuntimeError):
    """Imou API request failed."""


class ImouClient:
    """Send PTZ/scene actions to one Imou camera."""

    def __init__(self, session: aiohttp.ClientSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _signature(self, timestamp: int, nonce: str) -> str:
        source = f"time:{timestamp},nonce:{nonce},appSecret:{self.settings.imou_app_secret}"
        return hashlib.md5(source.encode("utf-8")).hexdigest()

    def _system(self) -> dict[str, Any]:
        timestamp = int(time.time())
        nonce = str(uuid.uuid4())
        return {
            "ver": "1.0",
            "appId": self.settings.imou_app_id,
            "sign": self._signature(timestamp, nonce),
            "time": timestamp,
            "nonce": nonce,
        }

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.imou_base_url}/{endpoint.lstrip('/')}"
        try:
            async with self.session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.settings.imou_timeout_seconds),
            ) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    raise ImouError(f"Imou {endpoint} returned HTTP {response.status}")
        except aiohttp.ClientResponseError as exc:
            raise ImouError(f"Imou {endpoint} request failed: {exc}") from exc
        except aiohttp.ClientError as exc:
            raise ImouError(f"Imou {endpoint} network error: {exc}") from exc

        if not isinstance(body, dict):
            raise ImouError(f"Imou {endpoint} returned invalid JSON")
        result = body.get("result")
        if isinstance(result, dict):
            code = result.get("code")
            if code not in (None, 0, "0"):
                raise ImouError(f"Imou {endpoint} returned API code {code}")
        return body

    async def access_token(self) -> str:
        response = await self._post(
            "accessToken",
            {
                "id": str(uuid.uuid4()),
                "system": self._system(),
                "params": {},
            },
        )
        try:
            return response["result"]["data"]["accessToken"]
        except (KeyError, TypeError) as exc:
            raise ImouError("Imou accessToken response lacks access token") from exc

    async def trigger(self, action: str) -> dict[str, Any]:
        """Execute camera action, preserving old action names."""

        valid_actions = {"ZoomOut", "Car", "ZoomIn", "Right", "Up", "Down"}
        if action not in valid_actions:
            raise ImouError(f"Unsupported Imou action: {action}")
        if self.settings.imou_dry_run:
            _LOGGER.warning("IMOU_DRY_RUN=true; would execute camera action %s", action)
            return {"dry_run": True, "action": action}

        token = await self.access_token()
        return await self._post(
            "turnCollection",
            {
                "id": str(uuid.uuid4()),
                "system": self._system(),
                "params": {
                    "token": token,
                    "deviceId": self.settings.imou_device_id,
                    "channelId": self.settings.imou_channel_id,
                    "name": action,
                },
            },
        )
