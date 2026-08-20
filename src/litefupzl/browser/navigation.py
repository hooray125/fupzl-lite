"""Navigation helpers with automatic Cloudflare challenge handling.

All page.goto() calls in the application MUST go through safe_goto().
JSON API requests go through fetch_json_via_browser() using the dedicated json_page.
"""

from __future__ import annotations

import asyncio
import json
import random
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from loguru import logger

from litefupzl.exceptions import SessionFatalError

TURNSTILE_INITIAL_WAIT_SECONDS = 5.0
TURNSTILE_POST_CLICK_WAIT_SECONDS = 8.0
TURNSTILE_COOLDOWN_WAIT_SECONDS = 10.0


async def safe_goto(page, url: str, **kwargs) -> None:
    """Navigate to URL with automatic CF challenge handling.

    Default timeout: 30s. Default wait_until: domcontentloaded.
    """
    kwargs.setdefault("timeout", 30_000)
    kwargs.setdefault("wait_until", "domcontentloaded")
    try:
        await page.goto(url, **kwargs)
    except (TimeoutError, Exception) as exc:
        if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower():
            if await is_cf_challenge(page):
                await handle_cf_challenge(page)
            else:
                raise
        else:
            raise
    else:
        await handle_cf_challenge(page)


async def safe_go_back(page, **kwargs) -> None:
    """Go back with CF challenge handling."""
    kwargs.setdefault("timeout", 30_000)
    kwargs.setdefault("wait_until", "domcontentloaded")
    try:
        await page.go_back(**kwargs)
    except (TimeoutError, Exception) as exc:
        if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower():
            if await is_cf_challenge(page):
                await handle_cf_challenge(page)
            else:
                raise
        else:
            raise
    else:
        await handle_cf_challenge(page)


async def safe_reload(page, **kwargs) -> None:
    """Reload page with CF challenge handling."""
    kwargs.setdefault("timeout", 30_000)
    kwargs.setdefault("wait_until", "domcontentloaded")
    try:
        await page.reload(**kwargs)
    except (TimeoutError, Exception) as exc:
        if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower():
            if await is_cf_challenge(page):
                await handle_cf_challenge(page)
            else:
                raise
        else:
            raise
    else:
        await handle_cf_challenge(page)


async def is_cf_challenge(page) -> bool:
    """Check if current page is a Cloudflare challenge."""
    state = await classify_cf_challenge(page)
    return bool(state["active"])


async def classify_cf_challenge(page) -> dict:
    """Classify Cloudflare challenge state from page evidence.

    Returns a small dict so callers can choose a strategy:

    - ``not_challenge``: normal usable page.
    - ``managed``: JS/non-interactive challenge; wait only.
    - ``interactive``: a visible Turnstile/checkbox-like control is present.
    - ``waiting``: Cloudflare says verification succeeded / waiting, but no
      usable page or ``cf_clearance`` is available yet.
    - ``clearance_pending``: ``cf_clearance`` exists but the challenge page has
      not navigated away yet.
    - ``error``: Cloudflare challenge error page.
    """
    title = ""
    current_url = ""
    title_is_challenge = False
    url_is_challenge = False
    try:
        title = await page.title()
        current_url = str(getattr(page, "url", ""))
        title_is_challenge = any(
            marker in title
            for marker in ("Just a moment", "请稍候", "Attention Required")
        )
        url_is_challenge = any(
            marker in current_url
            for marker in ("__cf_chl_rt_tk=", "__cf_chl_tk=", "/cdn-cgi/challenge-platform/")
        )

        # linux.do often embeds Cloudflare/Turnstile assets even on normal,
        # fully usable pages. Hidden inputs alone are therefore not sufficient
        # evidence of an active blocking challenge.
        normal_page_marker = await page.query_selector(
            ",".join((
                "#current-user",
                "#main-outlet .topic-post",
                "#main-outlet .topic-list-body",
                "#main-outlet .topic-area",
            ))
        )
        if normal_page_marker is not None and not title_is_challenge and not url_is_challenge:
            return _cf_state(False, "not_challenge", title=title, url=current_url)
        if title.startswith("Loading ") and not normal_page_marker:
            return _cf_state(True, "loading", title=title, url=current_url)

        details = await page.evaluate(
            """() => {
                const isVisible = (el) => {
                  const rect = el.getBoundingClientRect();
                  const style = getComputedStyle(el);
                  return (
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    rect.width >= 1 &&
                    rect.height >= 1
                  );
                };

                const challengeSelectors = [
                  "iframe[src*='challenges.cloudflare.com']",
                  "input[name*='cf-turnstile-response']",
                  "input[id*='cf-chl-widget']",
                ];
                const interactiveSelectors = [
                  "input[type='checkbox']",
                  "button",
                  "[role='checkbox']",
                  ".ctp-checkbox-label",
                  ".ctp-checkbox-container",
                  "label"
                ];

                let hasVisibleChallengeIframe = false;
                for (const el of document.querySelectorAll("iframe[src*='challenges.cloudflare.com']")) {
                  const rect = el.getBoundingClientRect();
                  if (isVisible(el) && rect.width >= 120 && rect.height >= 40) {
                    hasVisibleChallengeIframe = true;
                  }
                }

                let hasTurnstileInput = false;
                for (const selector of challengeSelectors) {
                  for (const el of document.querySelectorAll(selector)) {
                    if (selector.startsWith("input") || isVisible(el)) {
                      hasTurnstileInput = true;
                    }
                  }
                }

                let hasVisibleInteractiveControl = false;
                for (const selector of interactiveSelectors) {
                  for (const el of document.querySelectorAll(selector)) {
                    const rect = el.getBoundingClientRect();
                    if (isVisible(el) && rect.width >= 20 && rect.height >= 20) {
                      hasVisibleInteractiveControl = true;
                    }
                  }
                }

                const text = [
                  document.body?.innerText || "",
                  document.body?.textContent || "",
                ].join("\n").trim();
                const cType = window._cf_chl_opt && window._cf_chl_opt.cType || "";
                return {
                  cType,
                  hasChallengeError: !!document.querySelector("#challenge-error-text"),
                  hasVisibleChallengeIframe,
                  hasTurnstileInput,
                  hasVisibleInteractiveControl,
                  hasWaitingText: /verification succeeded|验证成功|正在等待|waiting for/i.test(text),
                  hasChallengeText: /just a moment|请稍候|performing security verification|正在进行安全验证|attention required|enable javascript and cookies|cloudflare/i.test(text),
                };
            }"""
        )
        if not isinstance(details, dict):
            details = {
                "hasChallengeText": details is True,
                "hasChallengeError": False,
                "hasVisibleChallengeIframe": details is True,
                "hasTurnstileInput": False,
                "hasVisibleInteractiveControl": False,
                "hasWaitingText": False,
                "cType": "",
            }
        if await _has_visible_turnstile_checkbox(page):
            details["hasVisibleInteractiveControl"] = True

        has_clearance = await _has_cf_clearance_for_page(page)
        ctype = str(details.get("cType") or "")
        has_waiting = bool(details.get("hasWaitingText"))
        has_error = bool(details.get("hasChallengeError"))
        has_challenge_evidence = any(
            (
                title_is_challenge,
                url_is_challenge,
                has_error,
                bool(details.get("hasVisibleChallengeIframe")),
                bool(details.get("hasChallengeText")),
                ctype,
            )
        )

        if not has_challenge_evidence:
            return _cf_state(False, "not_challenge", title=title, url=current_url, has_clearance=has_clearance)
        if has_clearance:
            return _cf_state(True, "clearance_pending", title=title, url=current_url, ctype=ctype, has_clearance=True)
        if has_error and not has_waiting:
            return _cf_state(True, "error", title=title, url=current_url, ctype=ctype)
        if ctype == "interactive" or (
            bool(details.get("hasVisibleInteractiveControl"))
            and has_challenge_evidence
            and not has_waiting
        ):
            return _cf_state(True, "interactive", title=title, url=current_url, ctype=ctype)
        if has_waiting:
            return _cf_state(True, "waiting", title=title, url=current_url, ctype=ctype)
        return _cf_state(True, "managed", title=title, url=current_url, ctype=ctype)
    except Exception:
        if title_is_challenge or url_is_challenge:
            return _cf_state(True, "managed", title=title, url=current_url)
        return _cf_state(False, "unknown")


def _cf_state(
    active: bool,
    kind: str,
    *,
    title: str = "",
    url: str = "",
    ctype: str = "",
    has_clearance: bool = False,
) -> dict:
    return {
        "active": active,
        "kind": kind,
        "title": title,
        "url": url,
        "ctype": ctype,
        "has_clearance": has_clearance,
    }


async def handle_cf_challenge(page, timeout_seconds: int = 120) -> None:
    """Wait for CF challenge to resolve. Raises SessionFatalError on timeout.

    Polls page title every 2s. Attempts Turnstile checkbox click if found.
    """
    state = await classify_cf_challenge(page)
    if not state["active"]:
        return

    logger.info(f"Cloudflare challenge detected ({state['kind']}), waiting...")
    elapsed = 0.0
    poll_interval = 2
    click_attempts = 0
    clear_observations = 0
    clearance_resume_attempts = 0
    post_click_wait_seconds = 0.0
    first_click_probe = True

    while elapsed < timeout_seconds:
        if first_click_probe and state["kind"] in {"managed", "loading"}:
            await asyncio.sleep(TURNSTILE_INITIAL_WAIT_SECONDS)
            elapsed += TURNSTILE_INITIAL_WAIT_SECONDS
            first_click_probe = False
            state = await classify_cf_challenge(page)
            continue

        if not _turnstile_click_budget_exhausted(click_attempts) and await _should_attempt_turnstile_click(page, state):
            if await _attempt_turnstile_click(page):
                click_attempts += 1
                post_click_wait_seconds = TURNSTILE_POST_CLICK_WAIT_SECONDS

        sleep_seconds = post_click_wait_seconds if post_click_wait_seconds > 0 else (
            TURNSTILE_COOLDOWN_WAIT_SECONDS if _turnstile_click_budget_exhausted(click_attempts) else poll_interval
        )
        await asyncio.sleep(sleep_seconds)
        elapsed += sleep_seconds
        post_click_wait_seconds = 0.0

        state = await classify_cf_challenge(page)
        if not state["active"]:
            clear_observations += 1
            if clear_observations >= 2:
                logger.info("Cloudflare challenge passed")
                return
            continue
        clear_observations = 0

        if state["kind"] == "clearance_pending":
            logger.info("Cloudflare clearance cookie observed, resuming target navigation")
            if clearance_resume_attempts < 2:
                clearance_resume_attempts += 1
                return_url = _challenge_return_url(state.get("url") or str(getattr(page, "url", "")))
                await _resume_after_clearance(page, return_url)
                state = await classify_cf_challenge(page)
                continue

    # Cloudflare can finish verification just after the last regular poll. A
    # final target navigation is therefore useful even when the cookie API has
    # not exposed cf_clearance yet. If verification is still incomplete, this
    # simply returns to the challenge page and the normal failure is preserved.
    return_url = _challenge_return_url(str(getattr(page, "url", "")))
    if await _recover_after_challenge_timeout(page, return_url):
        logger.info("Cloudflare challenge passed after final target navigation")
        return

    if click_attempts > 0:
        raise SessionFatalError("Cloudflare interactive challenge not resolved within timeout")

    raise SessionFatalError("Cloudflare challenge not resolved within timeout")


async def prime_cf_challenge(
    page,
    base_url: str,
    timeout_seconds: int = 35,
) -> bool:
    """Proactively visit /challenge to warm up Cloudflare clearance.

    A `/challenge` waiting page can show "验证成功。正在等待 linux.do 响应"
    before the browser has a reusable Cloudflare clearance. Treat that as an
    intermediate state, not success: callers need either the challenge to
    disappear entirely or a `cf_clearance` cookie to appear.
    """
    challenge_url = base_url.rstrip("/") + "/challenge"

    try:
        await page.goto(challenge_url, timeout=30_000, wait_until="domcontentloaded")
    except Exception as exc:
        logger.debug(f"Challenge prewarm navigation error (continuing): {exc}")

    elapsed = 0.0
    poll_interval = 2
    click_attempts = 0
    clearance_resume_attempts = 0
    post_click_wait_seconds = 0.0
    first_click_probe = True

    while elapsed < timeout_seconds:
        try:
            await page.title()
        except Exception as exc:
            logger.debug(f"Challenge prewarm page state read failed: {exc}")
            return False

        state = await classify_cf_challenge(page)
        if not state["active"]:
            logger.info("Challenge prewarm passed cleanly")
            return True

        if await _has_cf_clearance(page, base_url):
            logger.info("Challenge prewarm obtained cf_clearance cookie; verifying target navigation")
            if clearance_resume_attempts >= 2:
                logger.warning("Challenge prewarm clearance needs final target recovery")
                break
            clearance_resume_attempts += 1
            await _resume_after_clearance(page, challenge_url)
            elapsed += 2.0
            state = await classify_cf_challenge(page)
            if not state["active"]:
                logger.info("Challenge prewarm passed after clearance navigation")
                return True
            continue

        if first_click_probe and state["kind"] in {"managed", "waiting", "loading"}:
            await asyncio.sleep(TURNSTILE_INITIAL_WAIT_SECONDS)
            elapsed += TURNSTILE_INITIAL_WAIT_SECONDS
            first_click_probe = False
            continue

        if not _turnstile_click_budget_exhausted(click_attempts) and await _should_attempt_turnstile_click(page, state):
            if await _attempt_turnstile_click(page):
                click_attempts += 1
                post_click_wait_seconds = TURNSTILE_POST_CLICK_WAIT_SECONDS

        sleep_seconds = post_click_wait_seconds if post_click_wait_seconds > 0 else (
            TURNSTILE_COOLDOWN_WAIT_SECONDS if _turnstile_click_budget_exhausted(click_attempts) else poll_interval
        )
        await asyncio.sleep(sleep_seconds)
        elapsed += sleep_seconds
        post_click_wait_seconds = 0.0

    # Mirror handle_cf_challenge's final recovery. In current Turnstile flows
    # the verification response may become reusable only on the next request.
    if await _recover_after_challenge_timeout(page, challenge_url):
        logger.info("Challenge prewarm passed after final target navigation")
        return True

    logger.warning("Challenge prewarm did not reach a usable state before timeout")
    return False


async def require_cf_challenge_ready(
    page,
    base_url: str,
    timeout_seconds: int = 35,
) -> None:
    """Ensure `/challenge` prewarm succeeds before continuing.

    This is stricter than `prime_cf_challenge`: callers that require a verified
    Cloudflare-ready browser should stop immediately when prewarm does not reach
    a usable state.
    """
    primed = await prime_cf_challenge(page, base_url, timeout_seconds=timeout_seconds)
    if not primed:
        raise SessionFatalError("Cloudflare challenge prewarm did not reach a usable state")


async def random_delay(min_ms: int, max_ms: int) -> None:
    """Sleep for a random duration between min_ms and max_ms milliseconds."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def fetch_json_via_browser(json_page, url: str) -> dict:
    """Navigate json_page to a JSON URL, parse content, then navigate to about:blank.

    Requires Firefox JSON viewer to be disabled (devtools.jsonview.enabled=false).
    """
    await safe_goto(json_page, url)
    await random_delay(500, 1500)

    content = await json_page.text_content("body")
    data = json.loads(content)

    await random_delay(300, 800)
    await safe_goto(json_page, "about:blank")

    return data


async def _attempt_turnstile_click(page) -> bool:
    """Try to click visible Turnstile controls if present.

    Strategy order:
    1. Cloudflare iframe checkbox inside challenge frame.
    2. Cloudflare iframe bounding box in the top page.
    3. Main-document challenge container around hidden turnstile input
       (mirrors the reference project's human-like click model).
    """
    try:
        for frame in page.frames:
            if "challenges.cloudflare.com" not in frame.url:
                continue
            checkbox = await frame.query_selector("input[type='checkbox']")
            if checkbox:
                box = await checkbox.bounding_box()
                if box:
                    x, y = _turnstile_checkbox_target(box)
                    x += random.uniform(-2, 2)
                    y += random.uniform(-2, 2)
                    await _human_like_mouse_click(page, x, y)
                    _record_turnstile_click(
                        page,
                        strategy="frame_checkbox",
                        box=box,
                        x=x,
                        y=y,
                    )
                    logger.debug("Clicked Turnstile checkbox inside challenge frame")
                    return True

        for item in await _collect_challenge_frame_boxes(page):
            box = item["box"]
            clicked = await _click_turnstile_pixel_region(
                page,
                box,
                strategy="frame_element_pixel_region",
            )
            if clicked:
                return True

        turnstile = await page.query_selector(
            "iframe[src*='challenges.cloudflare.com']"
        )
        if turnstile:
            box = await turnstile.bounding_box()
            if box:
                clicked = await _click_turnstile_pixel_region(
                    page,
                    box,
                    strategy="iframe_pixel_region",
                )
                if clicked:
                    return True

        container_box = await _locate_turnstile_container_box(page)
        if container_box:
            # Cloudflare's managed challenge often renders a 300x65 widget-like
            # box in the main document while the actual input remains hidden.
            # The interactive control sits in the left-side 24x24 visual
            # component, whose center was measured at roughly (0.068, 0.50)
            # inside the widget in the CTF target.
            clicked = await _click_turnstile_pixel_region(
                page,
                container_box,
                strategy="container_pixel_region",
            )
            if clicked:
                return True
    except Exception:
        return False
    return False


async def _should_attempt_turnstile_click(page, state: dict) -> bool:
    """Return whether the current challenge state has a visible widget worth clicking."""
    kind = state.get("kind") if isinstance(state, dict) else None
    if kind == "interactive":
        return True
    if kind == "waiting":
        return False
    if kind not in {"managed", "loading"}:
        return False
    container_box = await _locate_turnstile_container_box(page)
    return bool(
        await _collect_challenge_frame_boxes(page)
        or (container_box and _is_usable_turnstile_box(container_box))
    )


_TURNSTILE_PIXEL_TARGETS = (
    (0.07, 0.50),
    (0.08, 0.50),
    (0.09, 0.50),
    (0.10, 0.50),
    (0.06, 0.50),
    (0.11, 0.50),
    (0.08, 0.42),
    (0.08, 0.58),
)


def _turnstile_click_budget_exhausted(click_attempts: int) -> bool:
    return click_attempts >= len(_TURNSTILE_PIXEL_TARGETS)


def _turnstile_checkbox_target(box: dict) -> tuple[float, float]:
    """Target the left 24px checkbox even when its DOM box spans the label."""
    width = max(0.0, float(box.get("width", 0)))
    height = max(0.0, float(box.get("height", 0)))
    x = float(box.get("x", 0)) + min(width / 2, 12.0)
    y = float(box.get("y", 0)) + min(height / 2, 12.0)
    return x, y


def _challenge_return_url(url: str) -> str:
    """Remove transient Cloudflare challenge query parameters from a URL."""
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"__cf_chl_rt_tk", "__cf_chl_tk"}
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(query), ""))


async def _resume_after_clearance(page, target_url: str) -> None:
    """Actively leave CF's waiting page after a clearance cookie is issued."""
    if not target_url:
        return
    try:
        await page.goto(target_url, timeout=30_000, wait_until="domcontentloaded")
    except Exception as exc:
        logger.debug(f"Clearance resume navigation error (continuing): {exc}")
    await asyncio.sleep(2.0)


async def _wait_for_challenge_exit(page, *, timeout_seconds: float) -> bool:
    """Allow delayed Turnstile completion after the final target request."""
    elapsed = 0.0
    poll_interval = 2.0
    while elapsed <= timeout_seconds:
        state = await classify_cf_challenge(page)
        if not state["active"]:
            return True
        if elapsed >= timeout_seconds:
            break
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return False


async def _recover_after_challenge_timeout(page, target_url: str) -> bool:
    """Retry the target request that commits a just-completed CF challenge."""
    for _ in range(2):
        await _resume_after_clearance(page, target_url)
        if await _wait_for_challenge_exit(page, timeout_seconds=20):
            return True
    return False


async def _click_turnstile_pixel_region(page, box: dict, *, strategy: str) -> bool:
    """Click the measured visual checkbox region inside a Turnstile widget box."""
    if not _is_usable_turnstile_box(box):
        return False

    index = int(getattr(page, "_litefupzl_cf_pixel_target_index", 0))
    if index >= len(_TURNSTILE_PIXEL_TARGETS):
        return False
    x_ratio, y_ratio = _TURNSTILE_PIXEL_TARGETS[index % len(_TURNSTILE_PIXEL_TARGETS)]
    setattr(page, "_litefupzl_cf_pixel_target_index", index + 1)

    x = box["x"] + box["width"] * x_ratio
    y = box["y"] + box["height"] * y_ratio
    await page.mouse.click(x, y, delay=90)
    _record_turnstile_click(
        page,
        strategy=strategy,
        box=box,
        x=x,
        y=y,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
    )
    logger.debug(f"Clicked Turnstile visual checkbox region via {strategy}")
    return True


def _record_turnstile_click(
    page,
    *,
    strategy: str,
    box: dict,
    x: float,
    y: float,
    x_ratio: float | None = None,
    y_ratio: float | None = None,
) -> None:
    try:
        attempts = list(getattr(page, "_litefupzl_cf_click_attempts", []))
        event = {
            "strategy": strategy,
            "x": round(float(x), 2),
            "y": round(float(y), 2),
            "box": _sanitize_box(box),
        }
        if x_ratio is not None and y_ratio is not None:
            event["target_ratio"] = [round(float(x_ratio), 3), round(float(y_ratio), 3)]
        attempts.append(event)
        del attempts[:-30]
        setattr(page, "_litefupzl_cf_click_attempts", attempts)
    except Exception:
        return


async def _collect_challenge_frame_boxes(page) -> list[dict]:
    """Return visible challenge frame element boxes, including frames not queryable by DOM selectors."""
    boxes: list[dict] = []
    try:
        frames = getattr(page, "frames", [])
    except Exception:
        return boxes
    if not isinstance(frames, (list, tuple)):
        return boxes

    for frame in frames:
        try:
            url = str(getattr(frame, "url", ""))
        except Exception:
            continue
        if "challenges.cloudflare.com" not in url:
            continue
        try:
            frame_element = await frame.frame_element()
            box = await frame_element.bounding_box() if frame_element else None
        except Exception:
            continue
        if not _is_usable_turnstile_box(box):
            continue
        boxes.append({
            "source": "frame_element",
            "box": _sanitize_box(box),
        })
    return boxes


def _is_usable_turnstile_box(box) -> bool:
    if not isinstance(box, dict):
        return False
    try:
        width = float(box.get("width", 0))
        height = float(box.get("height", 0))
    except Exception:
        return False
    return 120 <= width <= 500 and 40 <= height <= 150


def _sanitize_box(box: dict) -> dict:
    return {
        "x": round(float(box.get("x", 0)), 2),
        "y": round(float(box.get("y", 0)), 2),
        "width": round(float(box.get("width", 0)), 2),
        "height": round(float(box.get("height", 0)), 2),
    }


async def _has_visible_turnstile_checkbox(page) -> bool:
    """Return True only when an actual Turnstile checkbox is visible."""
    try:
        frames = getattr(page, "frames", [])
        if not isinstance(frames, (list, tuple)):
            return False
        for frame in frames:
            if "challenges.cloudflare.com" not in getattr(frame, "url", ""):
                continue
            checkbox = await frame.query_selector("input[type='checkbox']")
            if not checkbox:
                continue
            box = await checkbox.bounding_box()
            if box and box.get("width", 0) >= 20 and box.get("height", 0) >= 20:
                return True
    except Exception:
        return False
    return False


async def _has_cf_clearance_for_page(page) -> bool:
    try:
        url = str(getattr(page, "url", ""))
    except Exception:
        url = ""
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc:
        base_url = f"{parsed.scheme}://{parsed.netloc}"
    else:
        base_url = url
    return await _has_cf_clearance(page, base_url)


async def _has_cf_clearance(page, base_url: str) -> bool:
    """Check whether Cloudflare has issued a clearance cookie."""
    try:
        cookies = await page.context.cookies([base_url])
    except Exception:
        return False
    if not isinstance(cookies, list):
        return False
    return any(cookie.get("name") == "cf_clearance" for cookie in cookies)


async def _human_like_mouse_click(page, x: float, y: float) -> None:
    """Move mouse with jitter, pause briefly, then click."""
    await page.mouse.move(x, y, steps=random.randint(10, 25))
    await asyncio.sleep(random.uniform(0.12, 0.35))
    try:
        await page.mouse.click(x, y, delay=random.randint(60, 140))
    except Exception as exc:
        # A successful challenge click can immediately navigate/recreate the
        # page, causing Playwright/Patchright to report a destroyed execution
        # context or detached target after the click was already delivered.
        if _is_transient_navigation_error(exc):
            logger.debug(f"Ignoring post-click navigation race: {type(exc).__name__}")
            return
        raise


def _is_transient_navigation_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "execution context was destroyed",
            "frame was detached",
            "target closed",
            "page closed",
            "browser has been closed",
            "navigation",
            "context destroyed",
        )
    )


async def _locate_turnstile_container_box(page) -> dict | None:
    """Locate a visible main-document challenge container near hidden turnstile input.

    Some challenge states expose only a hidden `cf-turnstile-response` input in
    the main document while the visible widget is rendered inside a nearby
    container. We walk upward from the hidden input and also try known wrapper
    selectors used on linux.do's CF waiting page.
    """
    try:
        box = await page.evaluate(
            """() => {
                const selectors = [
                  "input[name='cf-turnstile-response']",
                  "input[id*='cf-chl-widget']",
                ];
                const inputs = selectors.flatMap((selector) =>
                  Array.from(document.querySelectorAll(selector))
                );

                function rectObj(rect) {
                  return {
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                  };
                }

                function isUsable(rect) {
                  return rect.width >= 120 && rect.height >= 40;
                }

                const candidates = [];
                for (const input of inputs) {
                  let node = input.parentElement;
                  while (node) {
                    const rect = node.getBoundingClientRect();
                    const style = getComputedStyle(node);
                    if (
                      style.display !== "none" &&
                      style.visibility !== "hidden" &&
                      style.pointerEvents !== "none" &&
                      isUsable(rect)
                    ) {
                      candidates.push(rectObj(rect));
                    }
                    node = node.parentElement;
                  }
                }

                const fallbackSelectors = [
                  "#AOzYg6",
                  ".main-content",
                  ".main-wrapper",
                ];
                for (const selector of fallbackSelectors) {
                  const node = document.querySelector(selector);
                  if (!node) continue;
                  const rect = node.getBoundingClientRect();
                  const style = getComputedStyle(node);
                  if (
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    isUsable(rect)
                  ) {
                    candidates.push(rectObj(rect));
                  }
                }

                if (!candidates.length) {
                  return null;
                }
                candidates.sort((a, b) => (a.width * a.height) - (b.width * b.height));
                return candidates[0];
            }"""
        )
        return _sanitize_box(box) if _is_usable_turnstile_box(box) else None
    except Exception:
        return None
