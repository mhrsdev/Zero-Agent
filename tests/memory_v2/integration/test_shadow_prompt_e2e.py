import pytest
from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import Decision, IncomingMessage, RouteResult
from zero.security import classify_intent
from zero.storage import ZeroStore
from zero.memory_v2.service import MemoryItem

class RecordingRouter:
    keys=[]
    def __init__(self): self.prompts=[]
    async def complete(self,prompt,*,max_output_tokens=700): self.prompts.append(prompt); return RouteResult(text='ok',provider='test',model='test',attempts=1)

async def turn(tmp_path, monkeypatch, *, enabled, shadow, break_v2=False):
    monkeypatch.setenv('ZERO_MEMORY_V2_DB',str(tmp_path/'v2.db'));monkeypatch.setenv('ZERO_MEMORY_V2_ENABLED',str(enabled).lower());monkeypatch.setenv('ZERO_MEMORY_V2_SHADOW',str(shadow).lower())
    cfg=ZeroConfig.load('/root/zero/config/zero.example.yaml');cfg=cfg.model_copy(update={'memory':cfg.memory.model_copy(update={'db_path':str(tmp_path/'v1.db')})})
    store=ZeroStore(cfg.memory.db_path); router=RecordingRouter(); brain=ZeroBrain(cfg,store,router)
    m=IncomingMessage(-1,'g',7,'u','zero marker query',mention_zero=True,message_id=1)
    await store.add_long_memory(-1,'marker','V1_ACTIVE_MEMORY_MARKER_4A92',created_by=7,subject_user_id=7,confidence=.99)
    await brain.memory_v2.put(MemoryItem('', 'fact','group_user','SHADOW_ONLY_MEMORY_MARKER_7F31 zero marker query','shadow_only_memory_marker_7f31 zero marker query',7,-1,group_id=-1,importance=1,confidence=1))
    if break_v2: monkeypatch.setattr(brain.memory_v2,'_search_sync',lambda *a: (_ for _ in ()).throw(RuntimeError('redacted')))
    d,reply=await brain._handle_no_media(m,Decision(True,'test'),classify_intent(m.text,m.reply_text));return router.prompts[-1],reply,brain,m

@pytest.mark.asyncio
async def test_shadow_payload_never_contains_v2_marker(tmp_path,monkeypatch):
    prompt,reply,brain,m=await turn(tmp_path,monkeypatch,enabled=False,shadow=True)
    assert reply=='ok' and prompt.count('V1_ACTIVE_MEMORY_MARKER_4A92')==1 and 'SHADOW_ONLY_MEMORY_MARKER_7F31' not in prompt
    assert await brain.memory_v2.session_state(m) is not None

@pytest.mark.asyncio
async def test_active_payload_contains_only_v2_marker(tmp_path,monkeypatch):
    prompt,reply,_,_=await turn(tmp_path,monkeypatch,enabled=True,shadow=False)
    assert reply=='ok' and prompt.count('SHADOW_ONLY_MEMORY_MARKER_7F31')==1 and 'V1_ACTIVE_MEMORY_MARKER_4A92' not in prompt

@pytest.mark.asyncio
async def test_shadow_failure_preserves_v1_payload(tmp_path,monkeypatch):
    prompt,reply,_,_=await turn(tmp_path,monkeypatch,enabled=False,shadow=True,break_v2=True)
    assert reply=='ok' and 'V1_ACTIVE_MEMORY_MARKER_4A92' in prompt and 'RuntimeError' not in prompt
