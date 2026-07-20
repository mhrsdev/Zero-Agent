from types import SimpleNamespace

import pytest

from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage, RouteResult
from zero.router import IndependentRouter
from zero.storage import ZeroStore


@pytest.mark.asyncio
async def test_router_parses_gemini_function_call(tmp_path):
    config = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})})
    router = IndependentRouter(config)
    captured = {}

    def fake_google(model, key, prompt, max_tokens, *, search=False, tools=None):
        captured['tools'] = tools
        return '', {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'read_knowledge', 'args': {'query': 'Iran news'}}}]}}]}

    router._google_call = fake_google
    router.config.router.normal_primary = 'gemini'
    result = await router.complete_with_tools('question', [{'name': 'read_knowledge', 'description': 'read', 'parameters': {'type': 'object'}}])
    assert captured['tools'][0]['name'] == 'read_knowledge'
    assert result.metadata['tool_calls'][0]['name'] == 'read_knowledge'
    assert result.metadata['tool_calls'][0]['arguments']['query'] == 'Iran news'


@pytest.mark.asyncio
async def test_brain_executes_knowledge_tool(tmp_path):
    config = ZeroConfig.load('/root/zero/config/zero.example.yaml')
    config = config.model_copy(update={'memory': config.memory.model_copy(update={'db_path': str(tmp_path / 'zero.db')})})
    store = ZeroStore(config.memory.db_path)

    class FakeKnowledge:
        def __init__(self): self.queries = []
        async def retrieval_context(self, query, *, policy):
            self.queries.append(query)
            return '[KNOWLEDGE_ITEM]\ntitle: Iran update\nsummary: verified\n[/KNOWLEDGE_ITEM]'

    class FakeRouter:
        keys = []
        def __init__(self): self.final_prompts = []
        async def complete_with_tools(self, prompt, tools, *, max_output_tokens=700):
            return RouteResult(text='', provider='test', model='test', attempts=1, metadata={'tool_calls': [{'name': 'read_knowledge', 'arguments': {'query': 'Iran news', 'max_results': 2}}]})
        async def complete(self, prompt, *, max_output_tokens=700):
            self.final_prompts.append(prompt)
            return RouteResult(text='پاسخ مبتنی بر خبر', provider='test', model='test', attempts=1)

    knowledge, router = FakeKnowledge(), FakeRouter()
    brain = ZeroBrain(config, store, router, knowledge=knowledge)
    message = IncomingMessage(chat_id=-1, chat_title='test', sender_id=2, sender_label='u', text='اخبار ایران چیست؟')
    reply = await brain._generate_with_knowledge_tool(message, 'base prompt', -1)
    assert reply == 'پاسخ مبتنی بر خبر'
    assert knowledge.queries == ['Iran news']
    assert '[TOOL_RESULT read_knowledge]' in router.final_prompts[0]
