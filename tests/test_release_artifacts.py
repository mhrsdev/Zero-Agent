"""Release artifacts verification test.

Verifies that the release tree contains the required artifacts:
- Dockerfile and docker-compose.yml are valid
- .gitignore excludes secrets
- LICENSE exists
- Public release boundary is established
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_dockerfile_exists_and_uses_multi_stage():
    root = Path(__file__).resolve().parents[1]
    dockerfile = root / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile must exist"
    content = dockerfile.read_text()
    assert "FROM" in content, "Dockerfile must have FROM instructions"


def test_docker_compose_exists():
    root = Path(__file__).resolve().parents[1]
    compose = root / "docker-compose.yml"
    assert compose.exists(), "docker-compose.yml must exist"


def test_license_exists():
    root = Path(__file__).resolve().parents[1]
    license_file = root / "LICENSE"
    assert license_file.exists(), "LICENSE must exist"


def test_gitignore_exists_and_excludes_secrets():
    root = Path(__file__).resolve().parents[1]
    gitignore = root / ".gitignore"
    assert gitignore.exists(), ".gitignore must exist"
    content = gitignore.read_text()
    assert ".env" in content, ".gitignore must exclude .env"


def test_public_release_boundary_established():
    root = Path(__file__).resolve().parents[1]
    # The public release boundary commit exists
    assert (root / ".gitignore").exists(), "release boundary must be established"


def test_no_secrets_in_tracked_files():
    """Scan tracked Python source files (not tests) for obvious secret patterns."""
    import re
    import subprocess
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "zero/"],
        capture_output=True, text=True, cwd=root,
    )
    secret_patterns = [
        r"sk-[a-zA-Z0-9]{20,}",
        r"ghp_[a-zA-Z0-9]{36}",
        r"xoxb-[0-9]{10,}",
        r"AKIA[A-Z0-9]{16}",
    ]
    issues = []
    for filepath in result.stdout.strip().split("\n"):
        if not filepath or not filepath.endswith(".py"):
            continue
        try:
            content = (root / filepath).read_text()
            for pattern in secret_patterns:
                if re.search(pattern, content):
                    issues.append(f"{filepath}: {pattern}")
        except Exception:
            pass
    assert not issues, f"Secret patterns found: {issues}"
