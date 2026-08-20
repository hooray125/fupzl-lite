import json
import os

import pytest


def test_env_loader_requires_only_cookie_and_no_like_lottery(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.delenv("LITEFUPZL_COOKIE_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("FUCKPZL_ONESHOT_COOKIE_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("FUCKPZL_ONESHOT_LOTTERY_TEXTS_JSON", raising=False)
    monkeypatch.delenv("FUCKPZL_ONESHOT_LOTTERY_EMOJIS_JSON", raising=False)
    monkeypatch.delenv("FUCKPZL_ONESHOT_LIKE_ENABLED", raising=False)
    monkeypatch.delenv("FUCKPZL_ONESHOT_LOTTERY_ENABLED", raising=False)

    config = load_oneshot_env()

    assert config.cookies == ["_t=redacted"]
    assert config.duration_minutes == 40
    assert config.cookie_refresh_enabled is True
    assert config.browser_name == "camoufox"
    assert config.monthly_topic_target == 500
    assert config.schedule_runs_per_day == 2
    assert config.new_topic_target_per_run == 9
    assert config.topic_prefetch_pages == 7
    assert config.topic_prefetch_max_pages == 10
    assert not hasattr(config, "like_enabled")
    assert not hasattr(config, "lottery_enabled")
    assert not hasattr(config, "lottery_texts")
    assert not hasattr(config, "lottery_emojis")


def test_scheduled_cookie_refresh_accepts_explicit_true(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.setenv("LITEFUPZL_COOKIE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")

    config = load_oneshot_env()

    assert config.cookie_refresh_enabled is True


def test_manual_cookie_refresh_accepts_explicit_true(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.setenv("LITEFUPZL_COOKIE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")

    config = load_oneshot_env()

    assert config.cookie_refresh_enabled is True


def test_cookie_refresh_defaults_to_enabled_for_schedule(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.delenv("LITEFUPZL_COOKIE_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("FUCKPZL_ONESHOT_COOKIE_REFRESH_ENABLED", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")

    config = load_oneshot_env()

    assert config.cookie_refresh_enabled is True


def test_cookie_refresh_schema_default_matches_loader_default():
    from litefupzl.oneshot.env_schema import OneShotEnvConfig

    config = OneShotEnvConfig(cookies=["_t=redacted"])

    assert config.cookie_refresh_enabled is True


def test_loader_scrubs_cookie_and_admin_token_from_child_process_environment(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted-cookie-value"]))
    monkeypatch.setenv("LITEFUPZL_ACTIONS_ADMIN_TOKEN", "redacted-admin-token")

    config = load_oneshot_env()

    assert config.cookies == ["_t=redacted-cookie-value"]
    assert config.actions_admin_token == "redacted-admin-token"
    assert "LITEFUPZL_COOKIES_JSON" not in os.environ
    assert "LITEFUPZL_ACTIONS_ADMIN_TOKEN" not in os.environ
    assert "actions_admin_token" not in config.model_dump()
    assert "redacted-admin-token" not in repr(config)


def test_cf_probe_loader_requires_no_cookie_and_scrubs_any_inherited_credentials(monkeypatch):
    from litefupzl.oneshot.env_loader import load_cf_probe_env

    monkeypatch.delenv("LITEFUPZL_COOKIES_JSON", raising=False)
    monkeypatch.delenv("FUCKPZL_ONESHOT_COOKIES_JSON", raising=False)
    monkeypatch.setenv("LITEFUPZL_ACTIONS_ADMIN_TOKEN", "redacted-unused-admin-token")
    monkeypatch.setenv("LITEFUPZL_BROWSER", "camoufox")

    config = load_cf_probe_env()

    assert config.browser_name == "camoufox"
    assert not hasattr(config, "cookies")
    assert "LITEFUPZL_COOKIES_JSON" not in os.environ
    assert "LITEFUPZL_ACTIONS_ADMIN_TOKEN" not in os.environ


def test_cookie_refresh_can_still_be_disabled_explicitly(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.setenv("LITEFUPZL_COOKIE_REFRESH_ENABLED", "false")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")

    config = load_oneshot_env()

    assert config.cookie_refresh_enabled is False


def test_legacy_cookie_env_alias_is_still_supported(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.delenv("LITEFUPZL_COOKIES_JSON", raising=False)
    monkeypatch.setenv("FUCKPZL_ONESHOT_COOKIES_JSON", json.dumps(["_t=legacy-redacted"]))

    config = load_oneshot_env()

    assert config.cookies == ["_t=legacy-redacted"]


def test_mutual_like_users_default_to_empty_when_unset(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.delenv("LITEFUPZL_MUTUAL_LIKE_USERS_JSON", raising=False)

    config = load_oneshot_env()

    assert config.mutual_like_users == []


def test_mutual_like_users_are_deduped_and_blank_values_removed(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.setenv("LITEFUPZL_MUTUAL_LIKE_USERS_JSON", json.dumps([" alice ", "", "bob", "alice"]))

    config = load_oneshot_env()

    assert config.mutual_like_users == ["alice", "bob"]


def test_mutual_like_users_malformed_json_skips_feature(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.setenv("LITEFUPZL_MUTUAL_LIKE_USERS_JSON", "not-json")

    config = load_oneshot_env()

    assert config.mutual_like_users == []


def test_new_topic_target_supports_overrides_and_legacy_aliases(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.setenv("LITEFUPZL_MONTHLY_TOPIC_TARGET", "720")
    monkeypatch.setenv("FUCKPZL_ONESHOT_SCHEDULE_RUNS_PER_DAY", "3")
    monkeypatch.setenv("LITEFUPZL_TOPIC_PREFETCH_PAGES", "8")
    monkeypatch.setenv("FUCKPZL_ONESHOT_TOPIC_PREFETCH_MAX_PAGES", "10")

    config = load_oneshot_env()

    assert config.new_topic_target_per_run == 8
    assert config.topic_prefetch_pages == 8
    assert config.topic_prefetch_max_pages == 10


def test_new_topic_target_rejects_reversed_prefetch_range(monkeypatch):
    from litefupzl.oneshot.env_loader import load_oneshot_env

    monkeypatch.setenv("LITEFUPZL_COOKIES_JSON", json.dumps(["_t=redacted"]))
    monkeypatch.setenv("LITEFUPZL_TOPIC_PREFETCH_PAGES", "9")
    monkeypatch.setenv("LITEFUPZL_TOPIC_PREFETCH_MAX_PAGES", "8")

    with pytest.raises(ValueError, match="topic_prefetch_max_pages"):
        load_oneshot_env()
