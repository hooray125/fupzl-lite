import json
import sys


def _command(*lines: str, sleep_seconds: float = 0.0, exit_code: int = 0) -> list[str]:
    source = ["import time"]
    source.extend(f"print({line!r}, flush=True)" for line in lines)
    if sleep_seconds:
        source.append(f"time.sleep({sleep_seconds!r})")
    source.append(f"raise SystemExit({exit_code})")
    return [sys.executable, "-c", ";".join(source)]


def test_watchdog_returns_child_exit_and_records_synced_phase(tmp_path, monkeypatch):
    from apps.litefupzl.watchdog import run_watchdog

    monkeypatch.setenv("LITEFUPZL_DURATION_MINUTES", "1")
    result = run_watchdog(
        command=_command(
            "2026-08-21T00:00:00Z | INFO | slot-001 login-prewarm started",
            "2026-08-21T00:00:01Z | INFO | slot-001 login-check ok",
            "2026-08-21T00:00:02Z | INFO | slot-001 cookie-refresh unchanged",
        ),
        output_dir=tmp_path,
        startup_seconds=1,
        pre_login_seconds=1,
        cookie_sync_seconds=1,
        post_login_grace_seconds=1,
        termination_grace_seconds=0.1,
        poll_seconds=0.01,
    )

    state = json.loads((tmp_path / "watchdog_state.json").read_text())
    assert result == 0
    assert state["phase"] == "authenticated_synced"
    assert state["timed_out"] is False


def test_watchdog_hard_kills_pre_auth_driver_hang(tmp_path):
    from apps.litefupzl.watchdog import run_watchdog

    result = run_watchdog(
        command=_command(
            "2026-08-21T00:00:00Z | INFO | slot-001 login-prewarm started",
            sleep_seconds=30,
        ),
        output_dir=tmp_path,
        startup_seconds=1,
        pre_login_seconds=0.15,
        cookie_sync_seconds=1,
        post_login_grace_seconds=1,
        termination_grace_seconds=0.1,
        poll_seconds=0.01,
    )

    state = json.loads((tmp_path / "watchdog_state.json").read_text())
    assert result == 124
    assert state["timeout_code"] == "BROWSER_DRIVER_HANG_PRE_AUTH"
    assert state["child_terminated"] is True


def test_watchdog_marks_cookie_state_uncertain_after_injection_hang(tmp_path):
    from apps.litefupzl.watchdog import run_watchdog

    result = run_watchdog(
        command=_command(
            "2026-08-21T00:00:00Z | INFO | slot-001 login-cookie-bootstrap started",
            sleep_seconds=30,
        ),
        output_dir=tmp_path,
        startup_seconds=1,
        pre_login_seconds=0.15,
        cookie_sync_seconds=1,
        post_login_grace_seconds=1,
        termination_grace_seconds=0.1,
        poll_seconds=0.01,
    )

    state = json.loads((tmp_path / "watchdog_state.json").read_text())
    assert result == 124
    assert state["timeout_code"] == "COOKIE_STATE_UNCERTAIN_DRIVER_HANG"
    assert state["phase"] == "cookie_injection_started"


def test_watchdog_bounds_cleanup_after_driver_crash(tmp_path):
    from apps.litefupzl.watchdog import run_watchdog

    result = run_watchdog(
        command=_command(
            "2026-08-21T00:00:00Z | INFO | slot-001 login-cookie-bootstrap started",
            "2026-08-21T00:00:01Z | ERROR | slot-001 browser-driver crashed BROWSER_DRIVER_CRASH",
            sleep_seconds=30,
        ),
        output_dir=tmp_path,
        startup_seconds=1,
        pre_login_seconds=1,
        cookie_sync_seconds=1,
        driver_cleanup_seconds=0.15,
        post_login_grace_seconds=1,
        termination_grace_seconds=0.1,
        poll_seconds=0.01,
    )

    state = json.loads((tmp_path / "watchdog_state.json").read_text())
    assert result == 124
    assert state["phase"] == "driver_crashed_cleanup"
    assert state["timeout_code"] == "COOKIE_STATE_UNCERTAIN_DRIVER_HANG"
