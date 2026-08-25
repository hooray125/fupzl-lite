"""Redacted browser probes for GitHub Actions validation.

The probe intentionally emits only status/category data. It never writes cookie
values, usernames, topic ids, topic titles, or reply text.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from litefupzl.browser.navigation import handle_cf_challenge, is_cf_challenge, safe_goto
from litefupzl.exceptions import SessionFatalError
from litefupzl.actions.read import human_like_scroll
from litefupzl.oneshot.env_loader import load_oneshot_env
from litefupzl.oneshot.session import (
    _BASE_URL,
    _build_topic_queue,
    _create_browser_context,
    _ensure_logged_in,
    _ensure_topic_posts_ready,
    _normalize_cookie_dicts,
    _prewarm_and_replace_login_page,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def _probe_challenge(page) -> dict:
    challenge_url = f"{_BASE_URL}/challenge"
    attempts: list[dict] = []
    response_error = None

    async def visit_once(label: str) -> tuple[int | None, bool]:
        local_status = None
        local_error = None
        try:
            response = await page.goto(
                challenge_url,
                timeout=90_000,
                wait_until="domcontentloaded",
            )
            if response is not None:
                local_status = response.status
        except Exception as exc:
            local_error = type(exc).__name__

        await asyncio.sleep(2)
        local_cf = await is_cf_challenge(page)
        attempts.append({
            "label": label,
            "status_code": local_status,
            "cf_challenge": local_cf,
            "error_type": local_error,
        })
        return local_status, local_cf

    status, cf_challenge = await visit_once("initial")
    if cf_challenge or status == 403:
        try:
            await asyncio.wait_for(
                handle_cf_challenge(page, timeout_seconds=90),
                timeout=120,
            )
        except SessionFatalError as exc:
            response_error = type(exc).__name__
        except Exception as exc:
            response_error = type(exc).__name__
        status, cf_challenge = await visit_once("after-challenge-handling")

    return {
        "step": "challenge",
        "status_code": status,
        "cf_challenge": cf_challenge,
        "ok": status == 404 and not cf_challenge,
        "error_type": response_error,
        "attempts": attempts,
    }


async def _probe_timings(page, slot_cookies, config) -> dict:
    status = None
    cf_like_body = None
    error_type = None
    ready = False
    observed_statuses: list[int] = []
    scroll_observed_statuses: list[int] = []
    timings_status_events: list[dict] = []

    def _append_timing_status(status_code: int | None) -> None:
        timings_status_events.append({
            "ts": _now(),
            "slot_alias": "probe",
            "browser_core": config.browser_name,
            "url_category": "/topics/timings",
            "status_code": status_code,
        })

    def on_response(response) -> None:
        if "/topics/timings" not in response.url:
            return
        observed_statuses.append(response.status)
        _append_timing_status(int(response.status))

    try:
        page.on("response", on_response)
        topics = await _build_topic_queue(slot_cookies, config)
        if not topics:
            return {
                "step": "topics-timings",
                "status_code": None,
                "cf_like_body": None,
                "observed_status_codes": observed_statuses,
                "timings_status_events": timings_status_events,
                "topic_ready": False,
                "ok": False,
                "error_type": "NO_TOPICS",
            }

        topic = topics[0]
        await safe_goto(page, topic.url, timeout=90_000)
        ready, ready_reason = await _ensure_topic_posts_ready(page)
        if not ready:
            return {
                "step": "topics-timings",
                "status_code": None,
                "cf_like_body": None,
                "observed_status_codes": observed_statuses,
                "timings_status_events": timings_status_events,
                "topic_ready": False,
                "ok": False,
                "error_type": ready_reason or "TOPIC_NOT_READY",
            }

        try:
            await human_like_scroll(
                page,
                max_duration_seconds=25,
                safety_timeout_seconds=35,
                bottom_dwell_seconds_range=(1.0, 2.0),
            )
        except Exception:
            pass
        await asyncio.sleep(3)
        scroll_observed_statuses = list(observed_statuses)

        probe_result = await page.evaluate(
            """async ({ topicId }) => {
                const csrf = document.querySelector("meta[name='csrf-token']")?.content || "";
                const params = new URLSearchParams();
                params.set("topic_id", String(topicId));
                params.set("topic_time", "1000");
                params.set("timings[1]", "1000");
                const response = await fetch("/topics/timings", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                        ...(csrf ? {"X-CSRF-Token": csrf} : {}),
                    },
                    body: params.toString(),
                });
                const body = (await response.text()).slice(0, 500).toLowerCase();
                const cfLike = body.includes("cloudflare")
                    || body.includes("just a moment")
                    || body.includes("cf-challenge")
                    || body.includes("cf-turnstile");
                return {status: response.status, cfLike};
            }""",
            {"topicId": topic.id},
        )
        status = int(probe_result.get("status"))
        _append_timing_status(status)
        cf_like_body = bool(probe_result.get("cfLike"))
    except Exception as exc:
        error_type = type(exc).__name__

    return {
        "step": "topics-timings",
        "status_code": status,
        "cf_like_body": cf_like_body,
        "observed_status_codes": scroll_observed_statuses,
        "timings_status_events": timings_status_events,
        "topic_ready": ready,
        "ok": bool(
            (
                scroll_observed_statuses
                and all(item != 403 for item in scroll_observed_statuses)
            )
            or
            (status and status != 403 and not cf_like_body)
        ),
        "error_type": error_type,
    }


async def run_probe() -> int:
    config = load_oneshot_env()
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "started_at": _now(),
        "browser": config.browser_name,
        "headless": config.headless,
        "login": {"state": "not_started", "ok": False},
        "challenge": None,
        "topics_timings": None,
    }

    temp_profile = Path(tempfile.mkdtemp(prefix="litefupzl-probe-"))
    controller = context = page = json_page = None
    try:
        cookie_string = config.cookies[0]
        slot_cookies = _normalize_cookie_dicts(cookie_string)
        controller, context, page, json_page = await _create_browser_context(
            temp_profile=temp_profile,
            config=config,
        )
        page, _ = await _prewarm_and_replace_login_page(context, page)
        login_state = await _ensure_logged_in(
            page,
            context,
            cookie_string,
            slot_cookies=slot_cookies,
        )
        result["login"] = {"state": login_state, "ok": login_state == "ok"}
        if login_state == "ok":
            result["topics_timings"] = await _probe_timings(page, slot_cookies, config)
        result["challenge"] = await _probe_challenge(page)
    finally:
        result["finished_at"] = _now()
        (output_dir / "browser_probe.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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

    challenge_ok = result.get("challenge", {}).get("ok") if result.get("challenge") else False
    timings_ok = result.get("topics_timings", {}).get("ok") if result.get("topics_timings") else False
    # Active /challenge may intentionally remain blocked in GitHub-hosted
    # environments. Keep recording it as evidence, but do not fail a read-only
    # validation run when normal login + timings/read probes work.
    return 0 if result["login"]["ok"] and timings_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe()))
