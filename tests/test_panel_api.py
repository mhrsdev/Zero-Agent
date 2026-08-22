from __future__ import annotations
from conftest import PANEL_DIR, ROOT

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zero.experience_memory import ExperienceMemory
from zero.panel_api import PanelAPI
from zero.procedural_memory import ProceduralMemory
from zero.semantic_memory import SemanticUserMemory
from zero.storage import ZeroStore
from zero.world_model import WorldModel


class FakeBot:
    def __init__(self): self.messages=[]
    async def send_message(self,user_id,text): self.messages.append((user_id,text))
    async def get_chat(self,username): return SimpleNamespace(id=123456789)


class FakeRouter:
    def status(self):
        return {'providers':{'openrouter':{'model':'safe/model','keys':[{'healthy':True,'enabled':True,'cooldown':False}]},'gemini':{'model':'safe-google','keys':[]}}}


class FakeKnowledge:
    async def status(self): return {'running':False,'backend':'remote'}
    async def schedule_status(self): return {'enabled':True,'backend':'remote'}
    async def run_nightly(self,**kwargs): return {'status':'dry_run','kwargs':kwargs}


class FakeJobs:
    async def list_jobs(self,actor=None): return [{'job_id':'job-1','title':'safe','state':'enabled','approval_state':'approved'}]
    async def status(self,job_id,actor=None): return {'job_id':job_id,'state':'enabled'}
    async def logs(self,job_id,limit=10,actor=None): return []
    async def set_state(self,actor,job_id,state): return None
    async def approve(self,actor,job_id): return {'job_id':job_id,'approved':True}


def config(tmp_path):
    log=tmp_path/'panel.log';log.write_text('ERROR trace_id=trace-ok token=test-placeholder\n')
    return SimpleNamespace(
        owner_user_id=111111111,owner_username='owner',panel_viewer_usernames=['ysnrfd3','PYT313'],panel_viewer_user_ids=[123456789,987654321],
        memory=SimpleNamespace(db_path=str(tmp_path/'zero.db'),recent_messages_limit=80,long_term_limit=120),
        router=SimpleNamespace(normal_primary='openrouter',normal_fallback='gemini',strategy='weighted_lru',search_provider='gemini',providers=SimpleNamespace(openrouter=SimpleNamespace(quota_scope='project'),gemini=SimpleNamespace(quota_scope='project'))),
        web=SimpleNamespace(enabled=True,google_grounding_enabled=True),telegram_search=SimpleNamespace(archived=True),
        listener=SimpleNamespace(account_username='zero',allowed_group_usernames=['safe']),
        policy=SimpleNamespace(model_dump=lambda:{'user_max_replies_per_day':120}),reactions=SimpleNamespace(model_dump=lambda:{'enabled':False}),stickers=SimpleNamespace(model_dump=lambda:{'enabled':True}),
        management_bot=SimpleNamespace(token_file=str(tmp_path/'bot.env')),
        logs=SimpleNamespace(listener_log=str(log),panel_log=str(log),router_log=str(log)),
    )


@pytest.fixture
async def panel(tmp_path):
    cfg=config(tmp_path);Path(cfg.management_bot.token_file).write_text('configured');Path(cfg.management_bot.token_file).chmod(0o600)
    store=ZeroStore(cfg.memory.db_path);await store.append_recent(10,20,'کاربر','user','پیام تست')
    semantic=SemanticUserMemory(cfg.memory.db_path);candidate=semantic.candidate(chat_id=10,sender_id=20,category='interest',key='topic',value='AI',confidence=.9,evidence_message_ids=[1]);semantic.approve(candidate,cfg.owner_user_id)
    bot=FakeBot();api=PanelAPI(cfg,store,FakeRouter(),bot,static_dir=str(PANEL_DIR),services={'knowledge':FakeKnowledge(),'jobs':FakeJobs(),'semantic':semantic,'experience':ExperienceMemory(cfg.memory.db_path),'procedure':ProceduralMemory(cfg.memory.db_path),'world':WorldModel(cfg.memory.db_path)})
    client=TestClient(TestServer(api.app));await client.start_server()
    yield client,api,bot,cfg
    await client.close()


async def login(panel):
    client,api,bot,cfg=panel
    denied=await client.post('/api/auth/request',json={'identity':'999999'});assert denied.status==403
    requested=await client.post('/api/auth/request',json={'identity':str(cfg.owner_user_id)});assert requested.status==200
    code=re.search(r'\b(\d{6})\b',bot.messages[-1][1]).group(1)
    verified=await client.post('/api/auth/verify',json={'identity':str(cfg.owner_user_id),'code':code});assert verified.status==200
    body=await verified.json();cookie=verified.cookies['zero_session'].value
    return {'Cookie':f'zero_session={cookie}','X-CSRF-Token':body['csrf']},body


@pytest.mark.asyncio
async def test_panel_production_path_auth_security_and_management_e2e(panel):
    client,api,bot,cfg=panel
    assert (await client.get('/api/dashboard')).status==401
    headers,_=await login(panel)
    assert (await client.get('/api/dashboard',headers=headers)).status==200
    assert (await client.get('/api/chats?q=تست',headers=headers)).status==200
    memory=await (await client.get('/api/memory/semantic',headers=headers)).json();mid=memory['items'][0]['id']
    assert (await client.get(f'/api/memory/semantic/{mid}',headers=headers)).status==200
    assert (await client.get('/api/knowledge',headers=headers)).status==200
    assert (await client.get('/api/router',headers=headers)).status==200
    logs=await (await client.get('/api/logs?level=ERROR&trace_id=trace-ok',headers=headers)).json();assert logs['items'] and 'hidden-secret' not in logs['items'][0]['line']
    assert (await client.get('/api/jobs/job-1',headers=headers)).status==200
    assert (await client.get('/api/realtime',headers=headers)).status==200
    bad={'Cookie':headers['Cookie'],'X-CSRF-Token':'wrong'}
    assert (await client.post('/api/settings/web_enabled',headers=bad,json={'value':False})).status==403
    assert (await client.post('/api/settings/web_enabled',headers=headers,json={'value':False})).status==200
    assert (await client.post('/api/auth/logout-all',headers=headers,json={'confirm':True})).status==200
    assert (await client.get('/api/dashboard',headers=headers)).status==401


@pytest.mark.asyncio
async def test_logout_invalidates_current_session(panel):
    client,*_=panel;headers,_=await login(panel)
    assert (await client.post('/api/auth/logout',headers=headers)).status==200
    assert (await client.get('/api/dashboard',headers=headers)).status==401


@pytest.mark.asyncio
async def test_readiness_bounded_pagination_sse_and_secret_protection(panel):
    client,api,bot,cfg=panel
    health=await client.get('/api/health');assert health.status==200;assert (await health.json())['status']=='ready'
    headers,_=await login(panel)
    chats=await (await client.get('/api/chats?size=10000',headers=headers)).json();assert chats['size']==100
    memory=await (await client.get('/api/memory/semantic?size=10000',headers=headers)).json();assert memory['size']==100
    stream=await client.get('/api/realtime',headers=headers);assert stream.status==200;assert stream.headers['Content-Type'].startswith('text/event-stream');stream.close()
    settings=await (await client.get('/api/settings',headers=headers)).json();assert settings['secrets']['management_bot']['configured'] is True
    assert 'tgsearch_enabled' not in settings['editable']
    assert 'telegram_archived' not in str(settings)
    payload=str(settings);assert 'configured' in payload and 'hidden-secret' not in payload


def test_panel_routes_have_no_direct_sql_boundary():
    source=(ROOT / "zero" / "panel_api.py").read_text(encoding="utf-8")
    tree=ast.parse(source)
    assert 'sqlite3' not in source
    assert '.execute(' not in source
    assert not any(isinstance(n,ast.Constant) and isinstance(n.value,str) and re.search(r'\b(SELECT|UPDATE|DELETE|INSERT|PRAGMA)\b',n.value,re.I) for n in ast.walk(tree))


def test_production_unit_keeps_hardening_and_external_config():
    unit=(ROOT / "deploy" / "zero-panel.service").read_text()
    assert 'User=zero' in unit and 'Group=zero' in unit
    assert 'ProtectHome=read-only' in unit and 'ProtectSystem=strict' in unit and 'UMask=0077' in unit
    assert 'ZERO_CONFIG_PATH=/opt/zero/config/panel.yaml' in unit
    assert 'ZERO_PANEL_HOST=127.0.0.1' in unit and 'ZERO_PANEL_PORT=8787' in unit
    assert 'User=root' not in unit and 'chmod 777' not in unit


@pytest.mark.asyncio
async def test_viewer_can_read_but_cannot_mutate(panel):
    client,api,bot,cfg=panel
    requested=await client.post('/api/auth/request',json={'identity':'@ysnrfd3'});assert requested.status==200
    code=next(re.search(r'\b(\d{6})\b',text).group(1) for _,text in bot.messages if 'secure login code' in text or 'login code' in text.lower())
    verified=await client.post('/api/auth/verify',json={'identity':'@ysnrfd3','code':code},headers={'X-Forwarded-For':'203.0.113.7'});assert verified.status==200
    body=await verified.json();cookie=verified.cookies['zero_session'].value
    headers={'Cookie':f'zero_session={cookie}','X-CSRF-Token':body['csrf']}
    me=await (await client.get('/api/auth/me',headers=headers)).json();assert me['role']=='viewer'
    alert_target,alert=bot.messages[-1]
    assert alert_target==cfg.owner_user_id
    assert '@ysnrfd3' in alert and '203.0.113.7' in alert and ('Time:' in alert or 'time:' in alert)
    assert (await client.get('/api/dashboard',headers=headers)).status==200
    assert (await client.get('/api/settings',headers=headers)).status==200
    assert (await client.post('/api/settings/web_enabled',headers=headers,json={'value':False})).status==403
    assert (await client.post('/api/auth/logout-all',headers=headers,json={'confirm':True})).status==403
