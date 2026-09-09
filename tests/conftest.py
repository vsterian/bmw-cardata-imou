import pytest


@pytest.fixture
def settings_env(monkeypatch):
    values = {
        "BMW_CLIENT_ID": "client-id",
        "BMW_GCID": "gcid",
        "BMW_REFRESH_TOKEN": "refresh-token",
        "BMW_VIN": "WBA12345678901234",
        "TARGET_LATITUDE": "44.4268",
        "TARGET_LONGITUDE": "26.1025",
        "IMOU_DRY_RUN": "true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
