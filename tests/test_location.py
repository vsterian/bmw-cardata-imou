from app.location import (
    LATITUDE_DESCRIPTOR,
    LONGITUDE_DESCRIPTOR,
    LocationExtractor,
    haversine_meters,
    is_inside_target,
)

VIN = "WBA12345678901234"


def descriptor(value, timestamp="2026-09-09T00:00:00Z"):
    return {"value": value, "unit": "degrees", "timestamp": timestamp}


def test_extracts_paired_coordinates():
    extractor = LocationExtractor()
    assert extractor.extract({"vin": VIN, "data": {LATITUDE_DESCRIPTOR: descriptor(44.4)}}, expected_vin=VIN) is None
    location = extractor.extract(
        {"vin": VIN, "data": {LONGITUDE_DESCRIPTOR: descriptor(26.1)}},
        expected_vin=VIN,
    )
    assert location is not None
    assert location.latitude == 44.4
    assert location.longitude == 26.1


def test_ignores_other_vin():
    extractor = LocationExtractor()
    payload = {"vin": "WBA99999999999999", "data": {LATITUDE_DESCRIPTOR: descriptor(44.4)}}
    assert extractor.extract(payload, expected_vin=VIN) is None


def test_rejects_invalid_coordinates():
    extractor = LocationExtractor()
    payload = {"vin": VIN, "data": {LATITUDE_DESCRIPTOR: descriptor(91)}}
    assert extractor.extract(payload, expected_vin=VIN) is None


def test_geofence_distance_and_membership():
    distance = haversine_meters(44.4268, 26.1025, 44.4268, 26.1025)
    assert distance == 0
    location = LocationExtractor().extract(
        {
            "vin": VIN,
            "data": {
                LATITUDE_DESCRIPTOR: descriptor(44.4268),
                LONGITUDE_DESCRIPTOR: descriptor(26.1025),
            },
        },
        expected_vin=VIN,
    )
    assert location is not None
    assert is_inside_target(location, 44.4268, 26.1025, 10)
