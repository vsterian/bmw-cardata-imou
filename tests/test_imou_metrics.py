import aiohttp
import pytest
from test_automation import make_settings
from test_metrics import _values

from app.imou import ImouClient
from app.metrics import Metrics


@pytest.mark.asyncio
async def test_dry_run_action_exports_success_without_identifiers(tmp_path):
    path = tmp_path / "metrics.prom"
    metrics = Metrics(path)
    async with aiohttp.ClientSession() as session:
        result = await ImouClient(session, make_settings(tmp_path), metrics).trigger("Car")

    assert result["dry_run"] is True
    published = _values(path)
    assert published["imou_action_success_total"] == 1
    assert published["imou_last_action_success"] == 1
    content = path.read_text(encoding="utf-8")
    assert "WBA12345678901234" not in content
    assert "device" not in content.lower()
