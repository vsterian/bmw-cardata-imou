"""BMW CarData device-flow token refresh and MQTT stream client."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import aiohttp
import paho.mqtt.client as mqtt

from .config import Settings

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = "https://customer.bmwgroup.com/gcdm/oauth/token"


class BMWError(RuntimeError):
    """Base BMW integration error."""


class BMWAuthError(BMWError):
    """BMW token refresh failed."""


class BMWStreamError(BMWError):
    """BMW MQTT stream failed."""


class TokenStore:
    """Persist BMW token rotation while allowing initial values from `.env`."""

    def __init__(self, settings: Settings) -> None:
        self.path = settings.bmw_token_file
        self.seed = {
            "client_id": settings.bmw_client_id,
            "gcid": settings.bmw_gcid,
            "refresh_token": settings.bmw_refresh_token,
            "id_token": settings.bmw_id_token,
            "scope": settings.bmw_scope,
        }

    def load(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Ignoring invalid BMW token file %s: %s", self.path, exc)
        for key, value in self.seed.items():
            if value and not data.get(key):
                data[key] = value
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self.path.chmod(0o600)


class BMWAuthenticator:
    """Refresh BMW OAuth tokens using refresh-token grant."""

    def __init__(self, session: aiohttp.ClientSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.store = TokenStore(settings)
        self.tokens = self.store.load()

    async def refresh(self) -> dict[str, Any]:
        refresh_token = self.tokens.get("refresh_token")
        client_id = self.tokens.get("client_id", self.settings.bmw_client_id)
        if not refresh_token or not client_id:
            raise BMWAuthError("BMW token state lacks client_id or refresh_token")

        payload = {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if self.settings.bmw_scope:
            payload["scope"] = self.settings.bmw_scope

        try:
            async with self.session.post(
                TOKEN_URL,
                data=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                body = await response.text()
                if response.status != 200:
                    raise BMWAuthError(f"BMW token refresh failed ({response.status})")
                try:
                    refreshed = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise BMWAuthError("BMW token refresh returned invalid JSON") from exc
        except TimeoutError as exc:
            raise BMWAuthError("BMW token refresh timed out") from exc
        except aiohttp.ClientError as exc:
            raise BMWAuthError(f"BMW token refresh network error: {exc}") from exc

        if not isinstance(refreshed, dict) or not refreshed.get("id_token"):
            raise BMWAuthError("BMW token refresh response lacks id_token")

        self.tokens.update(refreshed)
        self.tokens["client_id"] = client_id
        self.tokens["received_at"] = time.time()
        self.store.save(self.tokens)
        _LOGGER.info("BMW token refreshed")
        return self.tokens


class BMWStream:
    """Reconnectable MQTT stream with token refresh and async message delivery."""

    def __init__(self, session: aiohttp.ClientSession, settings: Settings) -> None:
        self.settings = settings
        self.authenticator = BMWAuthenticator(session, settings)
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.client: mqtt.Client | None = None
        self.connected = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.stop_requested = False

    def _topic(self) -> str:
        return f"{self.settings.bmw_gcid}/{self.settings.bmw_vin}/#"

    def _build_client(self, id_token: str) -> mqtt.Client:
        client_id = self.settings.bmw_mqtt_client_id or f"bmw-cardata-imou-{uuid4().hex}"
        client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            transport="tcp",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        client.user_data_set({"topic": self._topic()})
        client.username_pw_set(self.settings.bmw_gcid, id_token)
        tls_context = ssl.create_default_context()
        if not hasattr(ssl, "TLSVersion") or not hasattr(ssl.TLSVersion, "TLSv1_3"):
            raise BMWStreamError("BMW CarData MQTT requires TLS 1.3 support")
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_3
        client.tls_set_context(tls_context)
        client.tls_insecure_set(False)
        client.reconnect_delay_set(min_delay=5, max_delay=60)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        return client

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None
    ) -> None:
        if reason_code != 0:
            _LOGGER.error("BMW MQTT connection rejected: %s", reason_code)
            self.loop.call_soon_threadsafe(self.disconnected.set)
            return
        topic = userdata.get("topic") if isinstance(userdata, dict) else self._topic()
        result, _ = client.subscribe(topic)
        if result != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.error("BMW MQTT subscribe failed: %s", mqtt.error_string(result))
            self.loop.call_soon_threadsafe(self.disconnected.set)
            return
        _LOGGER.info("BMW MQTT connected; subscribed to configured VIN")
        self.loop.call_soon_threadsafe(self.connected.set)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, *args: Any, **kwargs: Any) -> None:
        self.loop.call_soon_threadsafe(self.disconnected.set)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _LOGGER.warning("Ignoring invalid BMW MQTT JSON payload")
            return
        if not isinstance(payload, dict):
            return
        self.loop.call_soon_threadsafe(self._enqueue_payload, payload)

    def _enqueue_payload(self, payload: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            _LOGGER.warning("BMW MQTT queue full; dropping payload")

    async def _connect(self, id_token: str) -> None:
        self.connected.clear()
        self.disconnected.clear()
        self.client = self._build_client(id_token)
        try:
            await asyncio.to_thread(
                self.client.connect,
                self.settings.bmw_stream_host,
                self.settings.bmw_stream_port,
                self.settings.bmw_keepalive_seconds,
            )
            self.client.loop_start()
            await asyncio.wait_for(self.connected.wait(), timeout=30)
        except Exception as exc:
            await self._disconnect()
            raise BMWStreamError(f"BMW MQTT connection failed: {exc}") from exc

    async def _disconnect(self) -> None:
        client = self.client
        self.client = None
        self.connected.clear()
        if client is None:
            return
        try:
            await asyncio.to_thread(client.disconnect)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("BMW MQTT disconnect raised: %s", exc)
        try:
            await asyncio.to_thread(client.loop_stop)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("BMW MQTT loop stop raised: %s", exc)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield stream payloads forever, reconnecting after token expiry or failure."""

        while not self.stop_requested:
            try:
                tokens = await self.authenticator.refresh()
                await self._connect(tokens["id_token"])
                refresh_deadline = time.monotonic() + self.settings.bmw_refresh_interval_seconds
                while not self.stop_requested and time.monotonic() < refresh_deadline:
                    if self.disconnected.is_set():
                        raise BMWStreamError("BMW MQTT disconnected")
                    timeout = min(1.0, max(0.1, refresh_deadline - time.monotonic()))
                    try:
                        yield await asyncio.wait_for(self.queue.get(), timeout=timeout)
                    except TimeoutError:
                        continue
            except BMWAuthError:
                _LOGGER.exception("BMW authentication failed; retrying")
                await asyncio.sleep(self.settings.bmw_reconnect_delay_seconds)
            except BMWStreamError as exc:
                _LOGGER.warning("%s; reconnecting", exc)
                await asyncio.sleep(self.settings.bmw_reconnect_delay_seconds)
            finally:
                await self._disconnect()

    async def stop(self) -> None:
        self.stop_requested = True
        await self._disconnect()
