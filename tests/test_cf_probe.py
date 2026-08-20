import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


def _load_cf_probe_module():
    path = Path(__file__).resolve().parents[1] / "apps" / "litefupzl" / "cf_probe.py"
    spec = importlib.util.spec_from_file_location("litefupzl_cf_probe", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_cf_probe_does_not_inject_configured_login_cookie(monkeypatch, tmp_path):
    module = _load_cf_probe_module()
    target_url = "https://linux.do/challenge"
    config = SimpleNamespace(
        cookies=["_t=redacted"],
        output_dir=str(tmp_path),
        browser_name="chromium",
        headless=True,
        virtual_display=False,
        proxy_server=None,
    )
    context = SimpleNamespace(
        add_cookies=AsyncMock(side_effect=AssertionError("probe must not inject cookies")),
        cookies=AsyncMock(return_value=[]),
        close=AsyncMock(),
    )
    page = SimpleNamespace(
        on=Mock(),
        goto=AsyncMock(return_value=SimpleNamespace(status=404)),
        wait_for_timeout=AsyncMock(),
    )
    controller = SimpleNamespace(stop=AsyncMock())

    monkeypatch.setattr(module, "load_cf_probe_env", lambda: config)
    monkeypatch.setattr(
        module,
        "_create_browser_context",
        AsyncMock(return_value=(controller, context, page, None)),
    )
    monkeypatch.setattr(
        module,
        "_snapshot",
        AsyncMock(return_value={"challenge": {"active": False}}),
    )
    monkeypatch.setattr(module, "handle_cf_challenge", AsyncMock())
    monkeypatch.setattr(module, "_probe_passed", lambda *_args: True)
    monkeypatch.setenv("LITEFUPZL_CF_PROBE_URL", target_url)
    monkeypatch.setenv("LITEFUPZL_CF_PROBE_TIMEOUT_SECONDS", "1")

    exit_code = await module.run_probe()

    result = json.loads((tmp_path / "cf_probe.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    context.add_cookies.assert_not_awaited()
    context.cookies.assert_awaited_once_with([target_url])
    assert result["initial_cookie_names"] == []
    assert "_t" not in result["initial_cookie_names"]
