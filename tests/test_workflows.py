import pathlib

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
            expression = env[key]
            assert "secrets." in expression
            assert "vars." not in expression

        for key in ("LITEFUPZL_SITE", "LITEFUPZL_HEADLESS", "LITEFUPZL_OUTPUT_DIR", "LITEFUPZL_BROWSER"):
            expression = env[key]
            assert "vars." in expression
            assert "secrets." in expression
            assert expression.index("vars.") < expression.index("secrets.")


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
        assert upload["uses"] == "actions/upload-artifact@v4"
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
    assert inputs["cookie_refresh_enabled"]["default"] == "false"
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
    assert "|| 'false'" in refresh_expr
    assert "inputs.cookie_refresh_enabled == 'true' && 'true' || 'false'" not in refresh_expr
    assert "|| 'camoufox'" in env["LITEFUPZL_BROWSER"]


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
        assert "camoufox fetch" in script
        assert "playwright install-deps firefox" in script
        assert "playwright install --with-deps firefox" in script
        assert "playwright install --with-deps chromium" in script


def test_oneshot_workflow_runs_auth_probe_only_after_oneshot_failure():
    workflow = load_workflow("oneshot.yml")
    run_oneshot = _step(workflow, "oneshot", "Run oneshot")
    auth_probe = _step(workflow, "oneshot", "Run auth probe")

    assert run_oneshot["id"] == "run_oneshot"
    assert "always()" not in auth_probe["if"]
    assert "steps.run_oneshot.outcome == 'failure'" in auth_probe["if"]
    assert "failure()" in auth_probe["if"]
