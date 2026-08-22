"""Make the repository package importable in an uninstalled test checkout."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_EXAMPLE = ROOT / "config" / "zero.example.yaml"
CONFIG_RUNTIME = ROOT / "config" / "zero.yaml"

if not CONFIG_RUNTIME.exists() and CONFIG_EXAMPLE.exists():
    # Hermetic test runs: derive the runtime config from the committed example
    # so tests never depend on a developer's local setup.
    CONFIG_RUNTIME.write_text(CONFIG_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
PANEL_DIR = ROOT / "panel"
RUNTIME_DIR = ROOT / "runtime"
