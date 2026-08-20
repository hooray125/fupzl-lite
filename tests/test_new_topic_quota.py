import asyncio
from types import SimpleNamespace

import pytest

from litefupzl.discourse.models import Topic
from litefupzl.oneshot.models import SlotConfig, SlotResult, SlotStatus, WarningCode, utc_now_iso


class _Recorder:
    def __init__(self):
        self.events = []

    def emit(self, slot, step, status, **kwargs):
        self.events.append({"slot": slot, "step": step, "status": status, **kwargs})


class _AsyncClosable:
    async def close(self):
        return None

    async def stop(self):
        return None


class _TimingPage:
    def __init__(self):
        self.callbacks = {}

    def on(self, event, callback):
        self.callbacks[event] = callback


class _Response:
    def __init__(self, topic_id: int):
        self.url = "https://linux.do/topics/timings"
        self.status = 200
        self.request = type(
            "Request",
            (),
            {"post_data": f"topic_id={topic_id}&topic_time=1000&timings%5B1%5D=1000"},
        )()


def _topic(topic_id: int, *, unseen: bool) -> Topic:
    return Topic(
        id=topic_id,
        title="redacted",
        slug=f"topic-{topic_id}",
        url=f"https://linux.do/t/topic-{topic_id}/{topic_id}",
        unread_posts=1,
        unseen=unseen,
        closed=False,
        archived=False,
        tags=[],
        category_id=1,
    )


def test_topic_time_budget_evenly_divides_remaining_slot():
    from litefupzl.oneshot.session import _topic_time_budget_seconds

    assert _topic_time_budget_seconds(
        remaining_seconds=180,
        remaining_new_topics=6,
        is_countable_new_topic=True,
    ) == 30
    assert _topic_time_budget_seconds(
        remaining_seconds=180,
        remaining_new_topics=0,
        is_countable_new_topic=True,
    ) == 180
    assert _topic_time_budget_seconds(
        remaining_seconds=180,
        remaining_new_topics=6,
        is_countable_new_topic=False,
    ) == 180


def test_unmet_new_topic_target_records_warning():
    from litefupzl.oneshot.session import _record_new_topic_target_status

    result = SlotResult(
        slot_index=1,
        slot_alias="slot-001",
        started_at=utc_now_iso(),
        new_topic_target=2,
        new_topics_confirmed=1,
    )
    recorder = _Recorder()

    _record_new_topic_target_status(result, recorder, result.slot_alias)

    assert result.new_topic_target_met is False
    assert WarningCode.NEW_TOPIC_TARGET_UNMET.value in result.warning_codes


@pytest.mark.asyncio
async def test_run_slot_counts_only_explicit_unseen_with_matching_timings_200(monkeypatch):
    from litefupzl.oneshot import session

    page = _TimingPage()

    async def fake_create_browser_context(*, temp_profile, config):
        return _AsyncClosable(), _AsyncClosable(), page, _AsyncClosable()

    async def fake_safe_goto(_page, url, *args, **kwargs):
        topic_id = int(url.rsplit("/", 1)[-1])
        if topic_id != 2:
            page.callbacks["response"](_Response(topic_id))

    async def fake_read_topic_to_bottom(*args, **kwargs):
        return True, None

    monkeypatch.setattr(session, "_create_browser_context", fake_create_browser_context)
    monkeypatch.setattr(session, "_get_browser_user_agent", lambda _page: asyncio.sleep(0, result="test-agent"))
    monkeypatch.setattr(session, "_ensure_logged_in", lambda *args, **kwargs: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(session, "_extract_username", lambda _page: asyncio.sleep(0, result="redacted-user"))
    monkeypatch.setattr(session, "_probe_security_preferences_via_browser", lambda *args: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(
        session,
        "_probe_security_preferences_device_list_via_browser",
        lambda *args: asyncio.sleep(0, result="ok"),
    )
    monkeypatch.setattr(
        session,
        "get_user_info_via_http",
        lambda *args, **kwargs: SimpleNamespace(suspended_till=None, silenced_till=None),
    )
    monkeypatch.setattr(
        session,
        "_build_topic_queue",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=[
                _topic(1, unseen=True),
                _topic(2, unseen=True),
                _topic(3, unseen=False),
            ],
        ),
    )
    monkeypatch.setattr(session, "safe_goto", fake_safe_goto)
    monkeypatch.setattr(session, "random_delay", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(session, "_read_topic_to_bottom", fake_read_topic_to_bottom)
    monkeypatch.setattr(session, "get_latest_topics_pages_via_http", lambda *args, **kwargs: [])

    config = SimpleNamespace(
        browser_name="chromium",
        cookie_refresh_enabled=False,
        mutual_like_users=[],
        new_topic_target_per_run=1,
        duration_minutes=1,
        topic_prefetch_pages=7,
        topic_prefetch_max_pages=10,
    )
    slot = SlotConfig(slot_index=1, slot_alias="slot-001", cookie="_t=redacted", duration_minutes=1)
    recorder = _Recorder()

    result = await session.run_slot_session(slot, config, recorder)

    assert result.status is SlotStatus.SUCCESS
    assert result.new_topic_target == 1
    assert result.new_topics_confirmed == 1
    assert result.new_topic_target_met is True
    assert WarningCode.NEW_TOPIC_TARGET_UNMET.value not in result.warning_codes
    assert not any(event.get("code") == "TIMINGS_NOT_CONFIRMED" for event in recorder.events)
