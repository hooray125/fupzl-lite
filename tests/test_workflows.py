import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def test_gitignore_does_not_blanket_ignore_dotfiles_or_github_changes():
    patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".*" not in patterns


def test_workflows_support_variables_for_non_sensitive_config_but_keep_credentials_in_secrets():
    for filename in ("oneshot.yml", "probes.yml"):
        workflow = load_workflow(filename)
        job = "oneshot" if filename == "oneshot.yml" else "probe"
        env = workflow["jobs"][job]["env"]

        for key in ("LITEFUPZL_COOKIES_JSON", "LITEFUPZL_ACTIONS_ADMIN_TOKEN"):
            assert key not in env

        for key in ("LITEFUPZL_SITE", "LITEFUPZL_HEADLESS", "LITEFUPZL_OUTPUT_DIR", "LITEFUPZL_BROWSER"):
            expression = env[key]
            assert "vars." in expression
            assert "secrets." in expression
            assert expression.index("vars.") < expression.index("secrets.")

    oneshot = load_workflow("oneshot.yml")
    run_env = _step(oneshot, "oneshot", "Run oneshot")["env"]
    assert "secrets." in run_env["LITEFUPZL_COOKIES_JSON"]
    assert "vars." not in run_env["LITEFUPZL_COOKIES_JSON"]
    assert "secrets." in run_env["LITEFUPZL_ACTIONS_ADMIN_TOKEN"]
    assert "vars." not in run_env["LITEFUPZL_ACTIONS_ADMIN_TOKEN"]

    probes = load_workflow("probes.yml")
    authenticated_env = _step(probes, "probe", "Run authenticated probe")["env"]
    assert "secrets." in authenticated_env["LITEFUPZL_COOKIES_JSON"]
    assert "LITEFUPZL_ACTIONS_ADMIN_TOKEN" not in authenticated_env
    assert "env" not in _step(probes, "probe", "Run unauthenticated CF probe")


def test_sensitive_credentials_are_not_exposed_to_install_or_cleanup_steps():
    for filename, job in [("oneshot.yml", "oneshot"), ("probes.yml", "probe")]:
        workflow = load_workflow(filename)
        install = _step(workflow, job, "Install dependencies")
        cleanup = _step(workflow, job, "Cleanup old workflow runs")

        for key in ("LITEFUPZL_COOKIES_JSON", "LITEFUPZL_ACTIONS_ADMIN_TOKEN"):
            assert key not in workflow["jobs"][job]["env"]
            assert key not in install.get("env", {})
        assert cleanup["env"]["ACTIONS_CLEANUP_TOKEN"] == "${{ github.token }}"


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _step(workflow: dict, job: str, name: str) -> dict:
    matches = [step for step in _steps(workflow, job) if step.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def test_artifacts_upload_is_manual_opt_in_for_both_workflows():
    for filename, job in [("oneshot.yml", "oneshot"), ("probes.yml", "probe")]:
        workflow = load_workflow(filename)
        inputs = workflow[True]["workflow_dispatch"]["inputs"]
        assert inputs["upload_artifacts"]["default"] == "false"
        upload = _step(workflow, job, "Upload redacted artifacts")
        assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
        condition = upload["if"]
        assert "github.event_name == 'workflow_dispatch'" in condition
        assert "inputs.upload_artifacts == 'true'" in condition
        assert condition.strip() != "always()"
        assert "${{ env.LITEFUPZL_OUTPUT_DIR }}/" in upload["with"]["path"]
        assert "output/phase3" not in upload["with"]["path"]


def test_oneshot_workflow_removed_write_action_env_and_supports_refresh_dual_stack():
    workflow = load_workflow("oneshot.yml")
    assert workflow["concurrency"] == {
        "group": "litefupzl-oneshot",
        "cancel-in-progress": False,
    }
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["cookie_refresh_enabled"]["default"] == "true"
    env = workflow["jobs"]["oneshot"]["env"]
    forbidden_env = [key for key in env if "LOTTERY" in key]
    assert forbidden_env == []
    assert "LITEFUPZL_MUTUAL_LIKE_USERS_JSON" in env
    assert "vars.LITEFUPZL_MUTUAL_LIKE_USERS_JSON" in env["LITEFUPZL_MUTUAL_LIKE_USERS_JSON"]
    assert "secrets.LITEFUPZL_MUTUAL_LIKE_USERS_JSON" in env["LITEFUPZL_MUTUAL_LIKE_USERS_JSON"]
    assert "vars.LITEFUPZL_DURATION_MINUTES" in env["LITEFUPZL_DURATION_MINUTES"]
    assert "secrets.LITEFUPZL_DURATION_MINUTES" in env["LITEFUPZL_DURATION_MINUTES"]
    assert "secrets.FUCKPZL_ONESHOT_DURATION_MINUTES" in env["LITEFUPZL_DURATION_MINUTES"]
    refresh_expr = env["LITEFUPZL_COOKIE_REFRESH_ENABLED"]
    assert "github.event_name == 'workflow_dispatch'" in refresh_expr
    assert "inputs.cookie_refresh_enabled" in refresh_expr
    assert "vars.LITEFUPZL_COOKIE_REFRESH_ENABLED" in refresh_expr
    assert "secrets.LITEFUPZL_COOKIE_REFRESH_ENABLED" in refresh_expr
    assert "secrets.FUCKPZL_ONESHOT_COOKIE_REFRESH_ENABLED" in refresh_expr
    assert "|| 'true'" in refresh_expr
    assert "inputs.cookie_refresh_enabled == 'true' && 'true' || 'false'" not in refresh_expr
    assert "|| 'camoufox'" in env["LITEFUPZL_BROWSER"]


def test_oneshot_manual_cookie_refresh_string_false_precedes_repository_config():
    workflow = load_workflow("oneshot.yml")
    input_config = workflow[True]["workflow_dispatch"]["inputs"]["cookie_refresh_enabled"]
    refresh_expr = workflow["jobs"]["oneshot"]["env"]["LITEFUPZL_COOKIE_REFRESH_ENABLED"]

    # Untyped workflow_dispatch inputs are strings, so the non-empty string
    # "false" wins the selector before any repository Variable or Secret.
    assert input_config.get("type", "string") == "string"
    assert refresh_expr.startswith(
        "${{ github.event_name == 'workflow_dispatch' && inputs.cookie_refresh_enabled || "
    )
    ordered_sources = [
        "inputs.cookie_refresh_enabled",
        "vars.LITEFUPZL_COOKIE_REFRESH_ENABLED",
        "vars.FUCKPZL_ONESHOT_COOKIE_REFRESH_ENABLED",
        "secrets.LITEFUPZL_COOKIE_REFRESH_ENABLED",
        "secrets.FUCKPZL_ONESHOT_COOKIE_REFRESH_ENABLED",
        "|| 'true'",
    ]
    positions = [refresh_expr.index(source) for source in ordered_sources]
    assert positions == sorted(positions)
    assert "inputs.cookie_refresh_enabled == 'true'" not in refresh_expr


def test_oneshot_topic_quota_defaults_match_two_daily_schedules():
    raw = (ROOT / ".github" / "workflows" / "oneshot.yml").read_text(encoding="utf-8")
    workflow = load_workflow("oneshot.yml")
    env = workflow["jobs"]["oneshot"]["env"]

    assert raw.count("    - cron:") == 2
    assert "|| '500'" in env["LITEFUPZL_MONTHLY_TOPIC_TARGET"]
    assert "|| '2'" in env["LITEFUPZL_SCHEDULE_RUNS_PER_DAY"]
    assert "|| '7'" in env["LITEFUPZL_TOPIC_PREFETCH_PAGES"]
    assert "|| '10'" in env["LITEFUPZL_TOPIC_PREFETCH_MAX_PAGES"]
    for key in (
        "LITEFUPZL_MONTHLY_TOPIC_TARGET",
        "LITEFUPZL_SCHEDULE_RUNS_PER_DAY",
        "LITEFUPZL_TOPIC_PREFETCH_PAGES",
        "LITEFUPZL_TOPIC_PREFETCH_MAX_PAGES",
    ):
        assert "vars." in env[key]
        assert "secrets." in env[key]
        assert env[key].index("vars.") < env[key].index("secrets.")


def test_probe_workflow_never_refreshes_cookies():
    workflow = load_workflow("probes.yml")
    env = workflow["jobs"]["probe"]["env"]
    assert env["LITEFUPZL_COOKIE_REFRESH_ENABLED"] == "false"
    assert "|| 'camoufox'" in env["LITEFUPZL_BROWSER"]
    assert "|| 'https://linux.do/challenge'" in env["LITEFUPZL_CF_PROBE_URL"]


def test_camoufox_install_uses_authenticated_github_api_in_both_workflows():
    for filename in ("oneshot.yml", "probes.yml"):
        raw = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        assert 'GITHUB_TOKEN="${{ github.token }}" python apps/litefupzl/fetch_camoufox.py' in raw
        assert "python -m camoufox fetch" not in raw


def test_actions_and_python_dependencies_are_immutable_in_workflows():
    lock_text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert "--hash=sha256:" in lock_text
    assert ">=" not in lock_text

    for filename, job in [("oneshot.yml", "oneshot"), ("probes.yml", "probe")]:
        workflow = load_workflow(filename)
        for step in _steps(workflow, job):
            if "uses" in step:
                assert re.fullmatch(r"actions/[A-Za-z0-9_.-]+@[0-9a-f]{40}", step["uses"])
        install = _step(workflow, job, "Install dependencies")["run"]
        assert "python -m pip install --require-hashes --only-binary=:all: -r requirements.lock" in install
        assert "python -m pip install --no-build-isolation --no-deps -e ." in install
        assert "pip install --upgrade" not in install
        assert 'pip install -e ".[test]"' not in install


def test_cleanup_old_workflow_runs_keeps_pagination_and_retention():
    for filename, job in [("oneshot.yml", "oneshot"), ("probes.yml", "probe")]:
        workflow = load_workflow(filename)
        cleanup = _step(workflow, job, "Cleanup old workflow runs")
        retention_expr = cleanup["env"]["ACTIONS_RUNS_KEEP"]
        assert "vars.LITEFUPZL_ACTIONS_RUNS_KEEP" in retention_expr
        assert "secrets.LITEFUPZL_ACTIONS_RUNS_KEEP" in retention_expr
        assert retention_expr.index("vars.") < retention_expr.index("secrets.")
        script = cleanup["run"]
        assert "per_page" in script and "100" in script
        assert "page += 1" in script
        assert "all_runs.extend" in script
        assert "cleanup_runs_seen=" in script


def test_workflows_install_selected_browser_backend():
    for filename, job in [("oneshot.yml", "oneshot"), ("probes.yml", "probe")]:
        workflow = load_workflow(filename)
        install = _step(workflow, job, "Install dependencies")
        script = install["run"]
        assert "LITEFUPZL_BROWSER" in script
        assert "patchright install --with-deps chromium" in script
        assert "camoufox)" in script
        assert "python apps/litefupzl/fetch_camoufox.py" in script
        assert "playwright install-deps firefox" in script
        assert "playwright install --with-deps firefox" in script
        assert "playwright install --with-deps chromium" in script


def test_oneshot_workflow_does_not_run_unsafe_failure_auth_probe():
    workflow = load_workflow("oneshot.yml")
    run_oneshot = _step(workflow, "oneshot", "Run oneshot")

    assert run_oneshot["id"] == "run_oneshot"
    assert not any(step.get("name") == "Run auth probe" for step in _steps(workflow, "oneshot"))
    assert "auth_probe.py" not in run_oneshot["run"]


def test_driver_matrix_is_unauthenticated_and_covers_runner_browser_controls():
    workflow = load_workflow("probes.yml")
    assert "driver-matrix" in workflow[True]["workflow_dispatch"]["inputs"]["probe"]["options"]

    job = workflow["jobs"]["driver-matrix"]
    assert job["if"] == "inputs.probe == 'driver-matrix'"
    assert job["strategy"]["fail-fast"] is False
    entries = job["strategy"]["matrix"]["include"]
    combinations = [(entry["os"], entry["browser"]) for entry in entries]
    assert combinations.count(("ubuntu-24.04", "camoufox")) == 3
    assert combinations.count(("ubuntu-22.04", "camoufox")) == 3
    assert combinations.count(("ubuntu-24.04", "firefox")) == 2
    assert combinations.count(("ubuntu-24.04", "patchright-chromium")) == 2

    matrix_text = str(job)
    assert "LITEFUPZL_COOKIES_JSON" not in matrix_text
    assert "LITEFUPZL_ACTIONS_ADMIN_TOKEN" not in matrix_text
    assert "cf_probe.py" in matrix_text
    assert "driver_crash_detected" in matrix_text


def test_manual_oneshot_supports_browser_and_runner_matrix_overrides():
    workflow = load_workflow("oneshot.yml")
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["browser_backend"]["options"] == ["configured", "camoufox", "firefox", "patchright-chromium"]
    assert inputs["runner_os"]["options"] == ["ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"]
    job = workflow["jobs"]["oneshot"]
    assert "inputs.runner_os" in job["runs-on"]
    assert "inputs.browser_backend" in job["env"]["LITEFUPZL_BROWSER"]
