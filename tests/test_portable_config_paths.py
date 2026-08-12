from pathlib import Path

from zero.config import ZeroConfig
from zero.storage import ZeroStore


def test_example_config_resolves_runtime_paths_from_zero_home(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("ZERO_HOME", str(runtime))
    project_root = Path(__file__).resolve().parents[1]
    config = ZeroConfig.load(project_root / "config" / "zero.example.yaml")

    store = ZeroStore(config.memory.db_path)

    assert Path(config.memory.db_path).parent == runtime / "state"
    assert Path(config.logs.listener_log).parent == runtime / "logs"
    assert Path(config.logs.panel_log).parent == runtime / "logs"
    assert Path(config.logs.router_log).parent == runtime / "logs"
    assert store.db_path.parent == runtime / "state"


def test_test_suite_does_not_require_a_root_zero_checkout():
    project_root = Path(__file__).resolve().parents[1]
    legacy_root = "/" + "root/zero"
    allowed_literal_contract_tests = {"test_docker_build.py", "test_security_hardening.py"}
    offenders = []
    for path in (project_root / "tests").rglob("*.py"):
        if path.name in allowed_literal_contract_tests:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if legacy_root in line:
                offenders.append(f"{path.relative_to(project_root)}:{line_number}")
    assert not offenders, f"legacy checkout paths remain: {offenders}"


def test_production_python_has_no_legacy_root_checkout_dependency():
    project_root = Path(__file__).resolve().parents[1]
    legacy_root = "/" + "root/zero"
    intentional_scanners = {Path("scripts/verify_public_artifact.py")}
    offenders = []
    for source_root in (project_root / "zero", project_root / "scripts"):
        for path in source_root.rglob("*.py"):
            relative = path.relative_to(project_root)
            if relative in intentional_scanners:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if legacy_root in line:
                    offenders.append(f"{relative}:{line_number}")
    assert not offenders, f"production legacy checkout paths remain: {offenders}"


def test_runtime_config_default_follows_zero_home(monkeypatch, tmp_path):
    from zero.runtime_config import runtime_config_path

    monkeypatch.delenv("ZERO_CONFIG_PATH", raising=False)
    monkeypatch.setenv("ZERO_HOME", str(tmp_path / "portable-home"))
    assert Path(runtime_config_path()) == tmp_path / "portable-home" / "config" / "zero.yaml"


def test_zero_home_resolves_relative_runtime_home(monkeypatch, tmp_path):
    from zero.paths import zero_home

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZERO_HOME", "portable-home")

    assert zero_home() == tmp_path / "portable-home"
    assert zero_home().is_absolute()


def test_panel_and_listener_share_the_portable_runtime_config_default():
    root = Path(__file__).resolve().parents[1]
    expected = "CONFIG_PATH = Path(runtime_config_path())"

    assert expected in (root / "scripts" / "run_listener.py").read_text(encoding="utf-8")
    assert expected in (root / "scripts" / "run_panel.py").read_text(encoding="utf-8")


def test_panel_setup_state_path_follows_runtime_home(monkeypatch, tmp_path):
    from zero.paths import panel_state_path

    monkeypatch.setenv("ZERO_HOME", str(tmp_path / "runtime-home"))

    assert panel_state_path() == tmp_path / "runtime-home" / "panel.db"


def test_panel_composition_uses_the_shared_setup_state_path():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_panel.py").read_text(encoding="utf-8")

    assert "panel_store=PanelStore(" in source
    assert "panel_state_path()," in source
