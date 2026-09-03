"""Packaging and installer contracts for the defects found in the full audit.

Each test here pins a failure that was reachable from a documented command and
that no existing gate caught:

* ``pyproject.toml`` dependency drift installed aiohttp 3.10.11 -- below the
  PYSEC floor ``requirements.txt`` documents -- and omitted ``tzdata``, so
  ``zoneinfo`` timezone validation raised on Windows.
* ``requirements.lock`` was a Linux-only resolution installed on every platform.
  Because it carries hashes, pip enters --require-hashes mode, where every
  transitive requirement must be pinned; ``colorama`` (a win32-only pytest
  dependency) was absent, so the documented Windows one-line install aborted.
* ``install.ps1`` assigned ``$Req`` inside one branch and interpolated it into
  the closing instructions, printing ``pip install -r`` with no argument.
* ``.dockerignore`` did not exclude ``config/zero.yaml``, so an operator who
  filled in credentials there baked them into an image layer.
* ``zero-listener`` inherited the image HEALTHCHECK, which polls the panel's
  HTTP endpoint that the listener process does not serve.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"
REQUIREMENTS_DEV = ROOT / "requirements-dev.txt"
LOCK = ROOT / "requirements.lock"
TEST_ONLY = {"pytest", "pytest-asyncio"}


def _requirement_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _name(requirement: str) -> str:
    return re.split(r"[=<>!~\[; ]", requirement, maxsplit=1)[0].strip().lower()


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_pyproject_dependencies_match_requirements_txt(pyproject):
    """The wheel metadata and the install path must resolve the same versions.

    Drift here is invisible: nothing in CI, Docker or either installer runs
    ``pip install .``, so a stale pin was only observable by building a wheel.
    """
    declared = {_name(r): r for r in pyproject["project"]["dependencies"]}
    required = {_name(r): r for r in _requirement_lines(REQUIREMENTS)}
    assert set(declared) == set(required), (
        "pyproject dependencies and requirements.txt must cover the same "
        f"packages; only in pyproject: {sorted(set(declared) - set(required))}, "
        f"only in requirements.txt: {sorted(set(required) - set(declared))}"
    )
    mismatched = {n: (declared[n], required[n]) for n in declared if declared[n] != required[n]}
    assert not mismatched, f"version specifiers differ between the two files: {mismatched}"


def test_aiohttp_security_floor_is_declared_in_both_places(pyproject):
    """The PYSEC floor must be enforced on the wheel path too, not only in
    requirements.txt: aiogram's own range permits vulnerable aiohttp releases."""
    for source in (pyproject["project"]["dependencies"], _requirement_lines(REQUIREMENTS)):
        aiohttp = next((r for r in source if _name(r) == "aiohttp"), None)
        assert aiohttp is not None, "aiohttp is imported directly and must be declared"
        assert ">=3.13.4" in aiohttp, f"aiohttp floor missing from {aiohttp!r}"


def test_tzdata_is_declared_because_zoneinfo_validation_needs_it(pyproject):
    """Windows has no system tz database; several modules import zoneinfo at
    module scope and validate configured timezones."""
    for source in (pyproject["project"]["dependencies"], _requirement_lines(REQUIREMENTS)):
        assert any(_name(r) == "tzdata" for r in source), "tzdata must be declared"


def test_runtime_requirements_carry_no_test_framework():
    """requirements.txt feeds requirements.lock, the Docker image and both
    installers. Test frameworks there shipped pytest into production and pulled
    in the platform-conditional dependency that broke the Windows lock install."""
    runtime = {_name(r) for r in _requirement_lines(REQUIREMENTS)}
    assert not (runtime & TEST_ONLY), (
        f"test-only packages must live in requirements-dev.txt: {sorted(runtime & TEST_ONLY)}"
    )
    dev = {_name(r) for r in _requirement_lines(REQUIREMENTS_DEV)}
    assert TEST_ONLY <= dev, f"requirements-dev.txt must declare {sorted(TEST_ONLY)}"


def test_lockfile_is_a_universal_resolution():
    """A hashed lock is installed in --require-hashes mode, where every
    transitive requirement must be pinned. A single-platform resolution silently
    omits platform-conditional dependencies and aborts the install elsewhere."""
    text = LOCK.read_text(encoding="utf-8")
    header = "\n".join(text.splitlines()[:4])
    assert "--generate-hashes" in header, "lock must carry hashes"
    assert "--universal" in header, (
        "requirements.lock must be compiled with --universal so platform-conditional "
        "dependencies are pinned for every supported OS; regenerate with "
        "`uv pip compile --generate-hashes --universal --python-version 3.11 "
        "requirements.txt -o requirements.lock`"
    )


def test_lockfile_pins_every_requirement_with_double_equals():
    """pip's hash-checking mode rejects any requirement that is not ``==``
    pinned, including transitive ones."""
    unpinned = [
        line for line in LOCK.read_text(encoding="utf-8").splitlines()
        if line and not line[0].isspace() and not line.startswith("#") and "==" not in line
    ]
    assert not unpinned, f"unpinned requirements would abort a hashed install: {unpinned}"


def test_lockfile_and_runtime_requirements_agree():
    locked = {
        _name(line): line.split("\\")[0].strip()
        for line in LOCK.read_text(encoding="utf-8").splitlines()
        if line and not line[0].isspace() and not line.startswith("#")
    }
    for requirement in _requirement_lines(REQUIREMENTS):
        name = _name(requirement)
        assert name in locked, f"{name} is declared but absent from requirements.lock"


def test_install_ps1_resolves_requirements_file_before_branching():
    """``$Req`` is printed in the closing instructions, so it must be assigned on
    every path -- not inside one install branch."""
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "Set-StrictMode" in text, (
        "install.ps1 must enable Set-StrictMode so an unassigned variable fails "
        "loudly instead of interpolating an empty string"
    )
    lines = text.splitlines()
    assignment = next(i for i, line in enumerate(lines) if re.match(r"\s*\$Req\s*=", line))
    assert not lines[assignment].startswith((" ", "\t")), (
        "$Req must be assigned at script scope, not inside a conditional branch"
    )
    first_use = next(i for i, line in enumerate(lines) if i != assignment and "$Req" in line)
    assert assignment < first_use, "$Req must be assigned before its first use"


def test_dockerignore_excludes_operator_config_and_state_recursively():
    """Single-segment .dockerignore patterns only match at the context root, so
    every credential/state pattern needs a recursive form as well."""
    entries = {
        line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "config/zero.yaml" in entries, (
        "config/ is COPYied into the image, so the operator-filled runtime YAML "
        "must be excluded by name or its credentials land in an image layer"
    )
    for pattern in ("*.db", "*.session", "*.log", "*.key", "*.pem"):
        assert f"**/{pattern}" in entries, f"missing recursive form of {pattern}"


def test_compose_listener_does_not_inherit_the_panel_healthcheck():
    """The image HEALTHCHECK polls the panel's /api/health. run_listener.py
    serves no HTTP endpoint, so the listener container would report unhealthy
    forever."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    listener = compose["services"]["zero-listener"]
    assert listener.get("healthcheck", {}).get("disable") is True, (
        "zero-listener must disable the inherited panel healthcheck"
    )
    assert "healthcheck" in compose["services"]["zero-panel"], (
        "the panel must keep a real healthcheck; the listener depends on it"
    )
