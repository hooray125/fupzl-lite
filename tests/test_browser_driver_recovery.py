from types import SimpleNamespace

import pytest

from litefupzl.oneshot.models import SlotConfig, SlotResult, SlotStatus, WarningCode


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, slot, step, status, **kwargs):
        self.events.append({"slot": slot, "step": step, "status": status, **kwargs})


class AsyncClosable:
    async def close(self):
        return None

    async def stop(self):
        return None


async def passthrough_prewarm(_context, page, **_kwargs):
    return page, True


@pytest.mark.parametrize(
    "message",
    [
        "Connection closed while reading from the driver",
        "pipe closed by peer",
        "Target page, context or browser has been closed",
    ],
)
def test_browser_driver_failure_recognizes_transport_errors(message):
    from litefupzl.oneshot import session

    assert session._is_browser_driver_failure(RuntimeError(message)) is True


def test_browser_driver_failure_does_not_match_normal_navigation_error():
    from litefupzl.oneshot import session

    assert session._is_browser_driver_failure(RuntimeError("navigation timeout")) is False


@pytest.mark.asyncio
async def test_login_classifier_preserves_browser_driver_crash(monkeypatch):
    from litefupzl.oneshot import session

    page = SimpleNamespace()

    async def crashed(_page):
        raise RuntimeError("Connection closed while reading from the driver")

    monkeypatch.setattr(session, "is_cf_challenge", crashed)

    state = await session._classify_browser_login_state(page)

    assert state == "browser_driver_crashed"
    assert session._browser_driver_failed(page) is True


@pytest.mark.asyncio
async def test_login_proof_detects_driver_crash_hidden_by_username_probe(monkeypatch):
    from litefupzl.oneshot import session

    class Page:
        async def title(self):
            raise RuntimeError("Connection closed while reading from the driver")

    async def no_username(_page):
        return None

    page = Page()
    monkeypatch.setattr(session, "_extract_username", no_username)

    state = await session._require_authenticated_login_proof(
        page,
        [{"name": "_t", "value": "redacted"}],
        "ok",
    )

    assert state == "browser_driver_crashed"
    assert session._browser_driver_failed(page) is True


@pytest.mark.asyncio
async def test_slot_driver_crash_is_runtime_error_not_cookie_invalid(monkeypatch):
    from litefupzl.oneshot import session

    async def fake_create_browser_context(*, temp_profile, config):
        return AsyncClosable(), AsyncClosable(), object(), AsyncClosable()

    async def fake_user_agent(_page):
        return "Mozilla/5.0 (X11; Linux x86_64) Firefox/152.0"

    async def fake_login(*args, **kwargs):
        return "browser_driver_crashed"

    monkeypatch.setattr(session, "_create_browser_context", fake_create_browser_context)
    monkeypatch.setattr(session, "_prewarm_and_replace_login_page", passthrough_prewarm)
    monkeypatch.setattr(session, "attach_topics_timing_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(session, "_get_browser_user_agent", fake_user_agent)
    monkeypatch.setattr(session, "_ensure_logged_in", fake_login)

    config = SimpleNamespace(browser_name="camoufox", cookie_refresh_enabled=False)
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=redacted", duration_minutes=1)
    recorder = Recorder()

    result = await session.run_slot_session(slot, config, recorder)

    assert result.status is SlotStatus.RUNTIME_ERROR
    assert WarningCode.BROWSER_DRIVER_CRASH.value in result.warning_codes
    assert not any(event.get("code") == "COOKIE_INVALID" for event in recorder.events)
    assert any(event.get("code") == WarningCode.BROWSER_DRIVER_CRASH.value for event in recorder.events)


@pytest.mark.asyncio
async def test_driver_crash_after_login_is_still_retryable_runtime_error(monkeypatch):
    from litefupzl.oneshot import session

    async def fake_create_browser_context(*, temp_profile, config):
        return AsyncClosable(), AsyncClosable(), object(), AsyncClosable()

    async def fake_user_agent(_page):
        return "Mozilla/5.0 (X11; Linux x86_64) Firefox/152.0"

    async def fake_login(*args, **kwargs):
        return "ok"

    async def crashed_username(_page):
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(session, "_create_browser_context", fake_create_browser_context)
    monkeypatch.setattr(session, "_prewarm_and_replace_login_page", passthrough_prewarm)
    monkeypatch.setattr(session, "attach_topics_timing_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(session, "_get_browser_user_agent", fake_user_agent)
    monkeypatch.setattr(session, "_ensure_logged_in", fake_login)
    monkeypatch.setattr(session, "_extract_username", crashed_username)

    config = SimpleNamespace(browser_name="camoufox", cookie_refresh_enabled=False)
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=redacted", duration_minutes=1)
    recorder = Recorder()

    result = await session.run_slot_session(slot, config, recorder)

    assert result.status is SlotStatus.RUNTIME_ERROR
    assert WarningCode.BROWSER_DRIVER_CRASH.value in result.warning_codes
    assert not any(event.get("code") == "COOKIE_INVALID" for event in recorder.events)


@pytest.mark.asyncio
async def test_orchestrator_retries_driver_crash_once(monkeypatch, tmp_path):
    from litefupzl.oneshot import orchestrator

    config = SimpleNamespace(
        cookies=["_t=redacted"],
        duration_minutes=1,
        output_dir=tmp_path,
    )

    attempts = []

    async def fake_session(slot, _config, _recorder):
        attempts.append(slot.slot_index)
        if len(attempts) == 1:
            return SlotResult(
                slot_index=slot.slot_index,
                slot_alias=slot.slot_alias,
                started_at="2026-08-21T00:00:00+00:00",
                finished_at="2026-08-21T00:00:01+00:00",
                status=SlotStatus.RUNTIME_ERROR,
                warning_codes=[WarningCode.BROWSER_DRIVER_CRASH.value],
            )
        return SlotResult(
            slot_index=slot.slot_index,
            slot_alias=slot.slot_alias,
            started_at="2026-08-21T00:00:02+00:00",
            finished_at="2026-08-21T00:00:03+00:00",
            status=SlotStatus.SUCCESS,
        )

    monkeypatch.setattr(orchestrator, "load_oneshot_env", lambda: config)

    assert await orchestrator.run_oneshot(session_runner=fake_session) == 0
    assert attempts == [1, 1]


@pytest.mark.asyncio
async def test_orchestrator_does_not_retry_confirmed_invalid_cookie(monkeypatch, tmp_path):
    from litefupzl.oneshot import orchestrator

    config = SimpleNamespace(
        cookies=["_t=redacted"],
        duration_minutes=1,
        output_dir=tmp_path,
    )
    attempts = []

    async def fake_session(slot, _config, _recorder):
        attempts.append(slot.slot_index)
        return SlotResult(
            slot_index=slot.slot_index,
            slot_alias=slot.slot_alias,
            started_at="2026-08-21T00:00:00+00:00",
            finished_at="2026-08-21T00:00:01+00:00",
            status=SlotStatus.COOKIE_INVALID,
        )

    monkeypatch.setattr(orchestrator, "load_oneshot_env", lambda: config)

    assert await orchestrator.run_oneshot(session_runner=fake_session) == 1
    assert attempts == [1]


@pytest.mark.asyncio
async def test_orchestrator_does_not_retry_driver_crash_after_authenticated_proof(
    monkeypatch,
    tmp_path,
):
    from litefupzl.oneshot import orchestrator

    config = SimpleNamespace(
        cookies=["_t=redacted"],
        duration_minutes=1,
        output_dir=tmp_path,
    )
    attempts = []

    async def fake_session(slot, _config, _recorder):
        attempts.append(slot.slot_index)
        return SlotResult(
            slot_index=slot.slot_index,
            slot_alias=slot.slot_alias,
            started_at="2026-08-21T00:00:00+00:00",
            finished_at="2026-08-21T00:00:01+00:00",
            status=SlotStatus.RUNTIME_ERROR,
            login_ok=True,
            same_context_login_proof_ok=True,
            warning_codes=[WarningCode.BROWSER_DRIVER_CRASH.value],
        )

    monkeypatch.setattr(orchestrator, "load_oneshot_env", lambda: config)

    assert await orchestrator.run_oneshot(session_runner=fake_session) == 2
    assert attempts == [1]
