from types import SimpleNamespace

import pytest

from litefupzl.oneshot.models import SlotConfig, SlotStatus, WarningCode


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


def _proof(*, rows=1, current=1, **flags):
    return {
        "security_preferences_path": True,
        "username_path_ok": True,
        "auth_tokens_section_present": True,
        "device_row_count": rows,
        "current_device_row_count": current,
        "logged_out_like": False,
        "cf_like": False,
        "rate_limited_like": False,
        **flags,
    }


@pytest.mark.parametrize(
    "platform_flags",
    [
        {
            "current_device_platform_windows_like": True,
            "current_device_browser_firefox_like": True,
        },
        {
            "current_device_platform_macos_like": True,
            "current_device_browser_firefox_like": True,
        },
    ],
)
def test_current_session_proof_ignores_spoofed_platform(platform_flags):
    from litefupzl.oneshot.session import _classify_security_device_proof

    assert _classify_security_device_proof(_proof(**platform_flags)) == "ok"


def test_current_session_proof_is_localization_independent():
    from litefupzl.oneshot.session import _classify_security_device_proof

    # No localized marker is required: only the dropdown-free row count matters.
    assert _classify_security_device_proof(
        _proof(current_device_platform_unknown=True, current_device_browser_unknown=True)
    ) == "ok"


@pytest.mark.asyncio
async def test_device_extractor_uses_dropdown_structure_and_sanitizes_result():
    from litefupzl.oneshot.session import _extract_security_device_proof

    class Page:
        async def evaluate(self, script, _username):
            assert ".auth-token-dropdown" in script
            assert "current_candidate: !hasDropdown" in script
            return {
                **_proof(
                    current_device_platform_windows_like=True,
                    current_device_browser_firefox_like=True,
                ),
                "raw_device": "must-not-survive",
            }

    proof = await _extract_security_device_proof(Page(), "redacted-user")

    assert proof["current_device_row_count"] == 1
    assert proof["current_device_platform_windows_like"] is True
    assert "must-not-survive" not in repr(proof)


def test_historical_rows_pass_when_exactly_one_row_is_current():
    from litefupzl.oneshot.session import _classify_security_device_proof

    assert _classify_security_device_proof(_proof(rows=5, current=1)) == "ok"


@pytest.mark.parametrize(
    ("current", "expected"),
    [(0, "missing"), (2, "ambiguous")],
)
def test_missing_or_ambiguous_current_session_row_warns(current, expected):
    from litefupzl.oneshot.session import _classify_security_device_proof

    assert _classify_security_device_proof(_proof(rows=3, current=current)) == expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"logged_out_like": True}, "cookie_invalid"),
        ({"cf_like": True}, "cf_blocked"),
    ],
)
def test_logged_out_and_cf_pages_still_fail(flags, expected):
    from litefupzl.oneshot.session import _classify_security_device_proof

    assert _classify_security_device_proof(_proof(**flags)) == expected


def test_device_proof_sanitizer_removes_raw_device_network_and_ua_details():
    from litefupzl.oneshot.session import _sanitize_security_device_proof

    sanitized = _sanitize_security_device_proof(
        {
            **_proof(),
            "device_name": "sensitive-device",
            "ip": "203.0.113.7",
            "location": "sensitive-location",
            "user_agent": "sensitive-full-ua",
            "row_summaries": [
                {
                    "current_candidate": True,
                    "has_dropdown": False,
                    "raw_text": "sensitive-device 203.0.113.7",
                }
            ],
        }
    )

    rendered = repr(sanitized)
    for sensitive in (
        "sensitive-device",
        "203.0.113.7",
        "sensitive-location",
        "sensitive-full-ua",
        "raw_text",
    ):
        assert sensitive not in rendered


@pytest.mark.asyncio
async def test_auth_probe_artifact_omits_full_user_agent(monkeypatch, tmp_path):
    from apps.litefupzl import auth_probe

    raw_user_agent = "sensitive-full-user-agent"
    config = SimpleNamespace(
        output_dir=tmp_path,
        cookies=["_t=redacted"],
        browser_name="camoufox",
        headless=False,
    )

    async def fake_browser_probe(_config, _cookies):
        return {"browser_user_agent_summary": {"present": True}}, raw_user_agent

    monkeypatch.setattr(auth_probe, "load_oneshot_env", lambda: config)
    monkeypatch.setattr(auth_probe, "_browser_endpoint_probe", fake_browser_probe)
    monkeypatch.setattr(auth_probe, "_http_endpoint_probe", lambda *args, **kwargs: {})

    assert await auth_probe.run_auth_probe() == 1
    artifact = (tmp_path / "auth_probe.json").read_text(encoding="utf-8")
    assert raw_user_agent not in artifact
    assert "redacted" not in artifact


@pytest.mark.asyncio
async def test_missing_current_device_is_warning_not_cookie_invalid(monkeypatch):
    from litefupzl.oneshot import session

    page = object()

    async def fake_create_browser_context(*, temp_profile, config):
        return AsyncClosable(), AsyncClosable(), page, AsyncClosable()

    async def fake_prewarm(_context, current_page, **_kwargs):
        return current_page, True

    async def fake_get_browser_user_agent(_page):
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/152.0"

    async def fake_ensure_logged_in(*args, **kwargs):
        return "ok"

    async def fake_extract_username(_page):
        return "redacted-user"

    async def fake_probe_security(_page, _username):
        return "ok"

    async def fake_probe_device(_page, _username):
        return "missing", _proof(rows=2, current=0)

    async def fake_build_topic_queue(*args, **kwargs):
        return []

    monkeypatch.setattr(session, "_create_browser_context", fake_create_browser_context)
    monkeypatch.setattr(session, "_prewarm_and_replace_login_page", fake_prewarm)
    monkeypatch.setattr(session, "attach_topics_timing_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(session, "_get_browser_user_agent", fake_get_browser_user_agent)
    monkeypatch.setattr(session, "_ensure_logged_in", fake_ensure_logged_in)
    monkeypatch.setattr(session, "_extract_username", fake_extract_username)
    monkeypatch.setattr(session, "_probe_security_preferences_via_browser", fake_probe_security)
    monkeypatch.setattr(session, "_probe_security_preferences_device_list_via_browser", fake_probe_device)
    monkeypatch.setattr(
        session,
        "get_user_info_via_http",
        lambda *args, **kwargs: SimpleNamespace(suspended_till=None, silenced_till=None),
    )
    monkeypatch.setattr(session, "_build_topic_queue", fake_build_topic_queue)

    config = SimpleNamespace(browser_name="camoufox", cookie_refresh_enabled=False)
    slot = SlotConfig(
        slot_index=1,
        slot_alias="slot-001",
        cookie="_t=redacted",
        duration_minutes=1,
    )
    recorder = Recorder()

    result = await session.run_slot_session(slot, config, recorder)

    assert result.status is SlotStatus.WARNING
    assert result.login_ok is True
    assert result.security_preferences_ok is True
    assert result.security_current_device_ok is False
    assert result.active_linux_device_ok is False
    assert WarningCode.LOGIN_DEVICE_PROOF_INCONCLUSIVE.value in result.warning_codes
    assert not any(event.get("code") == "COOKIE_INVALID" for event in recorder.events)
    assert any(
        event["step"] == "login-device-proof"
        and event.get("code") == "CURRENT_SESSION_DEVICE_MISSING"
        for event in recorder.events
    )


def test_auth_probe_success_does_not_require_linux_platform_label():
    from apps.litefupzl.auth_probe import _is_auth_probe_success

    browser_probe = {
        "username_probe": {"present": True},
        "security_preferences_state": "ok",
        "security_preferences_fetch": {
            "status_code": 200,
            "security_preferences_path": True,
            "username_path_ok": True,
            "login_path_like": False,
            "logged_out_like": False,
            "cf_like": False,
        },
        "security_device_state": "ok",
        "security_device_probe": _proof(
            current_device_platform_windows_like=True,
            current_device_browser_firefox_like=True,
        ),
    }

    assert _is_auth_probe_success(browser_probe) is True


@pytest.mark.asyncio
async def test_login_proof_accepts_security_page_when_device_proof_is_inconclusive(monkeypatch):
    from litefupzl.oneshot import session

    async def fake_extract_username(_page):
        return "redacted-user"

    async def fake_probe_security(_page, _username):
        return "ok"

    async def fake_probe_device(_page, _username):
        return "ambiguous", _proof(rows=3, current=2)

    monkeypatch.setattr(session, "_extract_username", fake_extract_username)
    monkeypatch.setattr(session, "_probe_security_preferences_via_browser", fake_probe_security)
    monkeypatch.setattr(session, "_probe_security_preferences_device_list_via_browser", fake_probe_device)

    state = await session._require_authenticated_login_proof(
        object(),
        [{"name": "_t", "value": "redacted"}],
        "ok",
    )

    assert state == "ok"
