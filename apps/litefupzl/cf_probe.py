"""Redacted Cloudflare challenge probe for CTF/toy targets.

This probe only verifies whether a browser can reach the actual page after a
Cloudflare challenge. It does not perform any site action after navigation.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from litefupzl.browser.navigation import classify_cf_challenge, handle_cf_challenge  # noqa: E402
from litefupzl.browser import navigation as navigation_module  # noqa: E402
from litefupzl.oneshot.env_loader import load_oneshot_env  # noqa: E402
from litefupzl.oneshot.session import _create_browser_context, _normalize_cookie_dicts  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def _snapshot(page, context, target_url: str) -> dict:
    state = await classify_cf_challenge(page)
    title = ""
    title_error_type = None
    try:
        title = await page.title()
    except Exception as exc:
        title_error_type = type(exc).__name__
    try:
        cookies = await context.cookies([target_url])
    except Exception:
        cookies = []
    try:
        body_text = (await page.text_content("body")) or ""
    except Exception:
        body_text = ""
    try:
        challenge_frame_boxes = await navigation_module._collect_challenge_frame_boxes(page)
    except Exception:
        challenge_frame_boxes = []
    try:
        click_attempt_details = list(getattr(page, "_litefupzl_cf_click_attempts", []))
    except Exception:
        click_attempt_details = []
    snapshot = {
        "title": title,
        "url": page.url,
        "challenge": state,
        "challenge_frame_boxes": challenge_frame_boxes,
        "click_attempt_details": click_attempt_details[-30:],
        "cookie_names": sorted(
            cookie.get("name", "<unknown>")
            for cookie in cookies
            if cookie.get("name")
        ),
        "body_preview": body_text[:240],
    }
    if title_error_type:
        snapshot["title_error_type"] = title_error_type
    return snapshot


def _latest_status(status_history: list[dict]) -> int | None:
    for item in reversed(status_history):
        status = item.get("status_code")
        if isinstance(status, int):
            return status
    return None


def _probe_passed(snapshot: dict, status_history: list[dict]) -> bool:
    """Return True only when the target produced 404 outside a CF page."""
    challenge = snapshot.get("challenge") or {}
    return _latest_status(status_history) == 404 and not challenge.get("active")


async def run_probe() -> int:
    config = load_oneshot_env()
    target_url = os.environ.get("LITEFUPZL_CF_PROBE_URL") or os.environ.get(
        "FUCKPZL_CF_PROBE_URL", "https://linux.do/challenge"
    )
    timeout_seconds = int(
        os.environ.get("LITEFUPZL_CF_PROBE_TIMEOUT_SECONDS")
        or os.environ.get("FUCKPZL_CF_PROBE_TIMEOUT_SECONDS", "120")
    )
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "started_at": _now(),
        "target": target_url,
        "browser": config.browser_name,
        "headless": config.headless,
        "virtual_display": config.virtual_display,
        "proxy_configured": bool(config.proxy_server),
        "navigation": None,
        "verification_navigation": None,
        "status_history": [],
        "before": None,
        "after": None,
        "click_attempts": 0,
        "ok": False,
    }

    temp_profile = Path(tempfile.mkdtemp(prefix="litefupzl-cf-probe-"))
    controller = context = page = json_page = None
    try:
        controller, context, page, json_page = await _create_browser_context(
            temp_profile=temp_profile,
            config=config,
        )
        if config.cookies:
            await context.add_cookies(_normalize_cookie_dicts(config.cookies[0]))
        try:
            visible_cookies = await context.cookies([target_url])
            result["initial_cookie_names"] = sorted(
                cookie.get("name", "<unknown>")
                for cookie in visible_cookies
                if cookie.get("name")
            )
        except Exception as exc:
            result["initial_cookie_error_type"] = type(exc).__name__

        def on_response(response) -> None:
            try:
                if response.url.split("#", 1)[0].startswith(target_url):
                    result["status_history"].append({
                        "url": response.url,
                        "status_code": int(response.status),
                    })
                    del result["status_history"][:-20]
            except Exception:
                return

        try:
            page.on("response", on_response)
        except Exception:
            pass

        try:
            response = await page.goto(
                target_url,
                timeout=90_000,
                wait_until="domcontentloaded",
            )
            result["navigation"] = {
                "status_code": response.status if response is not None else None,
                "error_type": None,
            }
        except Exception as exc:
            result["navigation"] = {
                "status_code": None,
                "error_type": type(exc).__name__,
            }

        result["before"] = await _snapshot(page, context, target_url)
        original_click = navigation_module._attempt_turnstile_click

        async def counted_click(*args, **kwargs):
            clicked = await original_click(*args, **kwargs)
            if clicked:
                result["click_attempts"] += 1
            return clicked

        navigation_module._attempt_turnstile_click = counted_click
        try:
            await handle_cf_challenge(page, timeout_seconds=timeout_seconds)
        except Exception as exc:
            result["challenge_error_type"] = type(exc).__name__
        finally:
            navigation_module._attempt_turnstile_click = original_click

        # Revisit the target after challenge handling. For linux.do/challenge,
        # the protocol-level success criterion is a normal 404 response; a
        # challenge page is still a 403 and must not be treated as success.
        try:
            response = await page.goto(
                target_url,
                timeout=90_000,
                wait_until="domcontentloaded",
            )
            result["verification_navigation"] = {
                "status_code": response.status if response is not None else None,
                "error_type": None,
            }
        except Exception as exc:
            result["verification_navigation"] = {
                "status_code": None,
                "error_type": type(exc).__name__,
            }

        # Give Cloudflare/browser one final short window to finish navigation.
        for _ in range(8):
            result["after"] = await _snapshot(page, context, target_url)
            result["latest_status_code"] = _latest_status(result["status_history"])
            result["ok"] = _probe_passed(result["after"], result["status_history"])
            if result["ok"]:
                break
            await page.wait_for_timeout(2_000)
    finally:
        result["finished_at"] = _now()
        (output_dir / "cf_probe.json").write_text(
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

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe()))
