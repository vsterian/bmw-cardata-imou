"""Target-zone transition handling."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol

from .config import Settings
from .imou import ImouClient
from .location import Location, is_inside_target
from .state import StateStore

_LOGGER = logging.getLogger(__name__)


class Camera(Protocol):
    async def trigger(self, action: str) -> dict: ...


class LocationAutomation:
    """Turn geofence enter/leave events into Imou actions."""

    def __init__(self, settings: Settings, state_store: StateStore, camera: Camera | ImouClient) -> None:
        self.settings = settings
        self.state_store = state_store
        self.state = state_store.load()
        self.camera = camera

    async def process(self, location: Location) -> str:
        current = is_inside_target(
            location,
            self.settings.target_latitude,
            self.settings.target_longitude,
            self.settings.target_radius_meters,
        )
        previous = self.state.in_target
        action: str | None = None
        transition = "stationary"

        if previous is None:
            transition = "arrived" if current else "baseline"
            if current:
                action = "Car"
        elif previous != current:
            transition = "arrived" if current else "departed"
            action = "Car" if current else "ZoomOut"

        if action:
            await self.camera.trigger(action)
            self.state.last_action = action
            self.state.last_action_at = datetime.now(timezone.utc).isoformat()
            _LOGGER.info("Vehicle %s target %s; Imou action=%s", transition, self.settings.target_name, action)
        else:
            _LOGGER.debug("Vehicle %s target %s", transition, self.settings.target_name)

        self.state.in_target = current
        self.state.last_location = self.state_store.location_payload(
            location.latitude,
            location.longitude,
            location.timestamp,
        )
        self.state_store.save(self.state)
        return transition
