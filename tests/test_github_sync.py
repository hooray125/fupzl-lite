import json

import pytest


class _CookieContext:
    async def cookies(self, urls):
        assert urls == ["https://linux.do"]
        return [
            {"name": "_t", "value": "new-token", "domain": "linux.do", "path": "/"},
            {"name": "cf_clearance", "value": "volatile", "domain": "linux.do", "path": "/"},
        ]


@pytest.mark.asyncio
async def test_cookie_refresh_persists_latest_context_token_and_updates_slot_source(monkeypatch):
    from litefupzl.oneshot import github_sync

    writes = []

    def fake_update_repository_secret(**kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(github_sync, "update_repository_secret", fake_update_repository_secret)
    cookie_strings = ["_t=old-token", "_t=second-token"]

    refreshed = await github_sync.refresh_slot_cookie_secret_from_context(
        _CookieContext(),
        original_cookie_string=cookie_strings[0],
        cookie_strings=cookie_strings,
        slot_index=1,
        admin_token="redacted",
        repository="owner/repo",
        base_url="https://linux.do",
    )

    assert refreshed is True
    assert json.loads(writes[0]["secret_value"]) == ["_t=new-token", "_t=second-token"]
    assert cookie_strings == ["_t=new-token", "_t=second-token"]


@pytest.mark.asyncio
async def test_failed_cookie_validation_does_not_mutate_slot_source(monkeypatch):
    from litefupzl.oneshot import github_sync

    async def reject(_cookie_string):
        return False

    def fail_if_written(**_kwargs):  # pragma: no cover - must not run
        raise AssertionError("rejected cookie must not be persisted")

    monkeypatch.setattr(github_sync, "update_repository_secret", fail_if_written)
    cookie_strings = ["_t=old-token"]

    with pytest.raises(github_sync.CookieRefreshError, match="COOKIE_REFRESH_VALIDATION_FAILED"):
        await github_sync.refresh_slot_cookie_secret_from_context(
            _CookieContext(),
            original_cookie_string=cookie_strings[0],
            cookie_strings=cookie_strings,
            slot_index=1,
            admin_token="redacted",
            repository="owner/repo",
            base_url="https://linux.do",
            validate_cookie_string=reject,
        )

    assert cookie_strings == ["_t=old-token"]
