from conftest import CONFIG_EXAMPLE
from zero.config import ZeroConfig
from zero.web import HybridWeb
from zero.telegram_search import TelegramSearchClient


def test_web_is_disabled_by_default():
    cfg = ZeroConfig.load(CONFIG_EXAMPLE)
    web = HybridWeb(cfg)
    assert web.enabled() is False


def test_telegram_search_is_disabled_by_default():
    cfg = ZeroConfig.load(CONFIG_EXAMPLE)
    tg = TelegramSearchClient(cfg)
    assert tg.enabled() is False
