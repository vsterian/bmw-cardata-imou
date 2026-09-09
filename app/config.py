"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when required application configuration is missing or invalid."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("replace-with-"):
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _float(name: str, default: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise ConfigurationError(f"Missing required environment variable: {name}")
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment."""

    bmw_client_id: str
    bmw_gcid: str
    bmw_refresh_token: str
    bmw_id_token: str | None
    bmw_scope: str
    bmw_stream_host: str
    bmw_stream_port: int
    bmw_mqtt_client_id: str
    bmw_refresh_interval_seconds: int
    bmw_keepalive_seconds: int
    bmw_reconnect_delay_seconds: int
    bmw_token_file: Path
    bmw_vin: str
    target_name: str
    target_latitude: float
    target_longitude: float
    target_radius_meters: float
    location_pair_window_seconds: float
    imou_base_url: str
    imou_app_id: str
    imou_app_secret: str
    imou_device_id: str
    imou_channel_id: str
    imou_timeout_seconds: float
    imou_dry_run: bool
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        """Build validated settings from process environment."""

        dry_run = _bool("IMOU_DRY_RUN")
        imou_app_id = os.getenv("IMOU_APP_ID", "").strip()
        imou_app_secret = os.getenv("IMOU_APP_SECRET", "").strip()
        imou_device_id = os.getenv("IMOU_DEVICE_ID", "").strip()
        if not dry_run:
            imou_app_id = imou_app_id or _required("IMOU_APP_ID")
            imou_app_secret = imou_app_secret or _required("IMOU_APP_SECRET")
            imou_device_id = imou_device_id or _required("IMOU_DEVICE_ID")

        target_latitude = _float("TARGET_LATITUDE")
        target_longitude = _float("TARGET_LONGITUDE")
        if not -90 <= target_latitude <= 90:
            raise ConfigurationError("TARGET_LATITUDE must be between -90 and 90")
        if not -180 <= target_longitude <= 180:
            raise ConfigurationError("TARGET_LONGITUDE must be between -180 and 180")

        radius = _float("TARGET_RADIUS_METERS", 100.0)
        if radius <= 0:
            raise ConfigurationError("TARGET_RADIUS_METERS must be greater than zero")

        refresh_interval_seconds = _int("BMW_REFRESH_INTERVAL_SECONDS", 2700)
        reconnect_delay_seconds = _int("BMW_RECONNECT_DELAY_SECONDS", 90)
        if refresh_interval_seconds <= 0 or refresh_interval_seconds >= 3600:
            raise ConfigurationError("BMW_REFRESH_INTERVAL_SECONDS must be between 1 and 3599")
        if reconnect_delay_seconds < 60:
            raise ConfigurationError("BMW_RECONNECT_DELAY_SECONDS must be at least 60 seconds")

        return cls(
            bmw_client_id=_required("BMW_CLIENT_ID"),
            bmw_gcid=_required("BMW_GCID"),
            bmw_refresh_token=_required("BMW_REFRESH_TOKEN"),
            bmw_id_token=os.getenv("BMW_ID_TOKEN", "").strip() or None,
            bmw_scope=os.getenv(
                "BMW_SCOPE",
                "authenticate_user openid cardata:api:read cardata:streaming:read",
            ).strip(),
            bmw_stream_host=os.getenv("BMW_STREAM_HOST", "customer.streaming-cardata.bmwgroup.com").strip(),
            bmw_stream_port=_int("BMW_STREAM_PORT", 9000),
            bmw_mqtt_client_id=os.getenv("BMW_MQTT_CLIENT_ID", "bmw-cardata-imou").strip(),
            bmw_refresh_interval_seconds=refresh_interval_seconds,
            bmw_keepalive_seconds=_int("BMW_KEEPALIVE_SECONDS", 30),
            bmw_reconnect_delay_seconds=reconnect_delay_seconds,
            bmw_token_file=Path(os.getenv("BMW_TOKEN_FILE", "/data/bmw-tokens.json")),
            bmw_vin=_required("BMW_VIN").upper(),
            target_name=os.getenv("TARGET_NAME", "target location").strip(),
            target_latitude=target_latitude,
            target_longitude=target_longitude,
            target_radius_meters=radius,
            location_pair_window_seconds=_float("LOCATION_PAIR_WINDOW_SECONDS", 60.0),
            imou_base_url=os.getenv("IMOU_BASE_URL", "https://openapi-fk.easy4ip.com:443/openapi").rstrip("/"),
            imou_app_id=imou_app_id,
            imou_app_secret=imou_app_secret,
            imou_device_id=imou_device_id,
            imou_channel_id=os.getenv("IMOU_CHANNEL_ID", "0").strip(),
            imou_timeout_seconds=_float("IMOU_TIMEOUT_SECONDS", 30.0),
            imou_dry_run=dry_run,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
