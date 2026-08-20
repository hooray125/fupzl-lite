from apps.litefupzl.fetch_camoufox import _authenticated_get


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
