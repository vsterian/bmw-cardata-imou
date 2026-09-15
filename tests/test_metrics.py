import threading
from pathlib import Path

from app.metrics import Metrics


def _values(path: Path) -> dict[str, float]:
    return {
        line.split()[0]: float(line.split()[1])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def test_metrics_publish_atomically_without_sensitive_labels(tmp_path):
    path = tmp_path / "service.prom"
    metrics = Metrics(path)
    metrics.update(service_up=1, bmw_geofence_state=1)
    metrics.increment("bmw_mqtt_valid_payloads_total")

    content = path.read_text(encoding="utf-8")
    assert 'service_up{product="bmw-cardata-imou"} 1' in content
    assert "bmw_mqtt_valid_payloads_total 1" in content
    assert path.stat().st_mode & 0o777 == 0o644
    assert not list(tmp_path.glob(".service.prom-*"))
    for forbidden in ("vin", "latitude", "longitude", "device_id", "email", "url"):
        assert forbidden not in content.lower()


def test_concurrent_updates_keep_valid_textfile(tmp_path):
    path = tmp_path / "service.prom"
    metrics = Metrics(path)
    threads = [threading.Thread(target=metrics.increment, args=("bmw_mqtt_queue_drops_total",)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert _values(path)["bmw_mqtt_queue_drops_total"] == 8
