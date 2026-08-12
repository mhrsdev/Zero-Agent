"""Regression tests for portable setup and terminal UI behavior.

These tests simulate Windows' lack of the stdlib ``curses`` backend without
requiring a Windows runner, so the contract remains covered in CI.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_interactive_tui_uses_a_console_loop_when_curses_is_unavailable(monkeypatch):
    """A Windows `zero tui` must not render once and immediately terminate."""
    from zero import tui

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        tui,
        "run_console_tui",
        lambda **kwargs: calls.append(kwargs) or 0,
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "curses", None)
    monkeypatch.setattr(tui.sys, "stdin", _TTY())
    monkeypatch.setattr(tui.sys, "stdout", _TTY())

    assert tui.main(["--no-animation"]) == 0
    assert len(calls) == 1
    assert calls[0]["initial_panel"] == "status"


def test_interactive_tui_recovers_with_console_when_curses_initialization_fails(monkeypatch):
    """A bad TERM must not make an otherwise interactive TUI immediately close."""
    from zero import tui

    class BrokenCurses:
        class error(Exception):
            pass

        @staticmethod
        def wrapper(_callback):
            raise BrokenCurses.error("terminal unavailable")

    calls: list[dict[str, object]] = []
    monkeypatch.setitem(sys.modules, "curses", BrokenCurses)
    monkeypatch.setattr(tui, "run_console_tui", lambda **kwargs: calls.append(kwargs) or 0)
    monkeypatch.setattr(tui.sys, "stdin", _TTY())
    monkeypatch.setattr(tui.sys, "stdout", _TTY())

    assert tui.main(["--no-animation"]) == 0
    assert len(calls) == 1


def test_portable_console_tui_renders_commands_until_user_quits(tmp_path):
    """The fallback remains interactive and can navigate before an explicit quit."""
    from zero import tui

    commands = iter(["2", "q"])
    prompts: list[str] = []
    output: list[str] = []

    def read_command(prompt: str) -> str:
        prompts.append(prompt)
        return next(commands)

    result = tui.run_console_tui(
        initial_panel="status",
        store_path=tmp_path / "zero.db",
        config_path=tmp_path / "zero.json",
        runtime_path=tmp_path / "zero.yaml",
        tail=50,
        input_fn=read_command,
        output_fn=output.append,
    )

    assert result == 0
    assert len(prompts) == 2
    rendered = "\n".join(output)
    assert "Zero Status" in rendered
    assert "Zero Doctor" in rendered
    assert "Zero console closed" in rendered


def test_portable_setup_wizard_persists_safe_configuration(tmp_path):
    """Windows setup must work without curses and retain only symbolic refs."""
    from zero import tui
    from zero.configuration import ConfigStore
    from zero.panel_store import PanelStore

    answers = iter(["windows-local", "bot", "telegram.bot_token"])
    output: list[str] = []

    ok = tui.run_console_setup_wizard(
        config_path=tmp_path / "zero.json",
        panel_path=tmp_path / "panel.db",
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    assert ok is True
    config = ConfigStore(tmp_path / "zero.json").load()
    assert config.installation_id == "windows-local"
    assert config.telegram.mode == "bot"
    assert config.telegram.bot_token_ref == "telegram.bot_token"
    assert "secret-value" not in (tmp_path / "zero.json").read_text(encoding="utf-8")
    assert PanelStore(tmp_path / "panel.db").get_setup_state()["completed"] is True
    assert any("Setup complete" in line for line in output)


def test_portable_setup_cancellation_leaves_existing_configuration_and_panel_state_untouched(tmp_path):
    """Aborting after a later prompt must not persist an earlier profile choice."""
    from zero import tui
    from zero.configuration import ConfigStore

    config_path = tmp_path / "zero.json"
    panel_path = tmp_path / "panel.db"
    store = ConfigStore(config_path)
    store.save(store.new_config("original-installation"))
    answers = iter(["replacement-installation", "bot"])

    def cancel_at_secret_reference(_prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration as exc:
            raise EOFError from exc

    assert (
        tui.run_console_setup_wizard(
            config_path=config_path,
            panel_path=panel_path,
            input_fn=cancel_at_secret_reference,
            output_fn=lambda _line: None,
        )
        is False
    )
    assert store.load().installation_id == "original-installation"
    assert not store.backup_path.exists()
    assert not panel_path.exists()


def test_portable_setup_rolls_back_canonical_config_if_panel_persistence_fails(tmp_path, monkeypatch):
    """A panel write failure must not leave canonical setup half-committed."""
    from zero import tui
    from zero.configuration import ConfigStore
    from zero.panel_store import PanelStore

    config_path = tmp_path / "zero.json"
    panel_path = tmp_path / "panel.db"
    store = ConfigStore(config_path)
    store.save(store.new_config("original-installation"))
    before = config_path.read_bytes()

    def fail_panel_write(self, step, data):
        raise sqlite3.Error("injected persistence failure")

    monkeypatch.setattr(PanelStore, "save_setup_step", fail_panel_write)
    answers = iter(["replacement-installation", "disabled"])
    assert (
        tui.run_console_setup_wizard(
            config_path=config_path,
            panel_path=panel_path,
            input_fn=lambda _prompt: next(answers),
            output_fn=lambda _line: None,
        )
        is False
    )

    assert config_path.read_bytes() == before
    assert not store.backup_path.exists()
    assert not panel_path.exists()


def test_portable_setup_restores_existing_panel_progress_if_a_later_write_fails(tmp_path, monkeypatch):
    from zero import tui
    from zero.configuration import ConfigStore
    from zero.panel_store import PanelStore

    config_path = tmp_path / "zero.json"
    panel_path = tmp_path / "panel.db"
    store = ConfigStore(config_path)
    store.save(store.new_config("original-installation"))
    panel = PanelStore(panel_path)
    before_panel = panel.get_setup_state()
    original_save = PanelStore.save_setup_step

    def fail_telegram_write(self, step, data):
        if step == "telegram":
            raise sqlite3.Error("injected second-write failure")
        return original_save(self, step, data)

    monkeypatch.setattr(PanelStore, "save_setup_step", fail_telegram_write)
    answers = iter(["replacement-installation", "disabled"])
    assert (
        tui.run_console_setup_wizard(
            config_path=config_path,
            panel_path=panel_path,
            input_fn=lambda _prompt: next(answers),
            output_fn=lambda _line: None,
        )
        is False
    )

    assert store.load().installation_id == "original-installation"
    assert PanelStore(panel_path).get_setup_state() == before_panel


def test_portable_setup_rejects_credential_shaped_installation_id_without_persisting_it(tmp_path):
    from zero import tui

    raw_bot_token = "123456789:" + ("a" * 31)
    config_path = tmp_path / "zero.json"
    panel_path = tmp_path / "panel.db"
    output: list[str] = []
    assert (
        tui.run_console_setup_wizard(
            config_path=config_path,
            panel_path=panel_path,
            input_fn=lambda _prompt: raw_bot_token,
            output_fn=output.append,
        )
        is False
    )

    assert not config_path.exists()
    assert not panel_path.exists()
    assert raw_bot_token not in "\n".join(output)


def test_portable_setup_rejects_raw_api_hash_without_persisting_it(tmp_path):
    """A secret-shaped API hash must never become a canonical reference."""
    from zero import tui

    raw_api_hash = "a" * 32
    answers = iter(["windows-local", "user_session", "1", raw_api_hash, "telegram.session"])
    output: list[str] = []
    config_path = tmp_path / "zero.json"
    panel_path = tmp_path / "panel.db"

    assert (
        tui.run_console_setup_wizard(
            config_path=config_path,
            panel_path=panel_path,
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        is False
    )

    assert not config_path.exists()
    assert not panel_path.exists()
    assert "Telegram settings did not pass validation" in "\n".join(output)


def test_portable_setup_rejects_nonpositive_telegram_api_id(tmp_path):
    """The console promises a positive API id and must enforce it before saving."""
    from zero import tui

    answers = iter(["windows-local", "user_session", "0"])
    output: list[str] = []
    config_path = tmp_path / "zero.json"

    assert (
        tui.run_console_setup_wizard(
            config_path=config_path,
            panel_path=tmp_path / "panel.db",
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        is False
    )

    assert not config_path.exists()
    assert "API id must be a positive integer" in "\n".join(output)


def test_portable_setup_does_not_echo_invalid_config_values(tmp_path):
    """Validation errors must not reflect an existing malformed configuration."""
    from zero import tui

    marker = "DO_NOT_ECHO_THIS_INVALID_VALUE"
    config_path = tmp_path / "zero.json"
    config_path.write_text(
        json.dumps(
            {
                "installation_id": "windows-local",
                "telegram": {
                    "mode": "user_session",
                    "api_id": marker,
                    "api_hash_ref": "telegram.api_hash",
                    "session_ref": "telegram.session",
                },
            }
        ),
        encoding="utf-8",
    )
    output: list[str] = []

    assert (
        tui.run_console_setup_wizard(
            config_path=config_path,
            panel_path=tmp_path / "panel.db",
            input_fn=lambda _prompt: "unused",
            output_fn=output.append,
        )
        is False
    )

    rendered = "\n".join(output)
    assert marker not in rendered
    assert "Setup failed: configuration or storage validation failed" in rendered


def test_portable_setup_hides_sensitive_free_text_prompts(tmp_path, monkeypatch):
    """Credential-shaped values cannot be accidentally echoed through setup prompts."""
    import builtins
    import getpass

    from zero import tui

    normal_answers = iter(["bot"])
    secret_prompts: list[str] = []
    secret_answers = iter(["windows-local", "telegram.bot_token"])

    def read_normal(prompt: str) -> str:
        try:
            return next(normal_answers)
        except StopIteration as exc:
            raise AssertionError(f"sensitive prompt read through input: {prompt}") from exc

    monkeypatch.setattr(builtins, "input", read_normal)
    monkeypatch.setattr(
        getpass,
        "getpass",
        lambda prompt: secret_prompts.append(prompt) or next(secret_answers),
    )

    assert (
        tui.run_console_setup_wizard(
            config_path=tmp_path / "zero.json",
            panel_path=tmp_path / "panel.db",
            output_fn=lambda _line: None,
        )
        is True
    )
    assert secret_prompts == ["Installation id [local]: ", "Bot token reference [telegram.bot_token]: "]


def test_portable_setup_aborts_when_hidden_sensitive_input_is_unavailable(tmp_path, monkeypatch):
    """The console must fail closed rather than falling back to echoed sensitive input."""
    import builtins
    import getpass
    import warnings

    from zero import tui

    normal_answers = iter(["windows-local", "bot"])
    output: list[str] = []
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(normal_answers))

    def unsafe_getpass(_prompt: str) -> str:
        warnings.warn("echo fallback", getpass.GetPassWarning)
        return "telegram.bot_token"

    monkeypatch.setattr(getpass, "getpass", unsafe_getpass)

    assert (
        tui.run_console_setup_wizard(
            config_path=tmp_path / "zero.json",
            panel_path=tmp_path / "panel.db",
            output_fn=output.append,
        )
        is False
    )
    assert "Setup failed: configuration or storage validation failed" in "\n".join(output)


def test_cli_setup_uses_portable_wizard_when_curses_is_unavailable(tmp_path, monkeypatch):
    """The public `zero setup` command must not import-crash on Windows."""
    from zero import cli, tui

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        tui,
        "run_console_setup_wizard",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    monkeypatch.setitem(sys.modules, "curses", None)
    monkeypatch.setattr(cli.sys, "stdin", _TTY())
    monkeypatch.setattr(cli.sys, "stdout", _TTY())

    assert cli.main(["setup", "--config", str(tmp_path / "zero.json"), "--panel-db", str(tmp_path / "panel.db")]) == 0
    assert len(calls) == 1
    assert calls[0]["config_path"] == tmp_path / "zero.json"
    assert calls[0]["panel_path"] == tmp_path / "panel.db"


def test_cli_setup_defaults_to_the_shared_runtime_paths(tmp_path, monkeypatch):
    """CLI setup, TUI setup, and the panel use the same home-owned state paths."""
    from zero import cli, tui

    home = tmp_path / "runtime-home"
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("ZERO_HOME", str(home))
    monkeypatch.delenv("ZERO_CANONICAL_CONFIG", raising=False)
    monkeypatch.setattr(tui, "run_console_setup_wizard", lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setitem(sys.modules, "curses", None)
    monkeypatch.setattr(cli.sys, "stdin", _TTY())
    monkeypatch.setattr(cli.sys, "stdout", _TTY())

    assert cli.main(["setup"]) == 0
    assert calls == [
        {
            "config_path": home / "config" / "zero.json",
            "panel_path": home / "panel.db",
        }
    ]


def test_cli_setup_recovers_when_curses_initialization_fails(tmp_path, monkeypatch):
    """An interactive setup should still work when curses imports but TERM fails."""
    from zero import cli, tui

    class BrokenCurses:
        class error(Exception):
            pass

        @staticmethod
        def wrapper(_callback):
            raise BrokenCurses.error("terminal unavailable")

    calls: list[dict[str, object]] = []
    monkeypatch.setitem(sys.modules, "curses", BrokenCurses)
    monkeypatch.setattr(tui, "run_console_setup_wizard", lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(cli.sys, "stdin", _TTY())
    monkeypatch.setattr(cli.sys, "stdout", _TTY())

    assert cli.main(["setup", "--config", str(tmp_path / "zero.json")]) == 0
    assert len(calls) == 1


def test_canonical_setup_save_works_without_posix_fchmod(tmp_path, monkeypatch):
    """Windows lacks os.fchmod, but canonical setup must still persist safely."""
    import zero.configuration as configuration
    from zero.configuration import ConfigStore

    monkeypatch.delattr(configuration.os, "fchmod", raising=False)
    store = ConfigStore(tmp_path / "zero.json")
    store.save(store.new_config("windows-local"))

    assert store.load().installation_id == "windows-local"


def test_console_chat_store_initializes_without_posix_geteuid(tmp_path, monkeypatch):
    """The portable Chat path must not assume the POSIX-only effective-UID API."""
    import zero.storage as storage
    from zero.storage import ZeroStore

    monkeypatch.delattr(storage.os, "geteuid", raising=False)
    store = ZeroStore(str(tmp_path / "chat.db"))

    assert store.db_path.exists()


def test_cli_exposes_every_documented_tui_panel():
    """The outer CLI and inner TUI must not drift on selectable panels."""
    from zero.cli import build_parser
    from zero.tui import PANEL_NAMES

    parser = build_parser()
    for panel in PANEL_NAMES:
        assert parser.parse_args(["tui", "--print", "--panel", panel]).panel == panel


def test_status_diagnostics_honor_the_explicit_tui_config_path(tmp_path, monkeypatch):
    """`zero tui --config` must not diagnose a different default config file."""
    from zero.configuration import ConfigStore
    from zero.tui import render_status

    home = tmp_path / "home"
    home.mkdir()
    explicit = tmp_path / "chosen.json"
    ConfigStore(explicit).save(ConfigStore.new_config("chosen-installation"))
    default = tmp_path / "default-missing.json"
    monkeypatch.setenv("ZERO_HOME", str(home))
    monkeypatch.setenv("ZERO_CANONICAL_CONFIG", str(default))

    rendered = "\n".join(render_status(store_path=home / "zero.db", config_path=explicit))

    assert f"✓ canonical_config: {explicit}" in rendered
    assert f"missing: {default}" not in rendered


def test_portable_console_tui_can_start_setup_wizard(tmp_path, monkeypatch):
    """The fallback preserves the TUI Setup action instead of becoming read-only."""
    from zero import tui

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        tui,
        "run_console_setup_wizard",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    commands = iter(["setup", "q"])
    output: list[str] = []

    assert (
        tui.run_console_tui(
            initial_panel="status",
            store_path=tmp_path / "zero.db",
            config_path=tmp_path / "zero.json",
            runtime_path=tmp_path / "zero.yaml",
            tail=50,
            input_fn=lambda _prompt: next(commands),
            output_fn=output.append,
        )
        == 0
    )

    assert len(calls) == 1
    assert calls[0]["config_path"] == tmp_path / "zero.json"
    assert any("Setup complete" in line for line in output)


def test_portable_console_tui_creates_a_backup_only_on_explicit_refresh(tmp_path, monkeypatch):
    """The portable backup panel retains the curses UI's explicit-action rule."""
    from zero import tui

    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "zero.db"
    with sqlite3.connect(store) as db:
        db.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        db.execute("INSERT INTO settings VALUES ('k', 'v')")
    monkeypatch.setenv("ZERO_HOME", str(home))
    commands = iter(["4", "r", "q"])

    assert (
        tui.run_console_tui(
            initial_panel="status",
            store_path=store,
            config_path=tmp_path / "zero.json",
            runtime_path=tmp_path / "zero.yaml",
            tail=50,
            input_fn=lambda _prompt: next(commands),
            output_fn=lambda _line: None,
        )
        == 0
    )

    backups = list((home / "backups").glob("zero-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT value FROM settings WHERE key='k'").fetchone()[0] == "v"


def test_noninteractive_missing_curses_backup_panel_has_no_write_side_effect(tmp_path, monkeypatch):
    """A fallback report may inspect Backup, but only `--print` may create it."""
    from zero import tui

    home = tmp_path / "home"
    store = tmp_path / "zero.db"
    with sqlite3.connect(store) as db:
        db.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        db.execute("INSERT INTO settings VALUES ('k', 'v')")
    monkeypatch.setenv("ZERO_HOME", str(home))
    monkeypatch.setitem(sys.modules, "curses", None)
    monkeypatch.setattr(tui.sys, "stdin", io.StringIO())
    monkeypatch.setattr(tui.sys, "stdout", io.StringIO())

    assert tui.main(["--panel", "backup", "--store", str(store)]) == 0
    assert not list((home / "backups").glob("zero-*.db"))


def test_noninteractive_curses_failure_backup_panel_has_no_write_side_effect(tmp_path, monkeypatch):
    """A failed curses initialization must follow the same explicit backup rule."""
    from zero import tui

    class BrokenCurses:
        class error(Exception):
            pass

        @staticmethod
        def wrapper(_callback):
            raise BrokenCurses.error("terminal unavailable")

    home = tmp_path / "home"
    store = tmp_path / "zero.db"
    with sqlite3.connect(store) as db:
        db.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        db.execute("INSERT INTO settings VALUES ('k', 'v')")
    monkeypatch.setenv("ZERO_HOME", str(home))
    monkeypatch.setitem(sys.modules, "curses", BrokenCurses)
    monkeypatch.setattr(tui.sys, "stdin", io.StringIO())
    monkeypatch.setattr(tui.sys, "stdout", io.StringIO())

    assert tui.main(["--panel", "backup", "--store", str(store)]) == 0
    assert not list((home / "backups").glob("zero-*.db"))


def test_portable_console_tui_can_execute_a_chat_message(tmp_path, monkeypatch):
    """The no-curses fallback retains the functional Chat panel path."""
    from zero import tui

    prompts: list[str] = []

    class Runtime:
        async def ask(self, prompt: str) -> str:
            prompts.append(prompt)
            return "completed answer"

    runtime = Runtime()
    monkeypatch.setattr(tui, "build_chat_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr(tui, "render_chat", lambda _runtime: ["Zero Chat", "completed answer"])
    commands = iter(["chat hello from Windows", "q"])
    output: list[str] = []

    assert (
        tui.run_console_tui(
            initial_panel="status",
            store_path=tmp_path / "zero.db",
            config_path=tmp_path / "zero.json",
            runtime_path=tmp_path / "zero.yaml",
            tail=50,
            input_fn=lambda _prompt: next(commands),
            output_fn=output.append,
        )
        == 0
    )

    assert prompts == ["hello from Windows"]
    assert any("Zero is thinking" in line for line in output)
    assert any("Response complete" in line for line in output)
