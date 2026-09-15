"""Atomic Prometheus textfile metrics for the BMW/Imou service."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

DEFAULT_METRICS_FILE = Path("/metrics/bmw-cardata-imou.prom")
PRODUCT = "bmw-cardata-imou"
COMMON_METRICS = {
    "service_up",
    "last_loop_success_timestamp_seconds",
    "last_dependency_success_timestamp_seconds",
    "consecutive_failures",
    "operations_success_total",
    "operations_failure_total",
    "last_operation_duration_seconds",
}

COUNTERS = {
    "operations_success_total",
    "operations_failure_total",
    "bmw_oauth_refresh_failures_total",
    "bmw_mqtt_reconnects_total",
    "bmw_mqtt_disconnects_total",
    "bmw_mqtt_valid_payloads_total",
    "bmw_mqtt_invalid_payloads_total",
    "bmw_mqtt_queue_drops_total",
    "bmw_location_updates_total",
    "bmw_geofence_transitions_total",
    "imou_action_success_total",
    "imou_action_failure_total",
}


class Metrics:
    """Keep low-cardinality process metrics and atomically publish each change."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.getenv("METRICS_FILE", str(DEFAULT_METRICS_FILE)))
        self._lock = threading.Lock()
        self._last_heartbeat_publish = 0.0
        self._values: dict[str, float] = {
            "service_up": 0,
            "last_loop_success_timestamp_seconds": 0,
            "last_dependency_success_timestamp_seconds": 0,
            "consecutive_failures": 0,
            "operations_success_total": 0,
            "operations_failure_total": 0,
            "last_operation_duration_seconds": 0,
            "bmw_oauth_refresh_success": 0,
            "bmw_oauth_last_refresh_timestamp_seconds": 0,
            "bmw_oauth_refresh_failures_total": 0,
            "bmw_mqtt_connected": 0,
            "bmw_mqtt_reconnects_total": 0,
            "bmw_mqtt_disconnects_total": 0,
            "bmw_mqtt_valid_payloads_total": 0,
            "bmw_mqtt_invalid_payloads_total": 0,
            "bmw_mqtt_queue_drops_total": 0,
            "bmw_location_updates_total": 0,
            "bmw_geofence_state": -1,
            "bmw_geofence_transitions_total": 0,
            "bmw_geofence_last_transition_timestamp_seconds": 0,
            "imou_action_success_total": 0,
            "imou_action_failure_total": 0,
            "imou_last_action_success": -1,
            "imou_last_action_timestamp_seconds": 0,
            "imou_last_action_duration_seconds": 0,
        }

    def publish(self) -> None:
        with self._lock:
            self._write_locked()

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._values[name] = float(value)
            self._write_locked()

    def increment(self, name: str, amount: float = 1) -> None:
        with self._lock:
            self._values[name] = self._values.get(name, 0) + amount
            self._write_locked()

    def update(self, **values: float) -> None:
        with self._lock:
            self._values.update({name: float(value) for name, value in values.items()})
            self._write_locked()

    def success(self, duration: float = 0) -> None:
        now = time.time()
        with self._lock:
            self._values["last_loop_success_timestamp_seconds"] = now
            self._values["consecutive_failures"] = 0
            self._values["operations_success_total"] += 1
            self._values["last_operation_duration_seconds"] = duration
            self._write_locked()

    def heartbeat(self) -> None:
        now_monotonic = time.monotonic()
        with self._lock:
            self._values["last_loop_success_timestamp_seconds"] = time.time()
            if now_monotonic - self._last_heartbeat_publish < 15:
                return
            self._last_heartbeat_publish = now_monotonic
            self._write_locked()

    def failure(self, duration: float = 0) -> None:
        with self._lock:
            self._values["consecutive_failures"] += 1
            self._values["operations_failure_total"] += 1
            self._values["last_operation_duration_seconds"] = duration
            self._write_locked()

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for name, value in sorted(self._values.items()):
            sample = f'{name}{{product="{PRODUCT}"}}' if name in COMMON_METRICS else name
            lines.append(f"# TYPE {name} {'counter' if name in COUNTERS else 'gauge'}\n{sample} {value:g}")
        content = "\n".join(lines) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
