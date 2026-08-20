def test_http_impersonation_tracks_browser_family():
    from litefupzl.discourse.http_bypass import _impersonate_for_user_agent

    assert _impersonate_for_user_agent("Mozilla/5.0 Firefox/152.0") == "firefox"
    assert _impersonate_for_user_agent("Mozilla/5.0 Chrome/151.0 Safari/537.36") == "chrome"
    assert _impersonate_for_user_agent("Mozilla/5.0 Version/18.0 Safari/605.1.15") == "safari"
    assert _impersonate_for_user_agent(None) == "firefox"
