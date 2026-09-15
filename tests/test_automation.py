from pathlib import Path

import pytest

from app.automation import LocationAutomation
from app.config import Settings
from app.location import Location
from app.metrics import Metrics
from app.state import StateStore


class FakeCamera:
    def __init__(self):
        self.actions = []

    async def trigger(self, action):
        self.actions.append(action)
        return {"action": action}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        bmw_client_id="client",
        bmw_gcid="gcid",
        bmw_refresh_token="refresh",
        bmw_id_token=None,
        bmw_scope="scope",
        bmw_stream_host="host",
        bmw_stream_port=9000,
        bmw_mqtt_client_id="client",
        bmw_refresh_interval_seconds=2700,
        bmw_keepalive_seconds=120,
        bmw_reconnect_delay_seconds=1,
        bmw_token_file=tmp_path / "tokens.json",
        bmw_vin="WBA12345678901234",
        target_name="target",
        target_latitude=44.4268,
        target_longitude=26.1025,
        target_radius_meters=100,
        location_pair_window_seconds=60,
        imou_base_url="https://example.invalid",
        imou_app_id="app",
        imou_app_secret="secret",
        imou_device_id="device",
        imou_channel_id="0",
        imou_timeout_seconds=30,
        imou_dry_run=True,
        log_level="INFO",
    )


@pytest.mark.asyncio
async def test_enter_leave_actions_are_idempotent(tmp_path):
    settings = make_settings(tmp_path)
    camera = FakeCamera()
    automation = LocationAutomation(settings, StateStore(tmp_path / "state.json"), camera)
    inside = Location(44.4268, 26.1025, "now")
    outside = Location(44.5, 26.2, "later")

    assert await automation.process(inside) == "arrived"
    assert await automation.process(inside) == "stationary"
    assert await automation.process(outside) == "departed"
    assert await automation.process(outside) == "stationary"
    assert camera.actions == ["Car", "ZoomOut"]


@pytest.mark.asyncio
async def test_automation_exports_only_sanitized_geofence_state(tmp_path):
    metrics_path = tmp_path / "metrics.prom"
    metrics = Metrics(metrics_path)
    automation = LocationAutomation(
        make_settings(tmp_path),
        StateStore(tmp_path / "state.json"),
        FakeCamera(),
        metrics,
    )

    assert await automation.process(Location(44.4268, 26.1025, "now")) == "arrived"
    content = metrics_path.read_text(encoding="utf-8")
    assert "bmw_geofence_state 1" in content
    assert "bmw_geofence_transitions_total 1" in content
    assert "44.4268" not in content
    assert "26.1025" not in content
