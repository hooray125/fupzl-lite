"""Fetch Camoufox binaries without using GitHub's anonymous API quota."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable

import requests
from camoufox.__main__ import cli
from camoufox.multiversion import get_active_path
from camoufox.pkgman import CamoufoxFetcher


CAMOUFOX_BROWSER_VERSION = "152.0.4-beta.29"
CAMOUFOX_BROWSER_SHA256 = "1bea4b55a51c88e82dc7d426d9c75093d942d2afc8c911cb8fc78ebf723d686c"
CAMOUFOX_BROWSER_SPEC = f"official/{CAMOUFOX_BROWSER_VERSION}-{CAMOUFOX_BROWSER_SHA256[:8]}"


def _authenticated_get(original_get: Callable, token: str | None) -> Callable:
    def get(url, *args, **kwargs):
        if token and str(url).startswith("https://api.github.com/"):
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("Authorization", f"Bearer {token}")
            headers.setdefault("Accept", "application/vnd.github+json")
            kwargs["headers"] = headers
        return original_get(url, *args, **kwargs)

    return get


def _verified_download(original_download: Callable, expected_sha256: str) -> Callable:
    """Wrap Camoufox's downloader with a mandatory payload digest check."""

    def download(file, url):
        result = original_download(file, url)
        result.seek(0)
        digest = hashlib.file_digest(result, "sha256").hexdigest()
        result.seek(0)
        if digest != expected_sha256:
            raise RuntimeError("Camoufox browser asset digest mismatch")
        return result

    return download


def _verify_active_install() -> None:
    active_path = get_active_path()
    if active_path is None:
        raise RuntimeError("Pinned Camoufox browser was not activated")
    metadata = json.loads((active_path / "version.json").read_text(encoding="utf-8"))
    installed_version = f"{metadata.get('version')}-{metadata.get('build')}"
    if installed_version != CAMOUFOX_BROWSER_VERSION:
        raise RuntimeError("Unexpected Camoufox browser version activated")
    if metadata.get("sha256") != CAMOUFOX_BROWSER_SHA256:
        raise RuntimeError("Unexpected Camoufox browser digest metadata")


def main() -> None:
    original_get = requests.get
    original_download = CamoufoxFetcher.download_file
    requests.get = _authenticated_get(original_get, os.environ.get("GITHUB_TOKEN"))
    CamoufoxFetcher.download_file = staticmethod(
        _verified_download(original_download, CAMOUFOX_BROWSER_SHA256)
    )
    try:
        cli.main(
            args=["fetch", CAMOUFOX_BROWSER_SPEC],
            prog_name="camoufox",
            standalone_mode=False,
        )
        _verify_active_install()
    finally:
        requests.get = original_get
        CamoufoxFetcher.download_file = staticmethod(original_download)


if __name__ == "__main__":
    main()
