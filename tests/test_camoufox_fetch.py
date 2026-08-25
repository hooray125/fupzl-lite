from io import BytesIO

import pytest

from apps.litefupzl.fetch_camoufox import (
    CAMOUFOX_BROWSER_SHA256,
    CAMOUFOX_BROWSER_SPEC,
    CAMOUFOX_BROWSER_VERSION,
    _authenticated_get,
    _verified_download,
)


def test_authenticated_camoufox_fetch_only_sends_token_to_github_api():
    calls = []

    def original_get(url, *args, **kwargs):
        calls.append((url, args, kwargs))
        return object()

    get = _authenticated_get(original_get, "redacted-token")
    get("https://api.github.com/repos/daijro/camoufox/releases", timeout=20)
    get("https://github.com/daijro/camoufox/releases/download/file.zip", stream=True)

    api_headers = calls[0][2]["headers"]
    assert api_headers["Authorization"] == "Bearer redacted-token"
    assert api_headers["Accept"] == "application/vnd.github+json"
    assert "headers" not in calls[1][2]


def test_camoufox_browser_asset_is_version_and_digest_pinned():
    assert CAMOUFOX_BROWSER_SPEC == (
        f"official/{CAMOUFOX_BROWSER_VERSION}-{CAMOUFOX_BROWSER_SHA256[:8]}"
    )
    assert len(CAMOUFOX_BROWSER_SHA256) == 64
    int(CAMOUFOX_BROWSER_SHA256, 16)


def test_verified_camoufox_download_rejects_payload_digest_mismatch():
    def original_download(file, _url):
        file.write(b"unexpected-browser-payload")
        file.seek(0)
        return file

    download = _verified_download(original_download, "0" * 64)

    with pytest.raises(RuntimeError, match="digest mismatch"):
        download(BytesIO(), "https://example.invalid/browser.zip")
