import pytest


def test_turnstile_checkbox_target_uses_left_control_not_wide_label_center():
    from litefupzl.browser.navigation import _turnstile_checkbox_target

    assert _turnstile_checkbox_target(
        {"x": 521.0, "y": 324.5, "width": 174.9, "height": 24.0}
    ) == (533.0, 336.5)
    assert _turnstile_checkbox_target(
        {"x": 10.0, "y": 20.0, "width": 20.0, "height": 20.0}
    ) == (20.0, 30.0)


def test_turnstile_pixel_targets_cover_current_checkbox_center():
    from litefupzl.browser.navigation import (
        TURNSTILE_POST_CLICK_WAIT_SECONDS,
        _TURNSTILE_PIXEL_TARGETS,
        _is_usable_turnstile_box,
    )

    assert _TURNSTILE_PIXEL_TARGETS[0] == (0.07, 0.50)
    assert all(0.42 <= y_ratio <= 0.58 for _, y_ratio in _TURNSTILE_PIXEL_TARGETS)
    assert TURNSTILE_POST_CLICK_WAIT_SECONDS >= 8.0
    assert _is_usable_turnstile_box({"width": 300, "height": 65}) is True
    assert _is_usable_turnstile_box({"width": 960, "height": 284}) is False


def test_challenge_return_url_removes_only_transient_cf_tokens():
    from litefupzl.browser.navigation import _challenge_return_url

    assert _challenge_return_url(
        "https://aapl.eu.cc/?foo=1&__cf_chl_rt_tk=secret&bar=2#fragment"
    ) == "https://aapl.eu.cc/?foo=1&bar=2"


@pytest.mark.asyncio
async def test_handle_cf_challenge_resumes_navigation_after_clearance(monkeypatch):
    from litefupzl.browser import navigation

    states = iter(
        [
            {"active": True, "kind": "clearance_pending", "url": "https://aapl.eu.cc/?__cf_chl_rt_tk=x"},
            {"active": True, "kind": "clearance_pending", "url": "https://aapl.eu.cc/?__cf_chl_rt_tk=x"},
            {"active": False, "kind": "not_challenge", "url": "https://aapl.eu.cc/"},
            {"active": False, "kind": "not_challenge", "url": "https://aapl.eu.cc/"},
            {"active": False, "kind": "not_challenge", "url": "https://aapl.eu.cc/"},
        ]
    )
    resumed = []

    async def fake_classify(_page):
        return next(states)

    async def fake_resume(_page, url):
        resumed.append(url)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(navigation, "classify_cf_challenge", fake_classify)
    monkeypatch.setattr(navigation, "_resume_after_clearance", fake_resume)
    monkeypatch.setattr(navigation.asyncio, "sleep", no_sleep)

    await navigation.handle_cf_challenge(object(), timeout_seconds=10)

    assert resumed == ["https://aapl.eu.cc/"]


@pytest.mark.asyncio
async def test_handle_cf_challenge_tries_final_navigation_without_visible_clearance(monkeypatch):
    from types import SimpleNamespace

    from litefupzl.browser import navigation

    states = iter(
        [
            {"active": True, "kind": "managed", "url": "https://linux.do/challenge?__cf_chl_rt_tk=x"},
            {"active": True, "kind": "managed", "url": "https://linux.do/challenge?__cf_chl_rt_tk=x"},
            {"active": True, "kind": "waiting", "url": "https://linux.do/challenge"},
            {"active": False, "kind": "not_challenge", "url": "https://linux.do/challenge"},
        ]
    )
    resumed = []

    async def fake_classify(_page):
        return next(states)

    async def fake_resume(_page, url):
        resumed.append(url)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(navigation, "classify_cf_challenge", fake_classify)
    monkeypatch.setattr(navigation, "_resume_after_clearance", fake_resume)
    monkeypatch.setattr(navigation.asyncio, "sleep", no_sleep)

    page = SimpleNamespace(url="https://linux.do/challenge?__cf_chl_rt_tk=x")
    await navigation.handle_cf_challenge(page, timeout_seconds=1)

    assert resumed == ["https://linux.do/challenge"]


@pytest.mark.asyncio
async def test_final_challenge_recovery_can_require_second_target_request(monkeypatch):
    from litefupzl.browser import navigation

    resumed = []
    exit_results = iter([False, True])

    async def fake_resume(_page, url):
        resumed.append(url)

    async def fake_wait(_page, *, timeout_seconds):
        assert timeout_seconds == 20
        return next(exit_results)

    monkeypatch.setattr(navigation, "_resume_after_clearance", fake_resume)
    monkeypatch.setattr(navigation, "_wait_for_challenge_exit", fake_wait)

    recovered = await navigation._recover_after_challenge_timeout(
        object(),
        "https://linux.do/challenge",
    )

    assert recovered is True
    assert resumed == ["https://linux.do/challenge", "https://linux.do/challenge"]


@pytest.mark.asyncio
async def test_prime_cf_uses_final_recovery_after_repeated_clearance_navigation(monkeypatch):
    from litefupzl.browser import navigation

    active = {"active": True, "kind": "clearance_pending", "url": "https://linux.do/challenge"}
    states = iter([active, active, active, active, active])
    recovered = []

    class Page:
        url = "https://linux.do/challenge"

        async def goto(self, *_args, **_kwargs):
            return None

        async def title(self):
            return "Just a moment..."

    async def fake_classify(_page):
        return next(states)

    async def has_clearance(_page, _base_url):
        return True

    async def fake_resume(_page, _url):
        return None

    async def fake_recover(_page, url):
        recovered.append(url)
        return True

    monkeypatch.setattr(navigation, "classify_cf_challenge", fake_classify)
    monkeypatch.setattr(navigation, "_has_cf_clearance", has_clearance)
    monkeypatch.setattr(navigation, "_resume_after_clearance", fake_resume)
    monkeypatch.setattr(navigation, "_recover_after_challenge_timeout", fake_recover)

    assert await navigation.prime_cf_challenge(Page(), "https://linux.do", timeout_seconds=30)
    assert recovered == ["https://linux.do/challenge"]
