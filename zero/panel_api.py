from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from .fsprivacy import path_is_private
from .runtime_control import listener_status
from .panel_store import DuplicateAdminError, PanelStore
from .paths import zero_home_path

_PAGE_MAX = 100
_SECRET = re.compile(r"(?i)(api[_ -]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;]+)|\b[A-Za-z0-9_-]{32,}\b")
_ALLOWED_SETTINGS = {
    'web_enabled','vision_enabled','mode','limit_challenge_enabled',
    'reactions_enabled','reaction_chance_percent','reaction_max_per_hour','reaction_cooldown_seconds',
    'social_enabled','knowledge_backend','knowledge_auto_enabled','knowledge_runtime_limit_minutes',
}
_MEMORY_TABLES = {
    'short':'short_term_context','medium':'medium_term_memory','long':'long_term_memory',
    'semantic':'semantic_user_memory','semantic-candidates':'semantic_user_memory_candidates',
    'experience':'experience_memory','experience-candidates':'experience_memory_candidates',
    'procedural':'procedural_memory','procedural-candidates':'procedural_memory_candidates',
    'world':'world_entities','world-relations':'world_relations',
}


class PanelAPI:
    """Authenticated owner-only web adapter over existing Zero services."""

    def __init__(self, config, store, router, bot, *, static_dir: str | Path, services: dict[str, Any] | None = None, panel_store: PanelStore | None = None):
        self.config, self.store, self.router, self.bot = config, store, router, bot
        self.services = services or {}
        self.panel_store = panel_store
        self.static_dir = Path(static_dir)
        self.pending: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.login_audit: deque[dict[str, Any]] = deque(maxlen=500)
        self._request_hits: dict[str, deque[float]] = {}
        self.app = web.Application(middlewares=[self._security_headers, self._rate_limit])
        self.app.add_routes([
            web.get('/panel', self._index), web.get('/panel/{name:.*}', self._static), web.get('/api/health', self._health),
            web.post('/api/auth/request', self._request_code), web.post('/api/auth/verify', self._verify),
            web.get('/api/auth/me', self._me), web.post('/api/auth/logout', self._logout),
            web.post('/api/auth/logout-all', self._logout_all),
            web.post('/api/local/auth/bootstrap', self._local_bootstrap), web.post('/api/local/auth/login', self._local_login),
            web.get('/api/local/auth/me', self._local_me), web.post('/api/local/auth/change-password', self._local_change_password), web.post('/api/local/auth/logout', self._local_logout),
            web.get('/api/local/setup', self._local_setup), web.post('/api/local/setup/{step}', self._local_setup_step), web.post('/api/local/setup/skip', self._local_setup_skip),
            web.get('/api/local/dashboard', self._local_dashboard),
            web.get('/api/dashboard', self._dashboard), web.get('/api/realtime', self._realtime),
            web.get('/api/chats', self._chats), web.get(r'/api/chats/{item_id:\d+}', self._chat_detail),
            web.get('/api/memory/{layer}', self._memory_list), web.get('/api/memory/{layer}/{item_id}', self._memory_detail),
            web.post('/api/memory/{layer}/{item_id}/{action}', self._memory_action),
            web.get('/api/knowledge', self._knowledge), web.get(r'/api/knowledge/items/{item_id:\d+}', self._knowledge_item),
            web.post('/api/knowledge/run', self._knowledge_run),
            web.get('/api/router', self._router), web.get('/api/logs', self._logs), web.get('/api/logs/download', self._logs_download), web.get('/api/logs/stream', self._log_stream),
            web.get('/api/jobs', self._jobs), web.get('/api/jobs/{job_id}', self._job_detail), web.post('/api/jobs/{job_id}/{action}', self._job_action),
            web.get('/api/users', self._users), web.get('/api/sessions', self._session_list), web.post('/api/sessions/{session_id}/revoke', self._session_revoke),
            web.get('/api/settings', self._settings), web.post('/api/settings/{key}', self._setting_update),
        ])

    @web.middleware
    async def _security_headers(self, request, handler):
        response = await handler(request)
        response.headers.update({
            'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
            'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy':"default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; worker-src 'self'; connect-src 'self'; img-src 'self' data:",
        })
        return response

    @web.middleware
    async def _rate_limit(self, request, handler):
        if not request.path.startswith('/api/'):
            return await handler(request)
        now=time.time(); bucket='auth' if request.path.startswith('/api/auth/') else 'api'; key=f'{request.remote or "local"}:{bucket}'; q=self._request_hits.setdefault(key,deque())
        while q and q[0] < now-60: q.popleft()
        limit=30 if bucket=='auth' else 600
        if len(q)>=limit: return self._json_error('Rate limit exceeded.',429)
        q.append(now); return await handler(request)

    def _json_error(self, message, status=400): return web.json_response({'error':message},status=status)

    def _local_session(self, request):
        if not self.panel_store:
            return None
        raw = request.cookies.get('zero_admin_session')
        return self.panel_store.get_session(raw) if raw else None

    def _local_require(self, request, *, csrf=False):
        session = self._local_session(request)
        if not session:
            raise web.HTTPUnauthorized(text=json.dumps({'error': 'Authentication required'}), content_type='application/json')
        if csrf and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf_token']):
            raise web.HTTPForbidden(text=json.dumps({'error': 'Invalid CSRF token'}), content_type='application/json')
        return session

    async def _local_bootstrap(self, request):
        if not self.panel_store:
            return self._json_error('Local authentication is unavailable', 503)
        data = await self._body(request)
        try:
            username, password = str(data.get('username', '')), str(data.get('password', ''))
            admin_id = self.panel_store.create_admin(username, password, must_change_password=(username.strip().lower() == 'admin' and password == 'Admin'))
        except DuplicateAdminError:
            return self._json_error('Administrator already exists', 409)
        except ValueError as exc:
            return self._json_error(str(exc), 400)
        return web.json_response({'created': True, 'admin_id': admin_id}, status=201)

    async def _local_login(self, request):
        if not self.panel_store:
            return self._json_error('Local authentication is unavailable', 503)
        data = await self._body(request)
        admin = self.panel_store.verify_admin(str(data.get('username', '')), str(data.get('password', '')))
        if not admin:
            return self._json_error('Invalid username or password', 401)
        token, csrf = self.panel_store.create_session(int(admin['id']))
        response = web.json_response({'username': admin['username'], 'role': admin['role'], 'csrf': csrf, 'must_change_password': bool(admin.get('must_change_password'))})
        response.set_cookie('zero_admin_session', token, httponly=True, secure=(request.secure or request.headers.get('X-Forwarded-Proto') == 'https'), samesite='strict', max_age=86400, path='/')
        return response

    async def _local_me(self, request):
        session = self._local_require(request)
        return web.json_response({'username': session['username'], 'role': session['role'], 'csrf': session['csrf_token'], 'must_change_password': bool(session.get('must_change_password'))})

    async def _local_change_password(self, request):
        session = self._local_require(request, csrf=True)
        if not self.panel_store:
            return self._json_error('Local authentication is unavailable', 503)
        try:
            data = await self._body(request)
            self.panel_store.change_admin_password(int(session['admin_id']), str(data.get('current_password', '')), str(data.get('new_password', '')))
        except ValueError as exc:
            return self._json_error(str(exc), 400)
        return web.json_response({'changed': True})

    async def _local_logout(self, request):
        self._local_require(request, csrf=True)
        raw = request.cookies.get('zero_admin_session')
        if raw and self.panel_store:
            self.panel_store.revoke_session(raw)
        response = web.json_response({'ok': True})
        response.del_cookie('zero_admin_session', path='/')
        return response

    async def _local_setup(self, request):
        self._local_require(request)
        if not self.panel_store:
            return self._json_error('Setup is unavailable', 503)
        return web.json_response(self.panel_store.get_setup_state())

    async def _local_setup_step(self, request):
        self._local_require(request, csrf=True)
        if not self.panel_store:
            return self._json_error('Setup is unavailable', 503)
        try:
            self.panel_store.save_setup_step(request.match_info['step'], await self._body(request))
        except ValueError as exc:
            return self._json_error(str(exc), 400)
        return web.json_response(self.panel_store.get_setup_state())

    async def _local_setup_skip(self, request):
        self._local_require(request, csrf=True)
        if not self.panel_store:
            return self._json_error('Setup is unavailable', 503)
        self.panel_store.skip_setup()
        return web.json_response(self.panel_store.get_setup_state())

    async def _local_dashboard(self, request):
        self._local_require(request)
        return web.json_response(await self._dashboard_payload())

    def _hash(self, value): return hmac.new(int(self.config.owner_user_id).to_bytes(8,'big',signed=False),value.encode(),hashlib.sha256).hexdigest()
    def _session(self, request):
        raw=request.cookies.get('zero_session'); item=self.sessions.get(self._hash(raw or '')) if raw else None
        return item if item and item['expires']>time.time() else None
    def _require(self, request, *, csrf=False, write=False):
        session=self._session(request)
        if not session:
            local = self._local_session(request)
            if local:
                session = dict(local) | {'user_id': int(self.config.owner_user_id), 'csrf': local['csrf_token'], 'role': 'owner'}
        if not session: raise web.HTTPUnauthorized(text=json.dumps({'error':'Authentication required.'}),content_type='application/json')
        if write and session.get('role') != 'owner': raise web.HTTPForbidden(text=json.dumps({'error':'This account has view-only access.'}),content_type='application/json')
        if csrf and not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),session['csrf']): raise web.HTTPForbidden(text=json.dumps({'error':'Invalid CSRF token.'}),content_type='application/json')
        return session
    @staticmethod
    def _redact(value):
        if value is None:return value
        text=str(value); return _SECRET.sub(lambda m:(m.group(1)+m.group(2)+'[REDACTED]') if m.group(1) else '[REDACTED]',text)[:8000]
    @staticmethod
    def _page(request):
        try: page=max(1,int(request.query.get('page','1'))); size=min(_PAGE_MAX,max(1,int(request.query.get('size','25'))))
        except ValueError: page,size=1,25
        return page,size,(page-1)*size
    def _audit(self,event,session,**details): self.login_audit.appendleft({'event':event,'user_id':session.get('user_id') if session else None,'timestamp':int(time.time()),'details':details})
    def _client_ip(self,request):
        peer=request.remote or ''
        candidates=[]
        if peer in {'127.0.0.1','::1'}:
            candidates.extend(request.headers.get('X-Forwarded-For','').split(','))
            candidates.append(request.headers.get('X-Real-IP',''))
        candidates.append(peer)
        for value in candidates:
            try:return str(ipaddress.ip_address(value.strip()))
            except ValueError:continue
        return 'unknown'
    async def _notify_login(self,request,session):
        if session.get('role') != 'viewer': return
        username='@'+str(session.get('username','')).lstrip('@')
        text=(f'🛡️ Zero Panel Login\nAdmin: {username}\n'
              f'IP: {self._client_ip(request)}\n'
              f'Time: {datetime.now(timezone.utc).isoformat(timespec="seconds")} UTC')
        try:
            await self.bot.send_message(int(self.config.owner_user_id),text)
        except Exception as exc:
            self._audit('LOGIN_NOTIFY_FAILED',session,error=type(exc).__name__)
    async def _body(self,request):
        try:return await request.json()
        except Exception: raise web.HTTPBadRequest(text=json.dumps({'error':'Invalid request.'}),content_type='application/json')

    async def _health(self,request):return web.json_response({'status':'ready','service':'zero-panel'})
    async def _index(self,request):return web.FileResponse(self.static_dir/'index.html')
    async def _static(self,request):
        name=request.match_info['name'] or 'index.html'; path=(self.static_dir/name).resolve()
        if self.static_dir.resolve() not in path.parents or not path.is_file():raise web.HTTPNotFound()
        return web.FileResponse(path)
    async def _identity(self,value):
        value=str(value or '').strip().lstrip('@')
        if value.isdigit() and 5<=len(value)<=15:
            user_id=int(value)
            return user_id if user_id==int(self.config.owner_user_id) or user_id in {int(x) for x in self.config.panel_viewer_user_ids} else None
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}',value): return None
        if value.lower()==str(self.config.owner_username).lstrip('@').lower():return int(self.config.owner_user_id)
        if value.lower() not in {str(x).lstrip('@').lower() for x in self.config.panel_viewer_usernames}: return None
        viewers=dict(zip((str(x).lstrip('@').lower() for x in self.config.panel_viewer_usernames),self.config.panel_viewer_user_ids))
        return int(viewers[value.lower()]) if value.lower() in viewers else None

    def _role_for(self,user_id):
        return 'owner' if int(user_id)==int(self.config.owner_user_id) else 'viewer'
    async def _request_code(self,request):
        data=await self._body(request); user_id=await self._identity(data.get('identity')); now=time.time()
        if user_id is None: self._audit('LOGIN_DENIED',None,reason='not_allowed'); return self._json_error('This ID is not allowed to log in.',403)
        old=self.pending.get(str(user_id))
        if old and now-old['sent']<30:return self._json_error('Please wait a few seconds.',429)
        code=f'{secrets.randbelow(1_000_000):06d}'; self.pending[str(user_id)]={'hash':self._hash(code),'sent':now,'expires':now+120,'attempts':0}
        await self.bot.send_message(user_id,f'Zero Panel secure login code: {code}\nValid for 2 minutes'); self._audit('OTP_SENT',{'user_id':user_id})
        return web.json_response({'ok':True})
    async def _verify(self,request):
        data=await self._body(request); user_id=await self._identity(data.get('identity')); item=self.pending.get(str(user_id)) if user_id else None
        if not item or item['expires']<time.time() or item['attempts']>=5:return self._json_error('Code expired or maximum attempts reached.',401)
        item['attempts']+=1
        if not hmac.compare_digest(item['hash'],self._hash(str(data.get('code','')))):return self._json_error('The entered code is incorrect.',401)
        self.pending.pop(str(user_id),None); raw=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24); sid=secrets.token_hex(8)
        identity=str(data.get('identity','')).lstrip('@'); username=self.config.owner_username if int(user_id)==int(self.config.owner_user_id) else identity
        self.sessions[self._hash(raw)]={'id':sid,'user_id':user_id,'username':username,'role':self._role_for(user_id),'csrf':csrf,'created_at':int(time.time()),'last_seen':int(time.time()),'expires':time.time()+86400}
        self._audit('LOGIN_SUCCESS',{'user_id':user_id},session_id=sid)
        await self._notify_login(request,self.sessions[self._hash(raw)])
        response=web.json_response({'ok':True,'csrf':csrf,'role':self._role_for(user_id)}); response.set_cookie('zero_session',raw,httponly=True,secure=True,samesite='strict',max_age=86400,path='/'); return response
    async def _me(self,request):
        s=self._require(request); s['last_seen']=int(time.time()); return web.json_response({'username':s['username'],'csrf':s['csrf'],'role':s['role']})
    async def _logout(self,request):
        s=self._require(request,csrf=True); raw=request.cookies.get('zero_session'); self.sessions.pop(self._hash(raw or ''),None); self._audit('LOGOUT',s)
        response=web.json_response({'ok':True}); response.del_cookie('zero_session',path='/'); return response
    async def _logout_all(self,request):
        s=self._require(request,csrf=True,write=True); self.sessions={k:v for k,v in self.sessions.items() if v['user_id']!=s['user_id']}; self._audit('LOGOUT_ALL',s); return web.json_response({'ok':True})

    async def _dashboard_payload(self):
        status=listener_status(); provider=self.router.status(); ram='—'; cpu='—'
        try:
            info={x.split(':')[0]:x.split(':')[1] for x in Path('/proc/meminfo').read_text().splitlines()}; ram=f'{(1-int(info["MemAvailable"].split()[0])/int(info["MemTotal"].split()[0]))*100:.0f}%'
        except Exception:pass
        try:
            cpu=f'{os.getloadavg()[0]:.2f}'  # POSIX only; graceful on Windows.
        except (AttributeError,OSError):pass
        try:
            import shutil as _shutil
            total,_,free=_shutil.disk_usage(self.config.memory.db_path); disk_free=f'{free/total*100:.0f}% free' if total else '—'
        except OSError:disk_free='—'
        active=self.config.router.normal_primary
        return {'status':{'listener':status['running'],'cpu':cpu,'ram':ram,'disk':disk_free},'provider':{'active':active,'model':provider.get('providers',{}).get(active,{}).get('model','')},'activity':list(self.login_audit)[:5]}
    async def _dashboard(self,request):self._require(request); return web.json_response(await self._dashboard_payload())
    async def _realtime(self,request):
        self._require(request); response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-cache','X-Accel-Buffering':'no'}); await response.prepare(request)
        for _ in range(12):
            try:
                payload=await self._dashboard_payload(); payload['recent_errors']=self._read_logs(limit=3,level='ERROR')['items']; await response.write(f"data: {json.dumps(payload,ensure_ascii=False)}\n\n".encode()); await asyncio.sleep(5)
            except (ConnectionResetError,asyncio.CancelledError):break
        return response

    async def _chats(self,request):
        self._require(request); page,size,_=self._page(request); q=request.query.get('q','')[:100]
        try:
            chat=int(request.query['chat_id']) if request.query.get('chat_id') else None
            user=int(request.query['user_id']) if request.query.get('user_id') else None
        except ValueError:return self._json_error('Invalid ID.')
        result=await self.store.panel_list_chats(query=q,chat_id=chat,sender_id=user,page=page,size=size)
        for r in result['items']:
            r.update({'type':'text','trace_id':None,'pinned':False,'read_only':True});r['text']=self._redact(r['text'])
        result['read_only']=True;return web.json_response(result)
    async def _chat_detail(self,request):
        self._require(request);r=await self.store.panel_get_chat(int(request.match_info['item_id']))
        if not r:raise web.HTTPNotFound()
        out=r|{'type':'text','trace_id':None,'pinned':False,'read_only':True};out['text']=self._redact(out['text']);return web.json_response(out)

    def _safe_row(self,row):
        hidden={'file_reference','access_hash','answer','answer_hash','state_json'};out={}
        for k,v in row.items():
            if k in hidden:continue
            out[k]=self._redact(v) if isinstance(v,str) else v
        return out
    async def _memory_list(self,request):
        self._require(request);layer=request.match_info['layer']
        if layer not in _MEMORY_TABLES:raise web.HTTPNotFound()
        page,size,_=self._page(request);result=await self.store.panel_list_dataset(layer,query=request.query.get('q',''),status=request.query.get('status',''),page=page,size=size)
        result['items']=[self._safe_row(r) for r in result['items']];result['layer']=layer;return web.json_response(result)
    async def _memory_detail(self,request):
        self._require(request);layer=request.match_info['layer'];item=request.match_info['item_id']
        if layer not in _MEMORY_TABLES:raise web.HTTPNotFound()
        out=await self.store.panel_get_dataset_item(layer,item)
        if not out:raise web.HTTPNotFound()
        return web.json_response(self._safe_row(out))
    async def _memory_action(self,request):
        s=self._require(request,csrf=True,write=True);layer=request.match_info['layer'];item=request.match_info['item_id'];action=request.match_info['action'];body=await self._body(request);owner=int(s['user_id'])
        svc=self.services
        try:
            if layer=='semantic' and action=='correct':result=svc['semantic'].correct(int(item),body.get('value'),owner)
            elif layer=='semantic' and action=='forget':
                row=svc['semantic'].inspect_for_actor(int(item),chat_id=0,sender_id=owner,actor_id=owner,owner_id=owner);result=svc['semantic'].forget(row['chat_id'],row['sender_id'],row['key'])
            elif layer=='semantic-candidates' and action=='approve':result=svc['semantic'].approve(int(item),owner)
            elif layer=='experience' and action=='verify':result=svc['experience'].verify(int(item),owner)
            elif layer=='experience' and action=='invalidate':result=svc['experience'].invalidate(int(item),owner,body.get('reason','panel'))
            elif layer=='experience-candidates' and action=='approve':result=svc['experience'].approve(int(item),owner)
            elif layer=='procedural-candidates' and action=='approve':result=svc['procedure'].approve(int(item),owner)
            elif layer=='procedural-candidates' and action=='reject':result=svc['procedure'].reject(int(item),owner)
            elif layer=='procedural' and action=='deprecate':result=svc['procedure'].deprecate(int(item),owner)
            elif layer=='long' and action=='correct':result=await self.store.correct_long_memory(item,str(body.get('value','')),actor_user_id=owner,trace_id=secrets.token_hex(8),reason='web_panel')
            else:return self._json_error('Operation not supported by current backend.',405)
        except (ValueError,PermissionError) as exc:return self._json_error(str(exc),400)
        self._audit('MEMORY_ACTION',s,layer=layer,item=item,action=action);return web.json_response({'ok':True,'result':result})

    async def _knowledge(self,request):
        self._require(request);worker=self.services.get('knowledge');status=await worker.status() if worker else {};schedule=await worker.schedule_status() if worker else {};page,size,_=self._page(request)
        items=await self.store.panel_list_dataset('knowledge-items',query=request.query.get('q',''),status=request.query.get('status',''),page=page,size=size);runs=await self.store.panel_list_dataset('knowledge-runs',page=page,size=size);telegram=await self.store.panel_list_dataset('telegram-knowledge',page=page,size=size)
        for result in (items,runs,telegram):result['items']=[self._safe_row(r) for r in result['items']]
        return web.json_response({'status':status,'schedule':schedule,'items':items,'runs':runs,'web_queue':await self.store.web_knowledge_queue_status(),'telegram_queue':telegram})
    async def _knowledge_item(self,request):
        self._require(request);out=await self.store.panel_get_knowledge_item(int(request.match_info['item_id']))
        if not out:raise web.HTTPNotFound()
        return web.json_response({'item':self._safe_row(out['item']),'sources':[self._safe_row(r) for r in out['sources']]})
    async def _knowledge_run(self,request):
        s=self._require(request,csrf=True,write=True);body=await self._body(request)
        if body.get('confirm') is not True:return self._json_error('Operation confirmation required.',409)
        result=await self.services['knowledge'].run_nightly(dry_run=bool(body.get('dry_run',True)),topic_limit=1);self._audit('KNOWLEDGE_RUN',s,dry_run=bool(body.get('dry_run',True)));return web.json_response(result)

    async def _router(self,request):
        self._require(request);snap=self.router.status();providers=[]
        for name,data in snap.get('providers',{}).items():
            keys=data.get('keys',[]);providers.append({'name':name,'model':data.get('model'),'quota_scope':getattr(getattr(self.config.router.providers,name,None),'quota_scope','unknown'),'key_pool':[{'id':f'{name.title()} Key #{i+1}','healthy':bool(k.get('healthy')),'cooldown':bool(k.get('cooldown'))} for i,k in enumerate(keys)],'healthy_keys':sum(bool(k.get('healthy') and k.get('enabled',True)) for k in keys),'cooldown_keys':sum(bool(k.get('cooldown')) for k in keys)})
        return web.json_response({'active':self.config.router.normal_primary,'fallback':[self.config.router.normal_fallback],'strategy':self.config.router.strategy,'search_provider':self.config.router.search_provider,'google_grounding':self.config.web.google_grounding_enabled,'providers':providers})

    def _read_logs(self,*,limit=50,level='',component='',trace=''):
        paths=[Path(self.config.logs.listener_log),Path(self.config.logs.panel_log),Path(self.config.logs.router_log),zero_home_path('logs', 'requests.log')];items=[]
        for path in paths:
            if not path.exists():continue
            for line in path.read_text(errors='replace').splitlines()[-2000:]:
                if level and level.upper() not in line.upper():continue
                if component and component.lower() not in (path.stem+' '+line).lower():continue
                if trace and trace not in line:continue
                items.append({'component':path.stem,'line':self._redact(line),'timestamp':line[:23]})
        return {'items':items[-limit:][::-1],'total_scanned':len(items)}
    async def _logs(self,request):
        self._require(request);limit=min(200,max(1,int(request.query.get('size','50'))));return web.json_response(self._read_logs(limit=limit,level=request.query.get('level',''),component=request.query.get('component',''),trace=request.query.get('trace_id','')))
    async def _logs_download(self,request):
        self._require(request);data=self._read_logs(limit=1000,level=request.query.get('level',''),component=request.query.get('component',''),trace=request.query.get('trace_id',''));text='\n'.join(f"[{x['component']}] {x['line']}" for x in data['items']);return web.Response(text=text,headers={'Content-Disposition':'attachment; filename="zero-redacted.log"'},content_type='text/plain')
    async def _log_stream(self,request):
        self._require(request);response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-cache','X-Accel-Buffering':'no'});await response.prepare(request);seen=''
        for _ in range(30):
            try:
                items=self._read_logs(limit=5,level='ERROR')['items'];payload=json.dumps(items,ensure_ascii=False)
                if payload!=seen:await response.write(f'data: {payload}\n\n'.encode());seen=payload
                await asyncio.sleep(2)
            except (ConnectionResetError,asyncio.CancelledError):break
        return response

    async def _jobs(self,request):
        self._require(request);jobs=await self.services['jobs'].list_jobs(actor=int(self.config.owner_user_id));page,size,_=self._page(request);runs=await self.store.panel_list_dataset('cron-runs',page=page,size=size);runs['items']=[self._safe_row(r) for r in runs['items']];return web.json_response({'items':jobs,'runs':runs})
    async def _job_detail(self,request):
        self._require(request);jid=request.match_info['job_id'];return web.json_response({'job':await self.services['jobs'].status(jid,actor=int(self.config.owner_user_id)),'logs':await self.services['jobs'].logs(jid,limit=30,actor=int(self.config.owner_user_id))})
    async def _job_action(self,request):
        s=self._require(request,csrf=True,write=True);body=await self._body(request);action=request.match_info['action'];jid=request.match_info['job_id']
        if body.get('confirm') is not True:return self._json_error('Operation confirmation required.',409)
        if action in {'pause','resume'}:result=await self.services['jobs'].set_state(int(s['user_id']),jid,'paused' if action=='pause' else 'enabled')
        elif action=='approve':result=await self.services['jobs'].approve(int(s['user_id']),jid)
        else:return self._json_error('Operation not supported.',405)
        self._audit('JOB_ACTION',s,job_id=jid,action=action);return web.json_response({'ok':True,'result':result})

    async def _users(self,request):
        self._require(request);users=await self.store.top_users(limit=100);known=[self._safe_row(r) for r in await self.store.panel_list_group_users(100)]
        return web.json_response({'owner':{'user_id':self.config.owner_user_id,'username':self.config.owner_username,'role':'owner'},'users':users,'known':known,'login_history':list(self.login_audit)})
    async def _session_list(self,request):self._require(request);return web.json_response({'items':[{k:v for k,v in x.items() if k!='csrf'} for x in self.sessions.values()]})
    async def _session_revoke(self,request):
        s=self._require(request,csrf=True,write=True);sid=request.match_info['session_id'];before=len(self.sessions);self.sessions={k:v for k,v in self.sessions.items() if v['id']!=sid};self._audit('SESSION_REVOKED',s,session_id=sid);return web.json_response({'revoked':before-len(self.sessions)})
    def _secret_status(self, path):
        p = Path(path)
        try:
            details = p.stat()
        except OSError:
            return {'configured': False, 'permission_valid': False, 'last_rotated': None}
        return {
            'configured': details.st_size > 0,
            'permission_valid': path_is_private(p),
            'last_rotated': int(details.st_mtime),
        }
    async def _settings(self,request):
        self._require(request);overrides={k:self._redact(v) for k,v in (await self.store.panel_get_settings(_ALLOWED_SETTINGS)).items()}
        cfg={'telegram':{'account_username':self.config.listener.account_username,'allowed_groups':self.config.listener.allowed_group_usernames},'memory':{'recent_messages_limit':self.config.memory.recent_messages_limit,'long_term_limit':self.config.memory.long_term_limit},'router':{'primary':self.config.router.normal_primary,'fallback':self.config.router.normal_fallback,'strategy':self.config.router.strategy},'search':{'web_enabled':self.config.web.enabled},'limits':self.config.policy.model_dump(),'reaction':self.config.reactions.model_dump(),'sticker':self.config.stickers.model_dump()}
        secrets_state={'management_bot':self._secret_status(self.config.management_bot.token_file),'provider_secrets':self._secret_status(os.environ.get('ZERO_SECRET_FILE', str(zero_home_path('secrets', 'zero.secrets.yaml'))))}
        return web.json_response({'config':cfg,'overrides':overrides,'secrets':secrets_state,'editable':sorted(_ALLOWED_SETTINGS)})
    async def _setting_update(self,request):
        s=self._require(request,csrf=True,write=True);key=request.match_info['key'];body=await self._body(request)
        if key not in _ALLOWED_SETTINGS:return self._json_error('Setting is not editable.',403)
        value=body.get('value');
        if not isinstance(value,(bool,int,float,str)):return self._json_error('Invalid value.')
        await self.store.set_setting(key,value);self._audit('SETTING_UPDATED',s,key=key);return web.json_response({'ok':True,'key':key})

    async def start(self,host='127.0.0.1',port=8787):
        runner=web.AppRunner(self.app);await runner.setup();site=web.TCPSite(runner,host,port);await site.start();self.runner=runner;return runner
