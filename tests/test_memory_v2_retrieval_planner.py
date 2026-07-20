import asyncio,json,time
from zero.memory_v2.retrieval_planner import parse,window
from zero.brain import ZeroBrain
from zero.config import ZeroConfig
from zero.models import IncomingMessage,RouteResult
from zero.storage import ZeroStore


def test_plan_schema_rejects_scope_escape_and_bad_operation():
 assert parse('{"version":1,"needs_memory":true,"operation":"sql","actors":[],"subjects":[],"time":{"kind":"last_week"},"evidence_mode":"historical_messages"}',set()) is None
 p=parse('{"version":1,"needs_memory":true,"operation":"find_statements","actors":["mention:0"],"subjects":["self"],"time":{"kind":"last_week"},"evidence_mode":"historical_messages"}',{'self','mention:0'})
 assert p and p.operation=='find_statements' and p.time_kind=='last_week' and window('last_week')

class Router:
 keys=[]
 async def complete_structured(self,*a,**k): return RouteResult(json.dumps({'version':1,'needs_memory':True,'operation':'find_statements','actors':['mention:0'],'subjects':['self'],'time':{'kind':'last_week'},'evidence_mode':'historical_messages'}), 'test','test',1)
 async def complete(self,prompt,*a,**k):
  if 'Return JSON only' in prompt: return RouteResult(json.dumps({'version':1,'needs_memory':True,'operation':'find_statements','actors':['mention:0'],'subjects':['self'],'time':{'kind':'last_week'},'evidence_mode':'historical_messages'}), 'test','test',1)
  return RouteResult('ok','test','test',1)

async def _evidence(tmp_path,monkeypatch):
 monkeypatch.setenv('ZERO_MEMORY_V2_PLANNER_ENABLED','true')
 cfg=ZeroConfig.load('/root/zero/config/zero.example.yaml');cfg=cfg.model_copy(update={'memory':cfg.memory.model_copy(update={'db_path':str(tmp_path/'v1.db')})})
 store=ZeroStore(cfg.memory.db_path);brain=ZeroBrain(cfg,store,Router())
 await store.append_recent(-1,2,'member','user','bounded historical statement',telegram_message_id=9)
 msg=IncomingMessage(-1,'g',1,'speaker','@member هفته پیش درباره من چی گفت؟',resolved_mention_user_ids=(2,))
 evidence,meta=await brain._planned_memory_context(msg,'t')
 assert 'bounded historical statement' in evidence and meta['actors']==1 and meta['selected_count']==1

def test_historical_evidence_is_actor_scoped(tmp_path,monkeypatch): asyncio.run(_evidence(tmp_path,monkeypatch))
