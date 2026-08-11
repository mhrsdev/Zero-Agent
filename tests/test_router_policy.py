from conftest import CONFIG_EXAMPLE
from zero.config import ZeroConfig
from zero.router import IndependentRouter


def test_router_prefers_openrouter_for_simple_prompt():
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    router = IndependentRouter(config)
    order = router._route_order('hello')
    assert order[0][0] == 'openrouter'


def test_router_prefers_gemini_for_complex_prompt():
    config = ZeroConfig.load(CONFIG_EXAMPLE)
    router = IndependentRouter(config)
    order = router._route_order('x' * 300)
    assert order[0][0] == 'gemini'
