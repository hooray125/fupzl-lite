"""Public-safe /topics/timings status logging helpers."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

from litefupzl.oneshot.logging import PublicRecorder

TIMINGS_URL_CATEGORY = "/topics/timings"


class TopicTimingsTracker:
    """Track successful timings responses by submitted topic id."""

    def __init__(self) -> None:
        self._confirmed_topic_ids: set[int] = set()
        self._events: dict[int, asyncio.Event] = {}

    def prepare(self, topic_id: int) -> None:
        self._events.setdefault(topic_id, asyncio.Event())

    def is_confirmed(self, topic_id: int) -> bool:
        return topic_id in self._confirmed_topic_ids

    def observe_response(self, response) -> None:
        try:
            if TIMINGS_URL_CATEGORY not in response.url or int(response.status) != 200:
                return
            topic_id = _topic_id_from_response(response)
            if topic_id is None:
                return
            self._confirmed_topic_ids.add(topic_id)
            event = self._events.get(topic_id)
            if event is not None:
                event.set()
        except Exception:
            return

    async def wait_for_confirmation(self, topic_id: int, *, timeout_seconds: float) -> bool:
        if topic_id in self._confirmed_topic_ids:
            return True
        if timeout_seconds <= 0:
            return False
        event = self._events.setdefault(topic_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return False
        return topic_id in self._confirmed_topic_ids


def _topic_id_from_response(response) -> int | None:
    request = getattr(response, "request", None)
    post_data = getattr(request, "post_data", None)
    if callable(post_data):
        post_data = post_data()
    if not isinstance(post_data, str) or not post_data:
        return None
    values = parse_qs(post_data).get("topic_id")
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def record_topics_timing_status(
    recorder: PublicRecorder,
    *,
    slot_alias: str,
    browser_name: str,
    status_code: int | None,
) -> None:
    """Record a redacted per-browser /topics/timings status observation."""
    recorder.emit(
        slot_alias,
        "topics-timings",
        "observed",
        code=f"HTTP_{status_code}" if status_code is not None else "HTTP_UNKNOWN",
        browser_core=browser_name,
        url_category=TIMINGS_URL_CATEGORY,
        status_code=status_code,
    )


def attach_topics_timing_observer(
    page,
    recorder: PublicRecorder,
    slot_alias: str,
    browser_name: str,
    *,
    tracker: TopicTimingsTracker | None = None,
) -> None:
    """Attach a Playwright response listener for public-safe /topics/timings statuses."""
    if getattr(page, "_litefupzl_topic_timings_observer_attached", False):
        return
    setattr(page, "_litefupzl_topic_timings_observer_attached", True)

    def on_response(response) -> None:
        if TIMINGS_URL_CATEGORY not in response.url:
            return
        if tracker is not None:
            tracker.observe_response(response)
        record_topics_timing_status(
            recorder,
            slot_alias=slot_alias,
            browser_name=browser_name,
            status_code=response.status,
        )

    page.on("response", on_response)
