from conftest import CONFIG_EXAMPLE
from zero.config import ZeroConfig
from zero.models import IncomingMessage
from zero.triggers import is_triggered, strip_trigger


def test_zero_triggers_for_words_and_command():
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    assert is_triggered(IncomingMessage(1, 'g', 2, 'u', 'زیرو بیا', False, False), config)
    assert is_triggered(IncomingMessage(1, 'g', 2, 'u', '/zero hello', False, False), config)
    assert is_triggered(IncomingMessage(1, 'g', 2, 'u', 'test', True, False), config)


def test_strip_trigger_removes_command_and_username():
    assert strip_trigger('/zero@APZeroBot سلام', 'APZeroBot') == 'سلام'
