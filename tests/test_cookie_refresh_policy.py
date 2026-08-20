from types import SimpleNamespace

import pytest

from litefupzl.oneshot.models import SlotConfig


@pytest.mark.asyncio
async def test_schedule_does_not_bypass_runtime_cookie_refresh_when_enabled(monkeypatch):
    from litefupzl.oneshot import session

    calls = []

    async def fake_refresh_slot_cookie_secret_from_context(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("LITEFUPZL_ACTIONS_ADMIN_TOKEN", "redacted-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(session, "refresh_slot_cookie_secret_from_context", fake_refresh_slot_cookie_secret_from_context)

    config = SimpleNamespace(cookie_refresh_enabled=True, cookies=["_t=old"], browser_name="chromium")
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=old", duration_minutes=1)

    refreshed = await session._maybe_refresh_cookie_secret(object(), slot, config)

    assert refreshed is True
    assert len(calls) == 1
    assert calls[0]["slot_index"] == 1
    assert calls[0]["repository"] == "owner/repo"
    assert "validate_cookie_string" not in calls[0]


@pytest.mark.asyncio
async def test_cookie_refresh_runtime_still_requires_enabled_flag(monkeypatch):
    from litefupzl.oneshot import session

    async def fail_if_called(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("refresh should not be called when disabled")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("LITEFUPZL_ACTIONS_ADMIN_TOKEN", "redacted-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(session, "refresh_slot_cookie_secret_from_context", fail_if_called)

    config = SimpleNamespace(cookie_refresh_enabled=False, cookies=["_t=old"], browser_name="chromium")
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=old", duration_minutes=1)

    refreshed = await session._maybe_refresh_cookie_secret(object(), slot, config)

    assert refreshed is False
