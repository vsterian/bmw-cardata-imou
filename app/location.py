"""Extract paired GPS coordinates from BMW CarData payloads."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

LATITUDE_DESCRIPTOR = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LONGITUDE_DESCRIPTOR = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
HEADING_DESCRIPTOR = "vehicle.cabin.infotainment.navigation.currentLocation.heading"


@dataclass(frozen=True)
class Location:
    """Vehicle location with optional BMW timestamp and heading."""

    latitude: float
    longitude: float
    timestamp: str
    heading: float | None = None


@dataclass
class _Coordinate:
    value: float
    received_at: float
    timestamp: str | None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class LocationExtractor:
    """Pair separate latitude and longitude descriptor messages."""

    def __init__(self, pair_window_seconds: float = 60.0) -> None:
        self.pair_window_seconds = pair_window_seconds
        self._coordinates: dict[str, dict[str, _Coordinate]] = {}

    def extract(self, payload: dict[str, Any], *, expected_vin: str) -> Location | None:
        vin = payload.get("vin")
        if vin != expected_vin:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        now = datetime.now(timezone.utc).timestamp()
        values = self._coordinates.setdefault(vin, {})
        for descriptor in (LATITUDE_DESCRIPTOR, LONGITUDE_DESCRIPTOR):
            descriptor_data = data.get(descriptor)
            if not isinstance(descriptor_data, dict):
                continue
            value = _number(descriptor_data.get("value"))
            if value is None:
                continue
            if descriptor == LATITUDE_DESCRIPTOR and not -90 <= value <= 90:
                continue
            if descriptor == LONGITUDE_DESCRIPTOR and not -180 <= value <= 180:
                continue
            values[descriptor] = _Coordinate(
                value=value,
                received_at=now,
                timestamp=descriptor_data.get("timestamp"),
            )

        latitude = values.get(LATITUDE_DESCRIPTOR)
        longitude = values.get(LONGITUDE_DESCRIPTOR)
        if latitude is None or longitude is None:
            return None
        if (
            now - latitude.received_at > self.pair_window_seconds
            or now - longitude.received_at > self.pair_window_seconds
        ):
            return None

        heading_data = data.get(HEADING_DESCRIPTOR)
        heading = _number(heading_data.get("value")) if isinstance(heading_data, dict) else None
        timestamp = latitude.timestamp or longitude.timestamp or datetime.now(timezone.utc).isoformat()
        return Location(
            latitude=latitude.value,
            longitude=longitude.value,
            timestamp=timestamp,
            heading=heading,
        )


def haversine_meters(latitude: float, longitude: float, target_latitude: float, target_longitude: float) -> float:
    """Return great-circle distance between two WGS84 coordinates."""

    radius = 6_371_000.0
    lat1, lat2 = math.radians(latitude), math.radians(target_latitude)
    delta_lat = math.radians(target_latitude - latitude)
    delta_lon = math.radians(target_longitude - longitude)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_inside_target(location: Location, target_latitude: float, target_longitude: float, radius_meters: float) -> bool:
    """Return whether vehicle is inside target geofence."""

    return (
        haversine_meters(
            location.latitude,
            location.longitude,
            target_latitude,
            target_longitude,
        )
        <= radius_meters
    )
