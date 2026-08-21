from types import SimpleNamespace

import pytest

from litefupzl.oneshot.models import SlotConfig, SlotResult, SlotStatus, WarningCode, utc_now_iso


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


@pytest.mark.asyncio
async def test_enabled_cookie_refresh_requires_admin_token_in_actions(monkeypatch):
    from litefupzl.oneshot import session

    monkeypatch.delenv("LITEFUPZL_ACTIONS_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("FUCKPZL_ACTIONS_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    config = SimpleNamespace(cookie_refresh_enabled=True, cookies=["_t=old"], browser_name="chromium")
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=old", duration_minutes=1)

    with pytest.raises(session.CookieRefreshError, match="COOKIE_REFRESH_CONFIG_MISSING"):
        await session._maybe_refresh_cookie_secret(object(), slot, config)


@pytest.mark.asyncio
async def test_enabled_cookie_refresh_without_github_context_is_a_local_noop(monkeypatch):
    from litefupzl.oneshot import session

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("LITEFUPZL_ACTIONS_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("FUCKPZL_ACTIONS_ADMIN_TOKEN", raising=False)

    config = SimpleNamespace(cookie_refresh_enabled=True, cookies=["_t=old"], browser_name="chromium")
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=old", duration_minutes=1)

    assert await session._maybe_refresh_cookie_secret(object(), slot, config) is False


def test_cookie_refresh_failure_escalates_successful_slot_to_warning():
    from litefupzl.oneshot.session import _mark_cookie_refresh_failure

    result = SlotResult(
        slot_index=1,
        slot_alias="slot-001",
        started_at=utc_now_iso(),
        status=SlotStatus.SUCCESS,
    )

    _mark_cookie_refresh_failure(result)

    assert result.status is SlotStatus.WARNING
    assert result.warning_codes == [WarningCode.COOKIE_REFRESH_FAILED.value]


@pytest.mark.asyncio
async def test_final_cookie_refresh_updates_slot_source(monkeypatch):
    from litefupzl.oneshot import session

    class Recorder:
        def __init__(self):
            self.events = []

        def emit(self, slot, step, status, **kwargs):
            self.events.append((slot, step, status, kwargs))

    config = SimpleNamespace(cookie_refresh_enabled=True, cookies=["_t=old"])
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=old", duration_minutes=1)
    result = SlotResult(slot_index=1, slot_alias="slot-001", started_at=utc_now_iso())
    recorder = Recorder()

    async def refreshed(*_args, **_kwargs):
        config.cookies[0] = "_t=new"
        return True

    monkeypatch.setattr(session, "_maybe_refresh_cookie_secret", refreshed)

    assert await session._finalize_cookie_refresh(object(), slot, config, result, recorder) is True
    assert slot.cookie == "_t=new"
    assert result.cookie_refresh_ok is True
    assert any(event[1:3] == ("cookie-refresh", "ok") for event in recorder.events)


@pytest.mark.asyncio
async def test_final_cookie_refresh_failure_marks_slot_warning(monkeypatch):
    from litefupzl.oneshot import session

    class Recorder:
        def __init__(self):
            self.events = []

        def emit(self, slot, step, status, **kwargs):
            self.events.append((slot, step, status, kwargs))

    config = SimpleNamespace(cookie_refresh_enabled=True, cookies=["_t=old"])
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=old", duration_minutes=1)
    result = SlotResult(slot_index=1, slot_alias="slot-001", started_at=utc_now_iso())
    recorder = Recorder()

    async def rejected(*_args, **_kwargs):
        raise session.CookieRefreshError("COOKIE_REFRESH_HTTP_401")

    monkeypatch.setattr(session, "_maybe_refresh_cookie_secret", rejected)

    assert await session._finalize_cookie_refresh(object(), slot, config, result, recorder) is False
    assert WarningCode.COOKIE_REFRESH_FAILED.value in result.warning_codes
    assert any(event[3].get("code") == "COOKIE_REFRESH_HTTP_401" for event in recorder.events)


@pytest.mark.asyncio
async def test_final_cookie_refresh_emits_terminal_marker_when_unchanged(monkeypatch):
    from litefupzl.oneshot import session

    class Recorder:
        def __init__(self):
            self.events = []

        def emit(self, slot, step, status, **kwargs):
            self.events.append((slot, step, status, kwargs))

    config = SimpleNamespace(cookie_refresh_enabled=True, cookies=["_t=current"])
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=current", duration_minutes=1)
    result = SlotResult(slot_index=1, slot_alias="slot-001", started_at=utc_now_iso())
    recorder = Recorder()

    async def unchanged(*_args, **_kwargs):
        return False

    monkeypatch.setattr(session, "_maybe_refresh_cookie_secret", unchanged)

    assert await session._finalize_cookie_refresh(object(), slot, config, result, recorder) is True
    assert any(event[1:3] == ("cookie-refresh", "unchanged") for event in recorder.events)
