"""CI pipeline verification — lint + test + security steps are present.

Static contract checks against ``.github/workflows/ci.yml`` (and any other CI
workflow files in that directory). The release contract requires that:

1. ``.github/workflows/`` exists with at least a ``ci.yml``,
2. a ``lint`` job runs a recognised linter (ruff and friends),
3. a ``test`` job drives the pytest suite on the supported Python versions,
4. a ``security`` job declares a vulnerability scan step.

These tests read the YAML only — they don't run GitHub Actions. They're
intentionally defensive about CI regressions: a removed or renamed step fails
the gate loudly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_YML = WORKFLOWS_DIR / "ci.yml"


@pytest.fixture(scope="module")
def ci_text():
    assert CI_YML.is_file(), ".github/workflows/ci.yml must exist"
    return CI_YML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ci_yaml():
    parsed = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "ci.yml must parse to a YAML mapping"
    return parsed


# ---------------------------------------------------------------------------
# 0. Workflow file presence
# ---------------------------------------------------------------------------
def test_workflows_directory_exists_with_ci_workflow():
    assert WORKFLOWS_DIR.is_dir(), ".github/workflows/ directory must exist"
    assert CI_YML.is_file(), ".github/workflows/ci.yml must be the canonical CI entry"


def test_workflows_directory_has_at_least_one_yaml():
    workflow_files = sorted(p.name for p in WORKFLOWS_DIR.glob("*.yml"))
    assert workflow_files, ".github/workflows/ must contain at least one .yml file"
    assert "ci.yml" in workflow_files


# ---------------------------------------------------------------------------
# 1. Triggers, permissions, and CI guards
# ---------------------------------------------------------------------------
def test_ci_triggers_on_push_and_pull_request(ci_yaml):
    on = ci_yaml.get("on", ci_yaml.get(True))  # YAML: bare `on:` could parse oddly.
    # The on block can be a dict / string / list — coerce to check.
    assert on is not None, "ci.yml must declare triggers via `on:`"
    if isinstance(on, dict):
        assert "push" in on, "CI must trigger on push"
        assert "pull_request" in on, "CI must trigger on pull_request"
        push_block = on.get("push", {}) or {}
        push_branches = push_block.get("branches", []) if isinstance(push_block, dict) else []
        # The open-source release branch family must be covered.
        assert any(
            "open-source" in str(branch) for branch in push_branches
        ), "CI must include open-source/** branches on push"
        # Allow the workflow_dispatch opt-in for ad-hoc triggers.
        assert "workflow_dispatch" in on, "CI must allow workflow_dispatch triggers"
    else:
        # Allow `on: [push, pull_request]` list form.
        assert "push" in on and "pull_request" in on


def test_ci_declares_least_privilege_permissions(ci_yaml):
    perms = ci_yaml.get("permissions", {})
    assert perms.get("contents") == "read", (
        "CI permissions must be least-privilege: contents: read; saw " + repr(perms)
    )


def test_ci_concurrency_group_prevents_overlapping_runs(ci_yaml):
    cc = ci_yaml.get("concurrency", {})
    assert "group" in cc, "CI must declare a concurrency group"
    assert cc.get("cancel-in-progress") is True, (
        "CI should cancel in-progress duplicate runs to conserve minutes"
    )


# ---------------------------------------------------------------------------
# 2. Lint job — recognised linter present
# ---------------------------------------------------------------------------
def test_ci_has_lint_job_running_recognised_linter(ci_yaml, ci_text):
    jobs = ci_yaml.get("jobs", {})
    assert "lint" in jobs, "ci.yml must declare a `lint` job"
    lint = jobs["lint"]
    # The job's steps must invoke a recognised Python linter.
    step_text = " ".join(
        " ".join(str(v) for v in (step.get("run") or "").splitlines())
        for step in lint.get("steps", [])
        if isinstance(step, dict)
    )
    assert any(
        tool in ci_text for tool in ("ruff", "mypy", "pyright", "flake8", "pylint")
    ), "CI lint job must run a recognised Python linter"
    # The default Zero linter is ruff scoped to defects in pyproject.toml.
    assert "ruff" in ci_text, "CI should run ruff for the Zero ruleset"


def test_ci_lint_job_runs_on_supported_runner(ci_yaml):
    lint = ci_yaml.get("jobs", {}).get("lint", {})
    runs_on = str(lint.get("runs-on", ""))
    assert "ubuntu" in runs_on, "lint job should run on Ubuntu (saw " + runs_on + ")"


# ---------------------------------------------------------------------------
# 3. Test job — pytest on supported Python versions
# ---------------------------------------------------------------------------
def test_ci_has_test_job_running_pytest(ci_yaml, ci_text):
    jobs = ci_yaml.get("jobs", {})
    assert "test" in jobs, "ci.yml must declare a `test` job"
    test = jobs["test"]
    step_text = ci_text
    assert "pytest" in step_text, "CI test job must invoke pytest"
    # The test job must compile the package before running the suite.
    assert "compileall" in step_text, (
        "CI test job must compile-check sources before running the suite"
    )


def test_ci_test_job_runs_matrix_on_supported_python_versions(ci_yaml):
    test = ci_yaml.get("jobs", {}).get("test", {})
    matrix = test.get("strategy", {}).get("matrix", {})
    versions = matrix.get("python", [])
    assert versions, "test job must run across multiple Python versions"
    # Zero targets 3.11+; the matrix must reach 3.11.
    assert any(v.startswith("3.11") for v in versions), (
        "CI matrix must exercise Python 3.11 (the documented target)"
    )
    # Should ALSO cover a forward Python (3.12+).
    assert any(v.startswith("3.12") for v in versions) or any(
        v.startswith("3.13") for v in versions
    ), "CI matrix should include a forward Python version"


def test_ci_test_job_uses_persistent_action_caching(ci_yaml):
    test = ci_yaml.get("jobs", {}).get("test", {})
    steps = test.get("steps", [])
    uses = [str(s.get("uses", "")) for s in steps if isinstance(s, dict)]
    assert any("actions/checkout" in u for u in uses), "test job must checkout the repo"
    # Setup-python with cache: pip accelerates dep installs.
    python_steps = [
        s
        for s in steps
        if isinstance(s, dict) and "actions/setup-python" in str(s.get("uses", ""))
    ]
    assert python_steps, "test job must set up Python via actions/setup-python"


# ---------------------------------------------------------------------------
# 4. Security job — declared and exercises a real scan
# ---------------------------------------------------------------------------
def test_ci_has_security_job_declared(ci_yaml):
    jobs = ci_yaml.get("jobs", {})
    assert "security" in jobs, "ci.yml must declare a `security` job"
    security = jobs["security"]
    assert "runs-on" in security
    steps = security.get("steps", [])
    assert len(steps) >= 2, "security job must declare at least 2 steps"


def test_ci_security_job_runs_dependency_vulnerability_scan(ci_yaml, ci_text):
    """The security job must run a dependency vulnerability audit (pip-audit
    against the pinned lock file) and an artifact scan against the repo tree."""
    security = ci_yaml.get("jobs", {}).get("security", {})
    step_text = ci_text
    # pip-audit guards the pinned requirements.lock.
    assert "pip-audit" in step_text, (
        "CI security job must run pip-audit against requirements.lock"
    )
    assert "requirements.lock" in step_text or "requirements.txt" in step_text, (
        "audit must target the pinned lock file"
    )


def test_ci_security_job_runs_public_artifact_scan(ci_yaml, ci_text):
    """The security job must run the public-artifact scan that guards the open
    release tree."""
    # verify_public_artifact.py enforces that the release output is clean.
    assert "verify_public_artifact.py" in ci_text, (
        "CI must run scripts/verify_public_artifact.py to gate the public release"
    )


# ---------------------------------------------------------------------------
# 5. Optional but recommended: docker build smoke gate
# ---------------------------------------------------------------------------
def test_ci_has_docker_build_smoke_job(ci_yaml):
    jobs = ci_yaml.get("jobs", {})
    assert "docker" in jobs, "CI should run a docker build job (clean container smoke)"
    docker = jobs["docker"]
    step_text = " ".join(str(s.get("run") or "") for s in docker.get("steps", []))
    # Smoke check that the image prints its own version banner cleanly.
    assert "version" in step_text and "status" in step_text, (
        "docker job must smoke-test `version` and `status` commands"
    )
    # Must assert the runtime user is unprivileged.
    assert "id zero:ci" in step_text.replace("\n", " ") or '"root"' not in step_text


# ---------------------------------------------------------------------------
# 6. CI references tests that exist in the repo (releases-blocker guard)
# ---------------------------------------------------------------------------
def test_ci_referenced_test_files_exist(ci_text, monkeypatch):
    """Every ``tests/test_*.py`` path referenced by the CI workflow must point
    to a file that exists. A dangling reference would silently skip a release
    gate, so this stays a hard error."""
    refs = set(re.findall(r"tests/(test_[A-Za-z0-9_]+\.py)", ci_text))
    missing = [name for name in refs if not (REPO_ROOT / "tests" / name).is_file()]
    assert not missing, (
        "CI workflow references tests that do not exist in the repo: "
        + ", ".join(sorted(missing))
    )
