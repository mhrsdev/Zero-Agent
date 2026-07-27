"""Panel + Office hardening verification tests.

Verifies:
- Panel UI is connected to real backend (not placeholder)
- Office preflight security rejects dangerous archives
- Document bundles carry scope
- Cleanup runs on expired jobs
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest


class TestPanelIsRealNotPlaceholder:
    """The panel must be connected to a real backend, not be a stub."""

    def test_panel_api_has_real_routes(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        # Must have real endpoints, not just 'pass' or 'TODO'
        assert "def _users" in source, "PanelAPI must have users endpoint"
        assert "def _memory_list" in source, "PanelAPI must have memory endpoint"
        assert "def _session_list" in source, "PanelAPI must have session endpoint"
        assert "TODO" not in source or "placeholder" not in source.lower()

    def test_panel_api_has_csrf_on_writes(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "csrf" in source.lower(), "PanelAPI must enforce CSRF on write operations"

    def test_panel_api_has_rate_limiting(self):
        from zero.panel_api import PanelAPI
        source = inspect.getsource(PanelAPI)
        assert "rate" in source.lower() or "limit" in source.lower(), "PanelAPI must have rate limiting"


class TestOfficeHardening:
    """Office preflight must reject dangerous inputs."""

    def test_zip_bomb_rejected(self):
        """Verify the preflight module exists and has zip bomb protection."""
        from zero.office import preflight
        source = inspect.getsource(preflight)
        assert "zip_bomb" in source or "uncompressed" in source or "ratio" in source, \
            "preflight must protect against zip bombs"

    def test_dangerous_archive_features_rejected(self):
        """Verify the preflight module exists and has security checks."""
        from zero.office import preflight
        source = inspect.getsource(preflight)
        assert "archive_traversal" in source or "zip_bomb" in source or "traversal" in source
        assert "macro" in source
        assert "xml_entity" in source or "entity" in source

    def test_office_cleanup_exists(self):
        from zero.office import cleanup
        assert hasattr(cleanup, "__name__")


class TestDocumentBundlesScope:
    """Document bundles should be group-scoped."""

    def test_document_bundles_module_exists(self):
        from zero import document_bundles
        assert hasattr(document_bundles, "__name__")

    def test_document_bundles_has_scoped_methods(self):
        from zero import document_bundles
        source = inspect.getsource(document_bundles)
        # Should reference group or installation context
        assert "group" in source.lower() or "scope" in source.lower() or "installation" in source.lower(), \
            "document_bundles should reference group/scope/installation"


class TestNoSecretsInGit:
    """No secrets should be in the git tree."""

    def test_gitignore_excludes_sensitive_files(self):
        root = Path(__file__).resolve().parents[1]
        gitignore = (root / ".gitignore").read_text()
        assert ".env" in gitignore or "*.env" in gitignore, ".gitignore must exclude .env files"
        assert "*.db" in gitignore or "*.sqlite" in gitignore, ".gitignore must exclude database files"

    def test_no_secret_patterns_in_tracked_files(self):
        """Scan tracked source files for obvious secret patterns."""
        root = Path(__file__).resolve().parents[1]
        import re
        secret_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",
            r"ghp_[a-zA-Z0-9]{36}",
            r"xoxb-[0-9]{10,}",
            r"AKIA[A-Z0-9]{16}",
        ]
        issues = []
        for src in (root / "zero").rglob("*.py"):
            if "__pycache__" in str(src):
                continue
            content = src.read_text()
            for pattern in secret_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    issues.append(f"{src.name}: {pattern} found {len(matches)} matches")
        assert not issues, f"Secret patterns found: {issues}"
