from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zero.config import ZeroConfig
from zero.storage import ZeroStore

CONFIG_PATH = '/root/zero/config/zero.yaml'


def main() -> None:
    config = ZeroConfig.load(CONFIG_PATH)
    ZeroStore(config.memory.db_path)
    print('DB initialized at', config.memory.db_path)


if __name__ == '__main__':
    main()
