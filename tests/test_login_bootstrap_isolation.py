import pytest


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, slot, step, status, **kwargs):
        self.events.append((slot, step, status, kwargs))


@pytest.mark.asyncio
async def test_cf_prewarm_page_is_closed_before_fresh_login_page(monkeypatch):
    from litefupzl.oneshot import session

    class Page:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class Context:
        def __init__(self, login_page):
            self.login_page = login_page
            self.new_page_calls = 0

        async def new_page(self):
            self.new_page_calls += 1
            return self.login_page

    prewarm_page = Page()
    login_page = Page()
    context = Context(login_page)
    recorder = Recorder()

    async def fake_prime(page, base_url, timeout_seconds):
        assert page is prewarm_page
        assert base_url == "https://linux.do"
        assert timeout_seconds == 60
        return True

    monkeypatch.setattr(session, "prime_cf_challenge", fake_prime)
    monkeypatch.setattr(session, "_attach_session_current_observer", lambda _page: None)

    returned_page, ready = await session._prewarm_and_replace_login_page(
        context,
        prewarm_page,
        recorder=recorder,
        slot_alias="slot-001",
    )

    assert ready is True
    assert prewarm_page.closed is True
    assert returned_page is login_page
    assert context.new_page_calls == 1
    assert [(step, status) for _, step, status, _ in recorder.events] == [
        ("login-prewarm", "started"),
        ("login-prewarm", "finished"),
    ]


@pytest.mark.asyncio
async def test_cookie_bootstrap_injects_then_navigates_home_once_without_reload(monkeypatch):
    from litefupzl.oneshot import session

    calls = []
    recorder = Recorder()
    page = object()

    class Context:
        async def add_cookies(self, cookies):
            calls.append(("add_cookies", cookies))

    async def fake_goto(current_page, url, **kwargs):
        calls.append(("goto", current_page, url, kwargs))

    async def fail_reload(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("fresh auth page must not be reloaded")

    async def fake_wait(current_page, *, timeout_seconds):
        assert current_page is page
        assert timeout_seconds == 20
        return "ok"

    monkeypatch.setattr(session, "safe_goto", fake_goto)
    monkeypatch.setattr(session, "safe_reload", fail_reload)
    monkeypatch.setattr(session, "_wait_for_browser_login", fake_wait)

    cookies = [{"name": "_t", "value": "redacted", "domain": "linux.do", "path": "/"}]
    state = await session._bootstrap_cookie_session(
        page,
        Context(),
        cookies,
        recorder=recorder,
        slot_alias="slot-001",
    )

    assert state == "ok"
    assert calls == [
        ("add_cookies", cookies),
        ("goto", page, "https://linux.do/", {"timeout": 90_000}),
    ]
    assert [(step, status) for _, step, status, _ in recorder.events] == [
        ("login-cookie-injection", "started"),
        ("login-cookie-injection", "finished"),
        ("login-auth-navigation", "started"),
        ("login-auth-navigation", "finished"),
    ]
