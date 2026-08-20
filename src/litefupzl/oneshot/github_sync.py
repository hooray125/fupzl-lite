"""GitHub secret synchronization helpers for litefupzl oneshot mode."""

from __future__ import annotations

import asyncio
import base64
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nacl import encoding, public

from litefupzl.utils import parse_cookies, normalize_cookie_value


class CookieRefreshError(RuntimeError):
    """Safe, non-sensitive cookie refresh failure."""

    def __init__(self, safe_code: str):
        super().__init__(safe_code)
        self.safe_code = safe_code


COOKIE_SECRET_NAME = "LITEFUPZL_COOKIES_JSON"
VOLATILE_COOKIE_NAMES = {"_forum_session", "cf_clearance"}


def _cookie_string_equivalent(left: str, right: str) -> bool:
    """Compare cookie strings by name/value without depending on ordering."""
    left_items = {
        cookie.get("name"): cookie.get("value")
        for cookie in parse_cookies(left)
        if cookie.get("name")
    }
    right_items = {
        cookie.get("name"): cookie.get("value")
        for cookie in parse_cookies(right)
        if cookie.get("name")
    }
    return left_items == right_items


def build_refresh_cookie_string(browser_cookies: list[dict], original_cookie_string: str) -> str | None:
    """Build a persisted cookie string from browser cookies.

    Persist only cookies that the operator originally supplied. The durable
    Linux.do login credential is `_t`; browser-issued `_forum_session` and
    `cf_clearance` are session/environment-bound and can poison subsequent
    stateless GitHub Action runs when written back.
    """
    original_cookies = parse_cookies(original_cookie_string)

    lookup: dict[str, str] = {}
    for cookie in browser_cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        lookup[str(name)] = str(value)

    parts = []
    for original_cookie in original_cookies:
        name = original_cookie.get("name")
        if not name:
            continue
        if name in VOLATILE_COOKIE_NAMES:
            continue
        value = lookup.get(name, original_cookie.get("value"))
        if value is None:
            continue
        parts.append(f"{name}={normalize_cookie_value(str(value))}")
    return "; ".join(parts) if parts else None


async def extract_refresh_cookie_string_from_context(
    context,
    *,
    original_cookie_string: str,
    base_url: str,
) -> str | None:
    try:
        cookies = await context.cookies([base_url])
    except Exception as exc:
        raise CookieRefreshError(f"COOKIE_REFRESH_CONTEXT_{type(exc).__name__.upper()}") from exc
    return build_refresh_cookie_string(cookies, original_cookie_string)


def update_repository_secret(
    *,
    admin_token: str,
    repository: str,
    secret_name: str,
    secret_value: str,
    api_url: str = "https://api.github.com",
) -> None:
    try:
        owner, repo = repository.split("/", 1)
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "litefupzl",
            "Content-Type": "application/json",
        }

        req = Request(
            f"{api_url}/repos/{owner}/{repo}/actions/secrets/public-key",
            headers=headers,
        )
        with urlopen(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        public_key = public.PublicKey(payload["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        body = json.dumps(
            {
                "encrypted_value": base64.b64encode(encrypted).decode("utf-8"),
                "key_id": payload["key_id"],
            }
        ).encode("utf-8")

        req = Request(
            f"{api_url}/repos/{owner}/{repo}/actions/secrets/{secret_name}",
            data=body,
            headers=headers,
            method="PUT",
        )
        with urlopen(req):
            return None
    except HTTPError as exc:
        raise CookieRefreshError(f"COOKIE_REFRESH_HTTP_{exc.code}") from exc
    except Exception as exc:
        raise CookieRefreshError(f"COOKIE_REFRESH_{type(exc).__name__.upper()}") from exc


async def refresh_slot_cookie_secret_from_context(
    context,
    *,
    original_cookie_string: str,
    cookie_strings: list[str],
    slot_index: int,
    admin_token: str,
    repository: str,
    api_url: str = "https://api.github.com",
    secret_name: str = COOKIE_SECRET_NAME,
    base_url: str,
    validate_cookie_string=None,
) -> bool:
    try:
        refreshed_cookie_string = await extract_refresh_cookie_string_from_context(
            context,
            original_cookie_string=original_cookie_string,
            base_url=base_url,
        )
        if not refreshed_cookie_string:
            raise CookieRefreshError("COOKIE_REFRESH_EMPTY")
        if _cookie_string_equivalent(refreshed_cookie_string, original_cookie_string):
            return False
        if validate_cookie_string is not None:
            valid = await validate_cookie_string(refreshed_cookie_string)
            if not valid:
                raise CookieRefreshError("COOKIE_REFRESH_VALIDATION_FAILED")

        updated_cookie_strings = list(cookie_strings)
        updated_cookie_strings[slot_index - 1] = refreshed_cookie_string
        await asyncio.to_thread(
            update_repository_secret,
            admin_token=admin_token,
            repository=repository,
            secret_name=secret_name,
            secret_value=json.dumps(updated_cookie_strings),
            api_url=api_url,
        )
        # Slots execute sequentially. Keep the in-memory source list in sync so
        # a later slot cannot overwrite an earlier slot's refreshed token with
        # the stale value captured at workflow startup.
        cookie_strings[slot_index - 1] = refreshed_cookie_string
        return True
    except CookieRefreshError:
        raise
    except Exception as exc:
        raise CookieRefreshError(f"COOKIE_REFRESH_{type(exc).__name__.upper()}") from exc
