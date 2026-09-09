"""Persistent local automation state."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AutomationState:
    """State needed to detect target-zone transitions across restarts."""

    in_target: bool | None = None
    last_location: dict[str, Any] | None = None
    last_action: str | None = None
    last_action_at: str | None = None


class StateStore:
    """Read and atomically write JSON state."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AutomationState:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return AutomationState()
        except (OSError, json.JSONDecodeError):
            return AutomationState()
        if not isinstance(payload, dict):
            return AutomationState()
        return AutomationState(
            in_target=payload.get("in_target"),
            last_location=payload.get("last_location"),
            last_action=payload.get("last_action"),
            last_action_at=payload.get("last_action_at"),
        )

    def save(self, state: AutomationState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(state), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def location_payload(latitude: float, longitude: float, timestamp: str | None) -> dict[str, Any]:
        return {
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }
