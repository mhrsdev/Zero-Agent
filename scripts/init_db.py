from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zero.config import ZeroConfig
from zero.runtime_config import load_effective_config, runtime_config_path
from zero.storage import ZeroStore

CONFIG_PATH = runtime_config_path()


def main() -> None:
    config = load_effective_config(CONFIG_PATH, ZeroConfig)
    ZeroStore(config.memory.db_path)
    print('DB initialized at', config.memory.db_path)


if __name__ == '__main__':
    main()
