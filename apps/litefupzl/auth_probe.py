"""Redacted authentication diagnostics for GitHub Actions.

This probe is intentionally coarse-grained. It records only status codes and
page/endpoint categories, never cookie values, usernames, topic ids, titles, or
reply text.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from litefupzl.browser.navigation import is_cf_challenge
from litefupzl.oneshot.env_loader import load_oneshot_env
from litefupzl.oneshot.session import (
    _BASE_URL,
    _create_browser_context,
    _extract_username,
    _get_browser_user_agent,
    _normalize_cookie_dicts,
    _normalize_security_device_probe_result,
    _prewarm_and_replace_login_page,
    _probe_notifications_via_browser,
    _probe_private_preferences_via_browser,
    _probe_security_preferences_device_list_via_browser,
    _probe_security_preferences_via_browser,
    _probe_current_session_via_browser,
    _summarize_user_agent,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_http_session(slot_cookies: list[dict], *, user_agent: str | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    if user_agent and user_agent.strip():
        session.headers["User-Agent"] = user_agent.strip()
    for cookie in slot_cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        session.cookies.set(
            name,
            value,
            domain=cookie.get("domain") or "linux.do",
            path=cookie.get("path") or "/",
        )
    return session


def _classify_body(text: str) -> dict:
    lowered = text[:1200].lower()
    return {
        "cf_like": any(
            marker in lowered
            for marker in (
                "cloudflare",
                "just a moment",
                "cf-challenge",
                "cf-turnstile",
            )
        ),
        "logged_out_like": any(
            marker in lowered
            for marker in (
                "auth-buttons",
                "login-button",
                "登录",
            )
        ),
        "logged_in_marker_like": any(
            marker in lowered
            for marker in (
                'id="current-user"',
                "toggle-current-user",
                "currentuser",
            )
        ),
        "rate_limited_like": any(
            marker in lowered
            for marker in (
                "rate limit",
                "too many requests",
                "请求过多",
                "访问过于频繁",
            )
        ),
    }


def _cookie_snapshot(
    visible_cookies: list[dict],
    *,
    input_cookie_lookup: dict[str, str] | None = None,
) -> dict:
    lookup = {
        str(cookie.get("name")): str(cookie.get("value"))
        for cookie in visible_cookies
        if cookie.get("name") and cookie.get("value") is not None
    }
    input_cookie_lookup = input_cookie_lookup or {}
    result = {
        "names": sorted(lookup.keys()),
        "has_t": "_t" in lookup,
        "has_forum_session": "_forum_session" in lookup,
        "has_cf_clearance": "cf_clearance" in lookup,
        "has_cfuvid": "_cfuvid" in lookup,
    }
    if "_t" in lookup and "_t" in input_cookie_lookup:
        result["t_value_changed"] = lookup["_t"] != input_cookie_lookup["_t"]
    return result


def _http_endpoint_probe(slot_cookies: list[dict], *, user_agent: str | None = None) -> dict:
    session = _new_http_session(slot_cookies, user_agent=user_agent)
    endpoints = {
        "home": f"{_BASE_URL}/",
        "notifications_html": f"{_BASE_URL}/notifications",
        "notifications_json": f"{_BASE_URL}/notifications.json?recent=true&limit=1",
        "session_current": f"{_BASE_URL}/session/current.json",
    }
    output: dict[str, dict] = {
        "_request_user_agent_summary": _summarize_user_agent(user_agent),
    }
    for name, url in endpoints.items():
        try:
            response = session.get(
                url,
                impersonate="firefox135",
                timeout=25,
                allow_redirects=True,
                headers={
                    **session.headers,
                    "Accept": (
                        "application/json, text/javascript, */*; q=0.01"
                        if name.endswith("json") or name == "session_current"
                        else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                    ),
                    "Referer": _BASE_URL,
                },
            )
            body_flags = _classify_body(response.text)
            output[name] = {
                "status_code": response.status_code,
                "final_host_ok": response.url.startswith(_BASE_URL),
                **body_flags,
            }
        except Exception as exc:
            output[name] = {
                "status_code": None,
                "error_type": type(exc).__name__,
            }
    return output


async def _browser_endpoint_probe(
    config,
    slot_cookies: list[dict],
) -> tuple[dict, str | None]:
    temp_profile = Path(tempfile.mkdtemp(prefix="litefupzl-auth-probe-"))
    controller = context = page = json_page = None
    input_cookie_lookup = {
        str(cookie.get("name")): str(cookie.get("value"))
        for cookie in slot_cookies
        if cookie.get("name") and cookie.get("value") is not None
    }
    result: dict = {
        "context_created": False,
        "cookie_state_initial": None,
        "cookie_state_after_home": None,
        "cookie_state_after_session_current": None,
        "cookie_state_after_notifications": None,
        "home": None,
        "dom": None,
        "session_current_state": None,
        "session_current_fetch": None,
        "notifications_state": None,
        "notifications_fetch": None,
        "my_preferences_state": None,
        "my_preferences_fetch": None,
        "username_probe": None,
        "security_preferences_state": None,
        "security_preferences_fetch": None,
        "security_device_state": None,
        "security_device_probe": None,
        "browser_user_agent_summary": None,
    }
    browser_user_agent = None
    try:
        controller, context, page, json_page = await _create_browser_context(
            temp_profile=temp_profile,
            config=config,
        )
        result["context_created"] = True
        page, challenge_ready = await _prewarm_and_replace_login_page(context, page)
        result["challenge_ready"] = challenge_ready
        browser_user_agent = await _get_browser_user_agent(page)
        result["browser_user_agent_summary"] = _summarize_user_agent(browser_user_agent)
        await context.add_cookies(slot_cookies)
        try:
            visible_cookies = await context.cookies([_BASE_URL])
            result["cookie_state_initial"] = _cookie_snapshot(
                visible_cookies,
                input_cookie_lookup=input_cookie_lookup,
            )
        except Exception as exc:
            result["cookie_state_initial"] = {"error_type": type(exc).__name__}

        try:
            response = await page.goto(
                _BASE_URL,
                timeout=90_000,
                wait_until="domcontentloaded",
            )
            response_headers = {}
            if response is not None:
                try:
                    response_headers = await response.all_headers()
                except Exception:
                    response_headers = {}
            set_cookie = str(response_headers.get("set-cookie", ""))
            result["home"] = {
                "status_code": response.status if response is not None else None,
                "cf_challenge": await is_cf_challenge(page),
                "set_cookie_has_forum_session": "_forum_session=" in set_cookie,
                "set_cookie_has_t": "_t=" in set_cookie,
            }
        except Exception as exc:
            result["home"] = {
                "status_code": None,
                "error_type": type(exc).__name__,
                "cf_challenge": await is_cf_challenge(page),
            }
        try:
            visible_cookies = await context.cookies([_BASE_URL])
            result["cookie_state_after_home"] = _cookie_snapshot(
                visible_cookies,
                input_cookie_lookup=input_cookie_lookup,
            )
        except Exception as exc:
            result["cookie_state_after_home"] = {"error_type": type(exc).__name__}

        try:
            result["dom"] = await page.evaluate(
                """() => {
                    const text = (document.body && document.body.innerText || "").slice(0, 1200).toLowerCase();
                    return {
                        hasCurrentUser: !!document.querySelector("#current-user"),
                        hasHeaderAvatar: !!document.querySelector("#current-user img.avatar, .d-header-icons img.avatar, .header-dropdown-toggle img.avatar"),
                        hasAuthButtons: !!document.querySelector("span.auth-buttons, .auth-buttons, .login-button"),
                        cfLike: text.includes("cloudflare") ||
                            text.includes("just a moment") ||
                            text.includes("cf-challenge") ||
                            text.includes("cf-turnstile"),
                        rateLimitedLike: text.includes("rate limit") ||
                            text.includes("too many requests") ||
                            text.includes("请求过多") ||
                            text.includes("访问过于频繁"),
                    };
                }"""
            )
        except Exception as exc:
            result["dom"] = {"error_type": type(exc).__name__}

        result["session_current_state"] = await _probe_current_session_via_browser(page)
        try:
            result["session_current_fetch"] = await page.evaluate(
                """async () => {
                    const response = await fetch("/session/current.json", {
                        method: "GET",
                        credentials: "same-origin",
                        headers: {
                            "Accept": "application/json, text/javascript, */*; q=0.01",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    });
                    const text = await response.text();
                    const lowered = text.slice(0, 800).toLowerCase();
                    let hasUser = false;
                    try {
                        const payload = JSON.parse(text);
                        const user = payload.current_user || payload.currentUser || payload.user;
                        hasUser = !!(user && (user.username || user.id));
                    } catch (_) {}
                    return {
                        status_code: response.status,
                        has_user: hasUser,
                        cf_like: lowered.includes("cloudflare") ||
                            lowered.includes("just a moment") ||
                            lowered.includes("cf-challenge") ||
                            lowered.includes("cf-turnstile"),
                        rate_limited_like: lowered.includes("rate limit") ||
                            lowered.includes("too many requests"),
                    };
                }"""
            )
        except Exception as exc:
            result["session_current_fetch"] = {"error_type": type(exc).__name__}
        try:
            visible_cookies = await context.cookies([_BASE_URL])
            result["cookie_state_after_session_current"] = _cookie_snapshot(
                visible_cookies,
                input_cookie_lookup=input_cookie_lookup,
            )
        except Exception as exc:
            result["cookie_state_after_session_current"] = {"error_type": type(exc).__name__}

        result["notifications_state"] = await _probe_notifications_via_browser(page)
        try:
            result["notifications_fetch"] = await page.evaluate(
                """async () => {
                    const response = await fetch("/notifications.json?recent=true&limit=1", {
                        method: "GET",
                        credentials: "same-origin",
                        headers: {
                            "Accept": "application/json, text/javascript, */*; q=0.01",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    });
                    const text = await response.text();
                    const lowered = text.slice(0, 800).toLowerCase();
                    let validJson = false;
                    try {
                        const payload = JSON.parse(text);
                        validJson = Array.isArray(payload) || (!!payload && typeof payload === "object");
                    } catch (_) {}
                    return {
                        status_code: response.status,
                        valid_json: validJson,
                        cf_like: lowered.includes("cloudflare") ||
                            lowered.includes("just a moment") ||
                            lowered.includes("cf-challenge") ||
                            lowered.includes("cf-turnstile"),
                        rate_limited_like: lowered.includes("rate limit") ||
                            lowered.includes("too many requests"),
                        logged_out_like: lowered.includes("login-button") ||
                            lowered.includes("auth-buttons") ||
                            lowered.includes("登录"),
                    };
                }"""
            )
        except Exception as exc:
            result["notifications_fetch"] = {"error_type": type(exc).__name__}
        try:
            visible_cookies = await context.cookies([_BASE_URL])
            result["cookie_state_after_notifications"] = _cookie_snapshot(
                visible_cookies,
                input_cookie_lookup=input_cookie_lookup,
            )
        except Exception as exc:
            result["cookie_state_after_notifications"] = {"error_type": type(exc).__name__}

        result["my_preferences_state"] = await _probe_private_preferences_via_browser(page)
        try:
            result["my_preferences_fetch"] = await page.evaluate(
                """async () => {
                    const response = await fetch("/my/preferences/account", {
                        method: "GET",
                        credentials: "same-origin",
                        headers: {
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    });
                    const text = await response.text();
                    const lowered = text.slice(0, 1600).toLowerCase();
                    return {
                        status_code: response.status,
                        private_preferences_path: response.url.includes("/preferences/account"),
                        login_path_like: response.url.includes("/login"),
                        has_account_form_like: lowered.includes("preferences/account") ||
                            lowered.includes("save changes") ||
                            lowered.includes("change-email") ||
                            lowered.includes("change password"),
                        logged_out_like: lowered.includes("login-button") ||
                            lowered.includes("auth-buttons") ||
                            lowered.includes("您需要登录") ||
                            lowered.includes("you need to log in"),
                        cf_like: lowered.includes("cloudflare") ||
                            lowered.includes("just a moment") ||
                            lowered.includes("cf-challenge") ||
                            lowered.includes("cf-turnstile"),
                    };
                }"""
            )
        except Exception as exc:
            result["my_preferences_fetch"] = {"error_type": type(exc).__name__}

        try:
            username = await _extract_username(page)
        except Exception:
            username = None
        result["username_probe"] = {
            "present": bool(username),
            "source": "browser_context" if username else None,
        }
        if username:
            result["security_preferences_state"] = await _probe_security_preferences_via_browser(page, username)
            try:
                result["security_preferences_fetch"] = await page.evaluate(
                    """async (username) => {
                        const encodedUsername = encodeURIComponent(username);
                        const response = await fetch(`/u/${encodedUsername}/preferences/security`, {
                            method: "GET",
                            credentials: "same-origin",
                            headers: {
                                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                "X-Requested-With": "XMLHttpRequest",
                            },
                        });
                        const text = await response.text();
                        const lowered = text.slice(0, 1600).toLowerCase();
                        return {
                            status_code: response.status,
                            security_preferences_path: response.url.includes("/preferences/security"),
                            username_path_ok: response.url.includes(`/u/${encodedUsername}/`) ||
                                response.url.includes(`/u/${username}/`),
                            login_path_like: response.url.includes("/login"),
                            logged_out_like: lowered.includes("login-button") ||
                                lowered.includes("auth-buttons") ||
                                lowered.includes("您需要登录") ||
                                lowered.includes("you need to log in"),
                            cf_like: lowered.includes("cloudflare") ||
                                lowered.includes("just a moment") ||
                                lowered.includes("cf-challenge") ||
                                lowered.includes("cf-turnstile"),
                        };
                    }""",
                    username,
                )
            except Exception as exc:
                result["security_preferences_fetch"] = {"error_type": type(exc).__name__}
            try:
                device_probe_result = await _probe_security_preferences_device_list_via_browser(
                    page,
                    username,
                )
                device_state, device_proof = _normalize_security_device_probe_result(
                    device_probe_result
                )
                result["security_device_state"] = device_state
                result["security_device_probe"] = device_proof
            except Exception as exc:
                result["security_device_state"] = "unknown"
                result["security_device_probe"] = {"error_type": type(exc).__name__}
        else:
            result["security_preferences_state"] = "cookie_invalid"
            result["security_preferences_fetch"] = {"skipped": "USERNAME_UNAVAILABLE"}
            result["security_device_state"] = "cookie_invalid"
            result["security_device_probe"] = {"skipped": "USERNAME_UNAVAILABLE"}
    finally:
        if json_page is not None:
            try:
                await json_page.close()
            except Exception:
                pass
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if controller is not None:
            try:
                await controller.stop()
            except Exception:
                pass
        shutil.rmtree(temp_profile, ignore_errors=True)
    return result, browser_user_agent


def _is_auth_probe_success(browser_probe: dict) -> bool:
    security_fetch = browser_probe.get("security_preferences_fetch") or {}
    security_device = browser_probe.get("security_device_probe") or {}
    device_logged_out = security_device.get("logged_out_like") is True
    device_cf_blocked = security_device.get("cf_like") is True
    return (
        (browser_probe.get("username_probe") or {}).get("present") is True
        and browser_probe.get("security_preferences_state") == "ok"
        and security_fetch.get("status_code") == 200
        and security_fetch.get("security_preferences_path") is True
        and security_fetch.get("username_path_ok") is True
        and security_fetch.get("login_path_like") is False
        and security_fetch.get("logged_out_like") is False
        and security_fetch.get("cf_like") is False
        and not device_logged_out
        and not device_cf_blocked
    )


async def run_auth_probe() -> int:
    config = load_oneshot_env()
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cookie_string = config.cookies[0]
    slot_cookies = _normalize_cookie_dicts(cookie_string)
    cookie_names = sorted(
        cookie.get("name", "<unknown>")
        for cookie in slot_cookies
        if cookie.get("name")
    )

    browser_probe, browser_user_agent = await _browser_endpoint_probe(config, slot_cookies)
    result = {
        "started_at": _now(),
        "browser": config.browser_name,
        "headless": config.headless,
        "cookie_slot_count": len(config.cookies),
        "first_slot_cookie_count": len(slot_cookies),
        "first_slot_cookie_names": cookie_names,
        "browser_probe": browser_probe,
        "http": _http_endpoint_probe(
            slot_cookies,
            user_agent=browser_user_agent,
        ),
        "finished_at": _now(),
    }
    (output_dir / "auth_probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return 0 if _is_auth_probe_success(result["browser_probe"]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_auth_probe()))
