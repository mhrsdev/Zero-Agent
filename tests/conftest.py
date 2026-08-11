"""Make the repository package importable in an uninstalled test checkout."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_EXAMPLE = ROOT / "config" / "zero.example.yaml"
CONFIG_RUNTIME = ROOT / "config" / "zero.yaml"
PANEL_DIR = ROOT / "panel"
RUNTIME_DIR = ROOT / "runtime"
