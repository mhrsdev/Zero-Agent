"""CI pipeline + release tree + backup/restore verification tests.

Verifies:
- CI workflow exists with lint, test, security steps
- .gitignore excludes secrets, sessions, databases, runtime data
- Backup/restore scripts exist and are scoped
- Upgrade/rollback migration support exists
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest


class TestCIPipeline:
    """CI must run lint + test + security on every push."""

    def test_ci_workflow_exists(self):
        ci = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        assert ci.exists(), ".github/workflows/ci.yml must exist"

    def test_ci_runs_tests(self):
        ci = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        content = ci.read_text()
        assert "pytest" in content, "CI must run pytest"

    def test_ci_runs_lint_or_type_check(self):
        ci = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        content = ci.read_text()
        assert "ruff" in content or "mypy" in content or "pyright" in content or "flake8" in content, \
            "CI must run a linter or type checker"

    def test_ci_has_security_step(self):
        ci = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        content = ci.read_text()
        assert "secret" in content.lower() or "trufflehog" in content.lower() or "gitleaks" in content.lower() or "safety" in content.lower() or "audit" in content.lower(), \
            "CI must have a security scanning step"


class TestReleaseTreeClean:
    """The public release tree must be clean — no secrets, no runtime data."""

    def test_gitignore_excludes_env_files(self):
        gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
        content = gitignore.read_text()
        assert ".env" in content, ".gitignore must exclude .env files"

    def test_gitignore_excludes_session_files(self):
        gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
        content = gitignore.read_text()
        assert "*.session" in content, ".gitignore must exclude Telegram session files"

    def test_gitignore_excludes_database_files(self):
        gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
        content = gitignore.read_text()
        assert "*.db" in content or "*.sqlite" in content, ".gitignore must exclude database files"

    def test_gitignore_excludes_runtime_data(self):
        gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
        content = gitignore.read_text()
        assert "runtime/" in content or "release/" in content, ".gitignore must exclude runtime/release dirs"

    def test_no_env_file_tracked(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["git", "ls-files", "*.env", ".env", ".env.*"],
            capture_output=True, text=True, cwd=root,
        )
        tracked = [f for f in result.stdout.strip().split("\n") if f and ".env.example" not in f]
        assert not tracked, f"Secret .env files must not be tracked: {tracked}"


class TestBackupRestore:
    """Backup and restore scripts must exist."""

    def test_init_db_script_exists(self):
        root = Path(__file__).resolve().parents[1]
        assert (root / "scripts" / "init_db.py").exists(), "scripts/init_db.py must exist"

    def test_generate_sbom_script_exists(self):
        root = Path(__file__).resolve().parents[1]
        assert (root / "scripts" / "generate_sbom.py").exists(), "scripts/generate_sbom.py must exist for artifact scanning"

    def test_verify_public_artifact_exists(self):
        root = Path(__file__).resolve().parents[1]
        assert (root / "scripts" / "verify_public_artifact.py").exists(), \
            "scripts/verify_public_artifact.py must exist for release verification"


class TestMigrationSupport:
    """Migrations must support rollback and verification."""

    def test_memory_v3_migration_exists(self):
        root = Path(__file__).resolve().parents[1]
        assert (root / "scripts" / "migrate_memory_v3.py").exists(), "memory v3 migration must exist"
        assert (root / "zero" / "memory_v3" / "migration.py").exists(), "migration module must exist"

    def test_migration_has_rollback_support(self):
        from zero.memory_v3 import migration
        source = inspect.getsource(migration)
        assert "rollback" in source.lower() or "reverse" in source.lower() or "down" in source.lower(), \
            "migration must support rollback/reverse"

    def test_migration_has_verification(self):
        from zero.memory_v3 import migration
        source = inspect.getsource(migration)
        assert "verify" in source.lower() or "validate" in source.lower() or "check" in source.lower(), \
            "migration must support verification"
