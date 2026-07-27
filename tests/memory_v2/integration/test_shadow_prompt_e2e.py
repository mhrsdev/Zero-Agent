import pytest
from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import Decision, IncomingMessage, RouteResult
from zero.security import classify_intent
from zero.storage import ZeroStore
from zero.memory_v3 import MemoryV3Item

class RecordingRouter:
    keys=[]
    def __init__(self): self.prompts=[]
    async def complete(self,prompt,*,max_output_tokens=700): self.prompts.append(prompt); return RouteResult(text='ok',provider='test',model='test',attempts=1)

async def turn(tmp_path, monkeypatch, *, enabled=True, shadow=False):
    monkeypatch.setenv('ZERO_MEMORY_V3_DB',str(tmp_path/'v3.db'));monkeypatch.setenv('ZERO_MEMORY_V3_ENABLED',str(enabled).lower());monkeypatch.setenv('ZERO_MEMORY_V3_SHADOW',str(shadow).lower())
    cfg=ZeroConfig.load('/root/zero/config/zero.example.yaml');cfg=cfg.model_copy(update={'memory':cfg.memory.model_copy(update={'db_path':str(tmp_path/'v1.db')})})
    store=ZeroStore(cfg.memory.db_path); router=RecordingRouter(); brain=ZeroBrain(cfg,store,router)
    m=IncomingMessage(-1,'g',7,'u','zero marker query',mention_zero=True,message_id=1)
    await store.add_long_memory(-1,'marker','V1_MUST_NOT_REACH_PROMPT',created_by=7,subject_user_id=7,confidence=.99)
    await brain.memory.put(MemoryV3Item.personal(chat_id=-1,user_id=7,content='V3_CANONICAL_MEMORY_MARKER zero marker query',importance=1,confidence=1))
    d,reply=await brain._handle_no_media(m,Decision(True,'test'),classify_intent(m.text,m.reply_text));return router.prompts[-1],reply,brain,m

@pytest.mark.asyncio
async def test_normal_prompt_contains_only_v3_memory(tmp_path,monkeypatch):
    prompt,reply,brain,m=await turn(tmp_path,monkeypatch)
    assert reply=='ok' and prompt.count('V3_CANONICAL_MEMORY_MARKER')==1
    assert 'V1_MUST_NOT_REACH_PROMPT' not in prompt
    assert await brain.memory_v3.session_state(m) is not None

@pytest.mark.asyncio
async def test_v2_environment_cannot_select_another_runtime(tmp_path,monkeypatch):
    monkeypatch.setenv('ZERO_MEMORY_V2_ENABLED','true')
    monkeypatch.setenv('ZERO_MEMORY_V2_SHADOW','false')
    prompt,reply,brain,_=await turn(tmp_path,monkeypatch)
    assert reply=='ok' and 'V3_CANONICAL_MEMORY_MARKER' in prompt
    assert not hasattr(brain, 'memory_v2')

@pytest.mark.asyncio
async def test_v1_runtime_flag_is_disabled_for_normal_requests(tmp_path,monkeypatch):
    _,reply,brain,_=await turn(tmp_path,monkeypatch)
    assert reply=='ok'
    assert brain.v1_memory_runtime_enabled is False
