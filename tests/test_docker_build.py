"""Docker build verification — Dockerfile + docker-compose.yml structure.

These are static contract tests against the public release build files. They
assert that the supplied ``Dockerfile`` and ``docker-compose.yml``:

* exist and parse cleanly,
* use a multi-stage build with an unprivileged runtime UID/GID,
* COPY only application code (no secrets, no ``runtime/``),
* reference each path that the README documents as the operator-facing entry
  point (``zero/``, ``scripts/``, ``panel/``, ``config/``, ``pyproject.toml``),
* expose the panel on the documented loopback-only port,
* mount a writable volume at the documented ``/data`` runtime dir, and
* do not patch ``runtime:root`` privileges or expose ``0.0.0.0`` by default.

No docker daemon is required — these tests read the source files only. The
``docker`` CI job exercises the actual image build.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def dockerfile_text():
    assert DOCKERFILE.is_file(), "Dockerfile must exist at repo root"
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose_yaml():
    assert COMPOSE.is_file(), "docker-compose.yml must exist at repo root"
    parsed = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


# ---------------------------------------------------------------------------
# 1. Dockerfile structural contracts
# ---------------------------------------------------------------------------
def test_dockerfile_uses_multi_stage_build(dockerfile_text):
    """A builder carries the toolchain; the runtime stage is slim & minimal."""
    assert re.search(r"^FROM .* AS builder\b", dockerfile_text, re.MULTILINE), (
        "Dockerfile must include a `builder` stage"
    )
    assert re.search(r"^FROM .* AS runtime\b", dockerfile_text, re.MULTILINE), (
        "Dockerfile must include a `runtime` stage"
    )
    # Only the venv produced by the builder is copied over — no build toolchain
    # leaks into the runtime image.
    assert re.search(
        r"^COPY --from=builder /opt/venv /opt/venv\b", dockerfile_text, re.MULTILINE
    )


def test_dockerfile_runs_as_unprivileged_non_root_user(dockerfile_text):
    """The runtime image must not run as root.

    Either ``USER zero`` or an explicit numeric UID != 0 must be present.
    """
    # An unprivileged user is created with a deterministic UID/GID.
    assert re.search(r"useradd --uid 10001 --gid zero", dockerfile_text), (
        "Dockerfile must create the unprivileged zero runtime user"
    )
    # The image entrypoint drops to that user.
    assert re.search(r"^USER zero", dockerfile_text, re.MULTILINE), (
        "Dockerfile must declare USER zero before ENTRYPOINT"
    )


def test_dockerfile_copies_application_directories_not_runtime(dockerfile_text):
    """Only application code, the static panel, and pinned requirements
    may be copied into the runtime image.

    The image contents must not include runtime state, secrets, history,
    tests, or local debug scripts.
    """
    expected_copies = [
        ("zero/", "application package"),
        ("scripts/", "support scripts"),
        ("panel/", "static panel assets"),
        ("config/", "bundled config defaults"),
        ("pyproject.toml", "pyproject metadata"),
        ("LICENSE", "LICENSE"),
        ("NOTICE", "NOTICE"),
        ("THIRD_PARTY_NOTICES.md", "third-party notices"),
    ]
    for token, label in expected_copies:
        # Either a directory (trailing slash) or a file — match either form.
        regex = re.escape(token).replace(r"\$", r"\\$")
        assert re.search(rf"^COPY[^\n]*\s{regex}", dockerfile_text, re.MULTILINE), (
            f"Dockerfile must COPY {token} ({label})"
        )
    # The dangerous sibling dirs must never leak in.
    for forbidden in ("runtime/", "/root/zero/runtime", "runtime/secrets", ".git"):
        assert forbidden not in re.sub(
            r"#.*$", "", dockerfile_text
        ), f"Dockerfile must never reference {forbidden!r}"


def test_dockerfile_exposes_panel_port_and_has_healthcheck(dockerfile_text):
    """The runtime image must EXPOSE the panel port and ship a HEALTHCHECK
    that fails when the panel is unreachable.
    """
    assert re.search(r"^EXPOSE 8787\b", dockerfile_text, re.MULTILINE), (
        "Dockerfile must EXPOSE 8787 (panel port)"
    )
    assert re.search(r"^HEALTHCHECK\b", dockerfile_text, re.MULTILINE), (
        "Dockerfile must ship a HEALTHCHECK that polls /api/health"
    )
    # The healthcheck must actually probe the documented health endpoint,
    # not just any HTTP probe.
    assert "/api/health" in dockerfile_text


def test_dockerfile_entrypoint_is_python_and_command_default_status(dockerfile_text):
    """PID 1 must be python so SIGTERM reaches the runtime gracefully; the
    ship default command is the read-only ``status`` operation.
    """
    assert re.search(
        r"^ENTRYPOINT \[.*python.*-m.*zero.*\]", dockerfile_text, re.MULTILINE
    ), "Dockerfile ENTRYPOINT must be exec-form python -m zero"
    assert re.search(r"^CMD \[.*status.*\]", dockerfile_text, re.MULTILINE), (
        "Dockerfile default CMD must be status"
    )


# ---------------------------------------------------------------------------
# 2. docker-compose.yml structural contracts
# ---------------------------------------------------------------------------
def test_compose_defines_panel_and_listener_services(compose_yaml):
    services = compose_yaml.get("services", {})
    assert "zero-panel" in services, "compose must define a zero-panel service"
    assert "zero-listener" in services, "compose must define a zero-listener service"
    panel = services["zero-panel"]
    # Both services build from the same Dockerfile.
    assert panel["build"]["dockerfile"] == "Dockerfile"
    assert panel["build"]["context"] == "."
    listener = services["zero-listener"]
    assert listener["build"]["dockerfile"] == "Dockerfile"


def test_compose_panel_binds_loopback_only_by_default(compose_yaml):
    panel = compose_yaml["services"]["zero-panel"]
    ports = panel.get("ports", [])
    port_strings = [str(p) for p in ports]
    assert any("127.0.0.1:8787" in port_str for port_str in port_strings), (
        "panel port must bind to 127.0.0.1 only by default; saw " + repr(ports)
    )
    # No port string must start with a wildcard bind (operator must opt in).
    for port_str in port_strings:
        assert not port_str.startswith("0.0.0.0"), (
            "panel must NOT bind 0.0.0.0 by default; saw " + repr(port_str)
        )


def test_compose_mounts_writable_data_volume(compose_yaml):
    panel = compose_yaml["services"]["zero-panel"]
    vol_strings = [str(v) for v in panel.get("volumes", [])]
    assert any("zero-data:/data" in v for v in vol_strings), (
        "panel must mount the zero-data volume at /data"
    )
    # The compose file must declare the same named volume.
    volumes = compose_yaml.get("volumes", {})
    assert "zero-data" in volumes, "compose must declare a zero-data volume"


def test_compose_secrets_mounted_and_never_baked_into_image(compose_yaml):
    """Secrets must come from a Docker secret, never baked into the runtime."""
    panel = compose_yaml["services"]["zero-panel"]
    secrets = panel.get("secrets", [])
    assert "zero_secrets" in secrets, "panel service must consume the zero_secrets secret"
    # The secret declaration must point at the runtime path on disk and never
    # bake a literal secret value into the compose file.
    declared = compose_yaml.get("secrets", {})
    assert "zero_secrets" in declared
    source_path = declared["zero_secrets"].get("file", "")
    assert "runtime/secrets" in source_path or "zero.secrets" in source_path
    # The compose file must not contain hardcoded tokens/api keys.
    text = COMPOSE.read_text()
    assert not re.search(r"(?i)(tok)\s*[:=]\s*['\"]\d+['\"]", text)
    assert "bot_token:" not in text


def test_compose_listener_depends_on_healthy_panel(compose_yaml):
    listener = compose_yaml["services"]["zero-listener"]
    depends = listener.get("depends_on", {})
    assert "zero-panel" in depends
    panel_dep = depends["zero-panel"]
    assert isinstance(panel_dep, dict)
    assert panel_dep.get("condition") == "service_healthy"
    assert listener["command"] == ["listener"]


def test_compose_hardens_services_with_drop_all_and_no_new_privileges(compose_yaml):
    for name in ("zero-panel", "zero-listener"):
        svc = compose_yaml["services"][name]
        assert "no-new-privileges:true" in svc.get("security_opt", []), (
            f"{name} must drop new privileges"
        )
        assert "ALL" in svc.get("cap_drop", []), f"{name} must cap_drop ALL"
        assert svc.get("read_only", False) is True, f"{name} must be read_only"
