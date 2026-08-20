"""Fetch Camoufox binaries without using GitHub's anonymous API quota."""

from __future__ import annotations

import os
from collections.abc import Callable

import requests
from camoufox.__main__ import cli


def _authenticated_get(original_get: Callable, token: str | None) -> Callable:
    def get(url, *args, **kwargs):
        if token and str(url).startswith("https://api.github.com/"):
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("Authorization", f"Bearer {token}")
            headers.setdefault("Accept", "application/vnd.github+json")
            kwargs["headers"] = headers
        return original_get(url, *args, **kwargs)

    return get


def main() -> None:
    original_get = requests.get
    requests.get = _authenticated_get(original_get, os.environ.get("GITHUB_TOKEN"))
    try:
        cli.main(args=["fetch"], prog_name="camoufox", standalone_mode=False)
    finally:
        requests.get = original_get


if __name__ == "__main__":
    main()
