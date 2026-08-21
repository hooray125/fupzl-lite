"""Hard wall-clock watchdog for the credentialed oneshot child process.

Playwright RPC calls normally honor their own timeouts, but a wedged driver can
also wedge coroutine cancellation. The watchdog therefore owns the oneshot as
an OS process group and can terminate the Python/browser/Xvfb tree without
depending on the Playwright event loop.

Only public phase names and exit metadata are persisted. Child output is
passed through the repository redactor before it reaches stdout.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from litefupzl.oneshot.redaction import install_sensitive_output_guard, redact_text  # noqa: E402

_DEFAULT_STARTUP_SECONDS = 300.0
_DEFAULT_PRE_LOGIN_SECONDS = 600.0
_DEFAULT_COOKIE_SYNC_SECONDS = 120.0
_DEFAULT_DRIVER_CLEANUP_SECONDS = 60.0
_DEFAULT_POST_LOGIN_GRACE_SECONDS = 600.0
_DEFAULT_TERMINATION_GRACE_SECONDS = 10.0
_POLL_SECONDS = 0.2


@dataclass(slots=True)
class WatchdogState:
    started_at: str
    finished_at: str | None = None
    phase: str = "startup"
    slot: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    timeout_code: str | None = None
    child_terminated: bool = False
    cookie_injection_started: bool = False
    cookie_checkpoint_complete: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _duration_seconds() -> float:
    return 60.0 * _positive_float_env("LITEFUPZL_DURATION_MINUTES", 40.0)


def _phase_update(line: str) -> tuple[str, str | None] | None:
    fields = line.strip().split(" | ")
    message = fields[-1] if fields else line.strip()
    parts = message.split()
    slot = parts[0] if parts and parts[0].startswith("slot-") else None

    if "browser-driver-retry started" in message:
        return "driver_retry", slot
    if "browser-driver crashed" in message:
        return "driver_crashed_cleanup", slot
    if "login-prewarm started" in message:
        return "login_prewarm", slot
    if "login-cookie-bootstrap started" in message:
        return "cookie_injection_started", slot
    if "login-cookie-bootstrap finished" in message:
        return "cookie_injected", slot
    if "login-check ok" in message:
        return "authenticated_unsynced", slot
    if any(
        marker in message
        for marker in (
            "cookie-refresh ok",
            "cookie-refresh unchanged",
            "cookie-refresh skipped",
        )
    ):
        return "authenticated_synced", slot
    if slot and any(
        f"slot {status}" in message
        for status in (
            "success",
            "warning",
            "cf_blocked",
            "cookie_invalid",
            "account_blocked",
            "runtime_error",
            "failed",
        )
    ):
        return "slot_complete", slot
    return None


def _timeout_code(state: WatchdogState) -> str:
    if state.cookie_injection_started and not state.cookie_checkpoint_complete:
        return "COOKIE_STATE_UNCERTAIN_DRIVER_HANG"
    if not state.cookie_checkpoint_complete:
        return "BROWSER_DRIVER_HANG_PRE_AUTH"
    return "BROWSER_DRIVER_HANG_POST_AUTH"


def _write_state(output_dir: Path, state: WatchdogState) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "watchdog_state.json"
    temporary = output_dir / ".watchdog_state.json.tmp"
    temporary.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _reader(stream, events: queue.Queue[str | None]) -> None:
    try:
        for line in iter(stream.readline, ""):
            events.put(line)
    finally:
        events.put(None)


def _terminate_process_group(process: subprocess.Popen, grace_seconds: float) -> bool:
    if process.poll() is not None:
        return False
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=max(1.0, grace_seconds))
    return True


def run_watchdog(
    *,
    command: list[str] | None = None,
    output_dir: str | Path | None = None,
    startup_seconds: float | None = None,
    pre_login_seconds: float | None = None,
    cookie_sync_seconds: float | None = None,
    driver_cleanup_seconds: float | None = None,
    post_login_grace_seconds: float | None = None,
    termination_grace_seconds: float | None = None,
    poll_seconds: float = _POLL_SECONDS,
) -> int:
    """Run the oneshot child with phase-aware hard wall-clock limits."""
    command = command or [sys.executable, str(ROOT / "apps" / "litefupzl" / "main.py")]
    output_path = Path(output_dir or os.environ.get("LITEFUPZL_OUTPUT_DIR") or "output/litefupzl")
    startup_limit = startup_seconds or _positive_float_env(
        "LITEFUPZL_WATCHDOG_STARTUP_SECONDS", _DEFAULT_STARTUP_SECONDS
    )
    pre_login_limit = pre_login_seconds or _positive_float_env(
        "LITEFUPZL_WATCHDOG_PRE_LOGIN_SECONDS", _DEFAULT_PRE_LOGIN_SECONDS
    )
    cookie_sync_limit = cookie_sync_seconds or _positive_float_env(
        "LITEFUPZL_WATCHDOG_COOKIE_SYNC_SECONDS", _DEFAULT_COOKIE_SYNC_SECONDS
    )
    driver_cleanup_limit = driver_cleanup_seconds or _positive_float_env(
        "LITEFUPZL_WATCHDOG_DRIVER_CLEANUP_SECONDS", _DEFAULT_DRIVER_CLEANUP_SECONDS
    )
    post_login_grace = post_login_grace_seconds or _positive_float_env(
        "LITEFUPZL_WATCHDOG_POST_LOGIN_GRACE_SECONDS", _DEFAULT_POST_LOGIN_GRACE_SECONDS
    )
    termination_grace = termination_grace_seconds or _positive_float_env(
        "LITEFUPZL_WATCHDOG_TERMINATION_GRACE_SECONDS", _DEFAULT_TERMINATION_GRACE_SECONDS
    )

    state = WatchdogState(started_at=_now())
    _write_state(output_path, state)
    child = subprocess.Popen(
        command,
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )
    assert child.stdout is not None
    events: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_reader, args=(child.stdout, events), daemon=True).start()

    deadline = time.monotonic() + startup_limit
    stream_closed = False
    while True:
        try:
            line = events.get(timeout=max(0.01, poll_seconds))
        except queue.Empty:
            line = ""
        if line is None:
            stream_closed = True
        elif line:
            print(redact_text(line), end="", flush=True)
            update = _phase_update(line)
            if update is not None:
                state.phase, state.slot = update
                if state.phase in {"driver_retry", "login_prewarm"}:
                    state.cookie_injection_started = False
                    state.cookie_checkpoint_complete = False
                elif state.phase in {
                    "cookie_injection_started",
                    "cookie_injected",
                    "authenticated_unsynced",
                }:
                    state.cookie_injection_started = True
                elif state.phase == "authenticated_synced":
                    state.cookie_injection_started = True
                    state.cookie_checkpoint_complete = True
                now = time.monotonic()
                if state.phase in {
                    "driver_retry",
                    "login_prewarm",
                    "cookie_injection_started",
                    "cookie_injected",
                }:
                    deadline = now + pre_login_limit
                elif state.phase == "authenticated_unsynced":
                    deadline = now + cookie_sync_limit
                elif state.phase == "driver_crashed_cleanup":
                    deadline = now + driver_cleanup_limit
                elif state.phase == "authenticated_synced":
                    deadline = now + _duration_seconds() + post_login_grace
                else:
                    deadline = now + startup_limit
                _write_state(output_path, state)

        return_code = child.poll()
        if return_code is not None and stream_closed and events.empty():
            state.finished_at = _now()
            state.exit_code = return_code
            _write_state(output_path, state)
            return return_code

        if time.monotonic() <= deadline:
            continue

        state.timed_out = True
        state.timeout_code = _timeout_code(state)
        print(
            f"litefupzl watchdog hard-timeout {state.timeout_code} "
            f"phase={state.phase} slot={state.slot or 'none'}",
            flush=True,
        )
        state.child_terminated = _terminate_process_group(child, termination_grace)
        state.finished_at = _now()
        state.exit_code = 124
        _write_state(output_path, state)
        return 124


def main() -> int:
    install_sensitive_output_guard()
    return run_watchdog()


if __name__ == "__main__":
    raise SystemExit(main())
