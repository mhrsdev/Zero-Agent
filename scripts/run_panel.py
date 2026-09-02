from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from zero.brain import ZeroBrain
from zero.semantic_memory import SemanticUserMemory
from zero.experience_memory import ExperienceMemory
from zero.procedural_memory import ProceduralMemory
from zero.world_model import WorldModel
from zero.config import ZeroConfig
from zero.configuration import ConfigStore, SetupService
from zero.runtime_config import load_effective_config, runtime_config_path
from zero.logging_utils import setup_logger
from zero.paths import panel_state_path, zero_home
from zero.management import load_bot_token
from zero.router import IndependentRouter
from zero.runtime_control import listener_status, restart_listener, start_listener, stop_listener
from zero.storage import ZeroStore
from zero.web import HybridWeb
from zero.telegram_search import TelegramSearchClient
from zero.reactions import parse_reaction_command
from zero.social import SocialService, parse_social_command
from zero.social_awareness import SocialAwareness, parse_awareness_command
from zero.template_jobs import TemplateJobService, JobSecurityError, parse_natural_job
from zero.knowledge import KnowledgeWorker
from zero.panel_api import PanelAPI
from zero.panel_store import PanelStore

CONFIG_PATH = Path(runtime_config_path())


def owner_only(config: ZeroConfig, message: Message) -> bool:
    return bool(
        message.from_user
        and int(message.from_user.id) == int(config.owner_user_id)
        and getattr(message.chat, 'type', None) == 'private'
    )


async def debug_web_search_hits(web: HybridWeb, query: str):
    """Use the async search path from aiogram command handlers."""
    return (await web.run(query)).results


async def main() -> None:
    config = load_effective_config(CONFIG_PATH, ZeroConfig)
    logger = setup_logger('zero.panel', config.logs.panel_log)
    store = ZeroStore(config.memory.db_path, recent_messages_limit=config.memory.recent_messages_limit, long_term_limit=config.memory.long_term_limit)
    social = SocialService(store)
    awareness = SocialAwareness(store)
    router = IndependentRouter(config)
    web = HybridWeb(config, store)
    knowledge = KnowledgeWorker(store, web, router)
    brain = ZeroBrain(config, store, router, knowledge=knowledge)
    jobs = TemplateJobService(store, config, web=web, knowledge=knowledge, summary_builder=brain.build_daily_summary)
    semantic = SemanticUserMemory(store.db_path)
    experience = ExperienceMemory(store.db_path)
    procedure = ProceduralMemory(store.db_path)
    world = WorldModel(store.db_path)
    tgsearch = TelegramSearchClient(config)
    token = load_bot_token(config.management_bot.token_file)
    bot = Bot(token)
    dp = Dispatcher()
    panel_api = PanelAPI(
        config, store, router, bot, static_dir=ROOT / 'panel',
        services={'knowledge': knowledge, 'jobs': jobs, 'semantic': semantic, 'experience': experience, 'procedure': procedure, 'world': world},
        panel_store=PanelStore(
            panel_state_path(),
            setup_service=SetupService(
                ConfigStore(),
                installation_id=os.environ.get('ZERO_INSTALLATION_ID', 'local'),
            ),
        ),
    )
    await panel_api.start(host=os.environ.get('ZERO_PANEL_HOST', '127.0.0.1'), port=int(os.environ.get('ZERO_PANEL_PORT', '8787')))
    logger.info('ZERO_PANEL_API_STARTED host=%s port=%s', os.environ.get('ZERO_PANEL_HOST', '127.0.0.1'), os.environ.get('ZERO_PANEL_PORT', '8787'))

    async def deny(message: Message) -> None:
        await message.answer(config.management_bot.deny_message)

    async def ensure_owner(message: Message) -> bool:
        if not owner_only(config, message):
            await deny(message)
            return False
        return True

    async def primary_group_id() -> int:
        raw = await store.get_setting('primary_group_chat_id', '')
        try:
            if raw and int(raw) != 0:
                return int(raw)
        except (TypeError, ValueError):
            pass
        if len(config.listener.allowed_group_ids) == 1:
            return int(next(iter(config.listener.allowed_group_ids)))
        active = await store.get_active_group_chat_ids()
        return int(next(iter(active))) if len(active) == 1 else 0

    @dp.message(Command('start'))
    async def cmd_start(message: Message) -> None:
        if not await ensure_owner(message):
            return
        await message.answer('پنل مدیریت Zero فعاله.')

    @dp.message(Command('zero'))
    async def cmd_zero(message: Message) -> None:
        parts = (message.text or '').split()
        sub = parts[1].lower() if len(parts) > 1 else 'status'
        # Semantic self-service is scoped to the real Telegram event identity; all other management is Owner-only.
        semantic_self_service = sub == 'memory' and len(parts) >= 4 and parts[2].lower() == 'semantic' and parts[3].lower() in {'me', 'inspect', 'correct', 'forget'}
        if not semantic_self_service and not await ensure_owner(message):
            return
        if sub == 'status':
            status = listener_status()
            mode = await store.get_setting('mode', config.persona.default_mode)
            today = await store.get_today_stats(datetime.now().strftime('%Y-%m-%d'))
            await message.answer(f"status: {'running' if status['running'] else 'stopped'}\npid: {status['pid']}\nmode: {mode}\nmessages: {today.get('message_count',0)}\nreplies: {today.get('reply_count',0)}")
        elif sub == 'router':
            action = parts[2].lower() if len(parts) >= 3 else 'status'
            snapshot = router.status()
            if action in {'status', 'providers', 'models', 'keys', 'cooldowns'}:
                lines = [f"strategy={config.router.strategy}", f"normal={config.router.normal_primary}->fallback:{config.router.normal_fallback}", "search=archived"]
                for provider, data in snapshot['providers'].items():
                    healthy = sum(1 for item in data['keys'] if item['healthy'] and item['enabled'])
                    cooldown = sum(1 for item in data['keys'] if item['cooldown'])
                    lines.append(f"{provider}: model={data['model']} keys={len(data['keys'])} healthy={healthy} cooldown={cooldown}")
                await message.answer('\\n'.join(lines))
            elif action == 'test':
                result = await router.complete('router health check')
                await message.answer(f"provider={result.provider} model={result.model} success={bool(result.text)}")
            else:
                await message.answer('Usage: /zero router [status|providers|models|keys|cooldowns|test]')
        elif sub == 'mode':
            if len(parts) >= 3:
                mode = parts[2].lower()
                if mode not in config.persona.available_modes:
                    await message.answer('mode نامعتبره.')
                    return
                await store.set_setting('mode', mode)
                await message.answer(f'mode شد: {mode}')
            else:
                await message.answer(str(await store.get_setting('mode', config.persona.default_mode)))
        elif sub == 'users':
            rows = await store.top_users(15)
            text = '\n'.join(f"{i+1}. {r['label']} — {r['reputation']}" for i, r in enumerate(rows)) or 'هنوز کاربری ثبت نشده.'
            await message.answer(text)
        elif sub == 'memory':
            chat_id = await primary_group_id()
            event_chat_id = int(message.chat.id)
            action = parts[2].lower() if len(parts) >= 3 else 'status'
            if action == 'semantic':
                subaction=parts[3].lower() if len(parts)>=4 else 'status'
                actor_id=int(message.from_user.id)
                self_scope_chat_id=event_chat_id
                owner_id=int(config.owner_user_id)
                if subaction == 'status':
                    with semantic._conn() as c: out={'active':c.execute("select count(*) from semantic_user_memory where status='active'").fetchone()[0],'candidates':c.execute("select count(*) from semantic_user_memory_candidates where status='pending'").fetchone()[0]}
                    await message.answer(json.dumps(out,ensure_ascii=False))
                elif subaction == 'me': await message.answer(json.dumps(semantic.retrieve(self_scope_chat_id,actor_id,3),ensure_ascii=False,default=str)[:3500])
                elif subaction == 'candidates':
                    with semantic._conn() as c: rows=[dict(x) for x in c.execute("select id,chat_id,sender_id,category,key,confidence,status from semantic_user_memory_candidates order by id desc limit 30")]
                    await message.answer(json.dumps(rows,ensure_ascii=False)[:3500] or 'candidate خالیه.')
                elif subaction == 'inspect' and len(parts)>=5 and parts[4].isdigit():
                    try: out=semantic.inspect_for_actor(int(parts[4]),chat_id=self_scope_chat_id,sender_id=actor_id,actor_id=actor_id,owner_id=owner_id)
                    except (PermissionError,ValueError) as exc: out={'ok':False,'reason':str(exc)}
                    await message.answer(json.dumps(out,ensure_ascii=False,default=str))
                elif subaction == 'correct' and len(parts)>=6 and parts[4].isdigit():
                    try: row=semantic.correct_for_actor(int(parts[4]),' '.join(parts[5:]),chat_id=self_scope_chat_id,sender_id=actor_id,actor_id=actor_id,owner_id=owner_id); out={'corrected':row}
                    except (PermissionError,ValueError) as exc: out={'corrected':False,'reason':str(exc)}
                    await message.answer(json.dumps(out,ensure_ascii=False))
                elif subaction == 'forget' and len(parts)>=5 and parts[4].isdigit():
                    try: count=semantic.forget_for_actor(int(parts[4]),chat_id=self_scope_chat_id,sender_id=actor_id,actor_id=actor_id,owner_id=owner_id); out={'forgotten':count}
                    except (PermissionError,ValueError) as exc: out={'forgotten':False,'reason':str(exc)}
                    await message.answer(json.dumps(out,ensure_ascii=False))
                else: await message.answer('Usage: /zero memory semantic [status|me|candidates|inspect <id>|correct <id> <value>|forget <id>]')
            elif action == 'status':
                status = await store.memory_status(chat_id)
                daily = await store.get_daily_summary(chat_id)
                await message.answer('Memory layers: ' + json.dumps({**status, 'daily_summary': daily}, ensure_ascii=False))
            elif action == 'stats':
                await message.answer(json.dumps(await store.get_today_stats(datetime.now().strftime('%Y-%m-%d')), ensure_ascii=False))
            elif action == 'daily-summary':
                await message.answer(json.dumps(await store.update_daily_summary(chat_id), ensure_ascii=False, indent=2)[:3500])
            elif action == 'weekly-summary':
                await message.answer(json.dumps(await store.build_period_summary(chat_id, days=7, label='weekly'), ensure_ascii=False, indent=2)[:3500])
            elif action == 'monthly-summary':
                await message.answer(json.dumps(await store.build_monthly_long_summary(chat_id), ensure_ascii=False, indent=2)[:3500])
            elif action == 'budget':
                await message.answer('short_tokens=1200 medium_tokens=800 long_tokens=600; proportional trim enabled')
            elif action == 'debug-context':
                ctx = await store.get_short_term_context(chat_id); media = await store.get_recent_media_context(chat_id, '', 5)
                await message.answer(json.dumps({'short':ctx,'media_count':len(media)}, ensure_ascii=False)[:3500])
            elif action == 'short':
                await message.answer(json.dumps(await store.get_short_term_context(chat_id), ensure_ascii=False, indent=2)[:3500] or '{}')
            elif action == 'medium':
                rows = await store.retrieve_layered_memory(chat_id, '', short_limit=0, medium_limit=5, long_limit=0)
                await message.answer(json.dumps(rows['medium'], ensure_ascii=False, indent=2)[:3500] or 'medium خالیه.')
            elif action == 'long':
                rows = await store.retrieve_layered_memory(chat_id, '', short_limit=0, medium_limit=0, long_limit=5)
                await message.answer(json.dumps(rows['long'], ensure_ascii=False, indent=2)[:3500] or 'long خالیه.')
            elif action == 'rebuild-short':
                row = await store.rebuild_short_from_recent(chat_id, 100)
                await message.answer('short از recent بازسازی شد: ' + json.dumps(row, ensure_ascii=False)[:3000])
            elif action == 'backfill' and len(parts) >= 4 and parts[3].isdigit():
                count = min(1000, int(parts[3]))
                result = await store.backfill_memory(chat_id, count)
                await message.answer('backfill انجام شد: ' + json.dumps(result, ensure_ascii=False))
            elif action == 'inspect':
                rows = await store.retrieve_layered_memory(chat_id, '', short_limit=1, medium_limit=5, long_limit=10)
                await message.answer(json.dumps(rows, ensure_ascii=False, indent=2)[:3500])
            elif action == 'history':
                rows = await store.list_memory_revisions(chat_id, limit=30)
                await message.answer(json.dumps(rows, ensure_ascii=False, indent=2)[:3500] or 'revision خالیه.')
            elif action == 'export':
                rows = await store.retrieve_layered_memory(chat_id, '', short_limit=10, medium_limit=100, long_limit=100)
                await message.answer(json.dumps(rows, ensure_ascii=False, indent=2)[:3500])
            elif action == 'compact':
                expired = await store.expire_medium_memory(chat_id)
                await message.answer(f'compact انجام شد؛ {expired} medium memory منقضی‌شده archive شد. حذف legacy انجام نشد.')
            elif action == 'restore' and len(parts) >= 4:
                restored = await store.restore_memory_revision(chat_id, parts[3], actor_user_id=int(message.from_user.id), trace_id=secrets.token_hex(6))
                await message.answer('revision restore شد.' if restored else 'revision پیدا نشد یا متعلق به این chat نیست.')
            elif action == 'clear' and len(parts) >= 4 and parts[3].lower() in {'short', 'medium', 'long'}:
                scope = parts[3].lower()
                token_key = f'memory_clear_confirmation:{chat_id}:{scope}'
                supplied = parts[4] if len(parts) >= 5 else ''
                pending = await store.get_setting(token_key, '')
                if scope == 'long' and (not supplied or supplied != pending):
                    token = secrets.token_urlsafe(9)
                    await store.set_setting(token_key, token)
                    await store.memory_audit_event('MEMORY_COMMAND_REJECTED', scope, chat_id, actor_user_id=int(message.from_user.id), details={'reason':'second_confirmation_required'})
                    await message.answer(f'پاک‌کردن Long-term ممنوع است مگر با تأیید دومرحله‌ای. برای تأیید همین توکن را بفرست:\n`/zero memory clear long {token}`')
                    return
                count = await store.soft_clear_memory(chat_id, scope, actor_user_id=int(message.from_user.id), trace_id=secrets.token_hex(6), reason='owner_command')
                await store.set_setting(token_key, '')
                await message.answer(f'{scope} memory soft-cleared: {count}. snapshot/revision ساخته شد.')
            else:
                await store.memory_audit_event('MEMORY_COMMAND_REJECTED', 'unknown', chat_id, actor_user_id=int(message.from_user.id), details={'reason':'scope_required'})
                await message.answer('Usage: /zero memory [status|short|medium|long|inspect|history|export|compact|rebuild-short|backfill <count>|restore <revision_id>|clear short|clear medium|clear long <confirmation_token>]')
        elif sub == 'experience':
            action=parts[2].lower() if len(parts)>2 else 'status'
            with experience._c() as c:
                if action=='status': out={'active':c.execute("select count(*) from experience_memory where status='active'").fetchone()[0],'candidates':c.execute("select count(*) from experience_memory_candidates where status='pending'").fetchone()[0]}
                elif action=='list': out=[dict(x) for x in c.execute("select id,topic,root_cause,confidence,status from experience_memory where status='active' order by id desc limit 20")]
                elif action=='inspect' and len(parts)>3 and parts[3].isdigit(): out=dict(c.execute('select * from experience_memory where id=?',(int(parts[3]),)).fetchone() or {})
                elif action=='search' and len(parts)>3:
                    out=experience.retrieve(' '.join(parts[3:]),debug=True,limit=3)
                elif action=='verify' and len(parts)>3 and parts[3].isdigit(): out=experience.verify(int(parts[3]),int(message.from_user.id),trace_id=secrets.token_hex(8))
                elif action=='invalidate' and len(parts)>3 and parts[3].isdigit(): out=experience.invalidate(int(parts[3]),int(message.from_user.id),' '.join(parts[4:]),trace_id=secrets.token_hex(8))
                else: out={'usage':'/zero experience [status|list|inspect <id>|search <query>|verify <id>|invalidate <id>]'}
            await message.answer(json.dumps(out,ensure_ascii=False,default=str)[:3500])
        elif sub == 'procedure':
            action=parts[2].lower() if len(parts)>2 else 'status'
            with procedure._c() as c:
                if action=='status': out={'active':c.execute("select count(*) from procedural_memory where status='active'").fetchone()[0],'pending':c.execute("select count(*) from procedural_memory_candidates where status='pending'").fetchone()[0]}
                elif action=='list': out=[dict(x) for x in c.execute("select id,name,risk_level,version,success_count,failure_count from procedural_memory where status='active' order by id desc")]
                elif action=='inspect' and len(parts)>3 and parts[3].isdigit(): out=procedure.inspect(int(parts[3])) or {}
                elif action=='approve' and len(parts)>3 and parts[3].isdigit(): out={'approved_id':procedure.approve(int(parts[3]),int(message.from_user.id))}
                elif action=='reject' and len(parts)>3 and parts[3].isdigit(): procedure.reject(int(parts[3]),int(message.from_user.id)); out={'rejected_id':int(parts[3])}
                elif action=='deprecate' and len(parts)>3 and parts[3].isdigit(): procedure.deprecate(int(parts[3]),int(message.from_user.id)); out={'deprecated_id':int(parts[3])}
                elif action=='search' and len(parts)>3: out=procedure.retrieve(' '.join(parts[3:])) or {}
                else: out={'usage':'/zero procedure [status|list|inspect <id>|approve <id>|reject <id>|deprecate <id>|search <query>]'}
            await message.answer(json.dumps(out,ensure_ascii=False,default=str)[:3500])
        elif sub == 'world':
            action=parts[2].lower() if len(parts)>2 else 'status'
            with world._c() as c:
                if action=='status': out={'entities':c.execute('select count(*) from world_entities').fetchone()[0],'relations':c.execute('select count(*) from world_relations where status="active"').fetchone()[0]}
                elif action=='entities': out=[dict(x) for x in c.execute('select id,canonical_name,entity_type,status from world_entities order by id')]
                elif action=='entity' and len(parts)>3:
                    row=c.execute('select * from world_entities where id=? or canonical_name=?',(int(parts[3]) if parts[3].isdigit() else -1,' '.join(parts[3:]))).fetchone(); out=dict(row) if row else {}
                elif action=='relations' and len(parts)>3: out=world.resolve_query(' '.join(parts[3:])) or {}
                elif action=='search' and len(parts)>3: out=world.resolve_query(' '.join(parts[3:])) or {}
                else: out={'usage':'/zero world [status|entities|entity <id|name>|relations <id|name>|search <query>]'}
            await message.answer(json.dumps(out,ensure_ascii=False,default=str)[:3500])
        elif sub == 'stats':
            chat_id = await primary_group_id()
            period = parts[2].lower() if len(parts) >= 3 else 'today'
            if period in {'today', 'week'}:
                stats = await store.get_social_plus_stats(chat_id, days=7 if period == 'week' else 1)
                await message.answer(json.dumps(stats, ensure_ascii=False, indent=2)[:3500] if stats.get('message_count') else 'هنوز آماری ندارم.')
            elif period == 'users':
                stats = await store.get_social_plus_stats(chat_id, days=7)
                await message.answer('فعال‌ترین کاربران (شناسه خام نمایش داده نمی‌شود):\n' + '\n'.join(f'{i+1}. {count} پیام' for i, (_, count) in enumerate(stats.get('active_users', []))) or 'هنوز آماری ندارم.')
            else:
                await message.answer('Usage: /zero stats [today|week|users]')
        elif sub == 'quote':
            chat_id = await primary_group_id()
            action = parts[2].lower() if len(parts) >= 3 else 'random'
            quotes = await store.get_social_quotes(chat_id, today=action == 'today', limit=5)
            await message.answer('\n'.join(f"• {row['quote']}" for row in quotes) or 'هنوز Quote تأییدشده‌ای ندارم.')
        elif sub == 'summary':
            chat_id = await primary_group_id()
            if not chat_id:
                await message.answer('allowed group تعریف نشده.')
                return
            summary = await brain.build_daily_summary(chat_id)
            await message.answer(summary[:3500])
        elif sub == 'nickname':
            if len(parts) >= 5 and parts[2] == 'set':
                user_id = int(parts[3])
                nickname = ' '.join(parts[4:])
                target_chat_id = await primary_group_id()
                await store.upsert_profile(target_chat_id, user_id, f'user:{user_id}', nicknames=[nickname])
                await message.answer('لقب ثبت شد.')
            else:
                await message.answer('فرمت: /zero nickname set <user_id> <nickname>')
        elif sub == 'reputation':
            if len(parts) >= 5 and parts[2] == 'add':
                user_id = int(parts[3])
                delta = int(parts[4])
                target_chat_id = await primary_group_id()
                await store.upsert_profile(target_chat_id, user_id, f'user:{user_id}', reputation_delta=delta)
                await message.answer('reputation آپدیت شد.')
            else:
                await message.answer('فرمت: /zero reputation add <user_id> <delta>')
        elif sub == 'reset':
            if len(parts) >= 3 and parts[2] == 'stats':
                await store.set_setting('manual_reset_note', f'stats reset requested {datetime.now().isoformat()}')
                await message.answer('برای امنیت، reset سخت دیتابیس دستی انجام می‌شود؛ درخواست ثبت شد.')
            else:
                await message.answer('فعلاً فقط /zero reset stats')
        elif sub == 'mute':
            if len(parts) >= 4:
                user_id = int(parts[2])
                seconds = int(parts[3])
                raw = await store.get_setting('muted_users', '{}')
                data = json.loads(raw or '{}')
                data[str(user_id)] = int(datetime.now().timestamp()) + seconds
                await store.set_setting('muted_users', data)
                await message.answer('mute شد.')
            else:
                await message.answer('فرمت: /zero mute <user_id> <seconds>')
        elif sub == 'unmute':
            if len(parts) >= 3:
                user_id = int(parts[2])
                raw = await store.get_setting('muted_users', '{}')
                data = json.loads(raw or '{}')
                data.pop(str(user_id), None)
                await store.set_setting('muted_users', data)
                await message.answer('unmute شد.')
            else:
                await message.answer('فرمت: /zero unmute <user_id>')
        elif sub == 'cooldown':
            if len(parts) >= 3:
                await message.answer(f'cooldown فعلی در config: {config.policy.spam_cooldown_seconds}')
            else:
                await message.answer(f'cooldown: {config.policy.spam_cooldown_seconds}')
        elif sub == 'budget':
            await message.answer(f"soft budget: {config.router.daily_budget_soft_limit_usd}$")
        elif sub == 'limitgame':
            action = parts[2].lower() if len(parts) >= 3 else 'status'
            if action in {'on', 'off'}:
                await store.set_setting('limit_challenge_enabled', 'true' if action == 'on' else 'false')
                await message.answer(f'Limit Challenge {"روشن" if action == "on" else "خاموش"} شد.')
            elif action == 'status':
                enabled = await store.get_setting('limit_challenge_enabled', 'true')
                reset_daily = await store.get_setting('limit_challenge_reset_daily', 'true')
                templates = await store.list_limit_challenge_templates()
                await message.answer(f'limitgame={enabled}\nreset_daily={reset_daily}\ntemplates={len(templates)}\ntimeout=180s\nattempts=2')
            elif action == 'reset' and len(parts) >= 4 and parts[3].isdigit():
                await store.reset_limit_challenge(int(parts[3]))
                await message.answer('Progress و challenge فعال کاربر reset شد.')
            elif action == 'progress' and len(parts) >= 4 and parts[3].isdigit():
                rows = await store.list_limit_challenge_progress(int(parts[3]))
                if not rows:
                    await message.answer('برای این کاربر progress ثبت نشده.')
                else:
                    text = '\n'.join(
                        f"chat={row['chat_id']} stage={row['current_stage']} bonus={row['bonus_quota']} completed={row['completed_stages_json']} daily={row['daily_completed_count']}"
                        for row in rows
                    )
                    await message.answer(text[:3500])
            elif action == 'templates':
                rows = await store.list_limit_challenge_templates()
                text = '\n'.join(f"stage={row['stage']} id={row['template_id']} uses={row['usage_count']} created={row['created_at']}" for row in rows) or 'template خالیه.'
                await message.answer(text[:3500])
            elif action == 'clear-active' and len(parts) >= 4 and parts[3].isdigit():
                cleared = await store.clear_limit_challenge_active(int(parts[3]))
                await message.answer(f'{cleared} challenge فعال پاک شد.')
            else:
                await message.answer('Usage: /zero limitgame [status|on|off|reset <user_id>|progress <user_id>|templates|clear-active <user_id>]')
        elif sub == 'web':
            action = parts[2].lower() if len(parts) >= 3 else 'status'
            if action in {'on', 'off'}:
                await store.set_setting('web_enabled', 'true' if action == 'on' else 'false')
                web.invalidate_cache()
                await message.answer(f'وب‌سرچ {"روشن" if action == "on" else "خاموش"} شد.')
            elif action == 'status':
                enabled = await store.get_setting('web_enabled', str(config.web.enabled).lower())
                healthy, detail = await web.health_check()
                await message.answer(
                    f"web_enabled={enabled}\n"
                    f"primary=google-grounding\n"
                    f"local=google-cse > brave+startpage > duckduckgo\n"
                    f"health={'ok' if healthy else 'failed'} ({detail})"
                )
            else:
                await message.answer('Usage: /zero web [on|off|status]')
        elif sub == 'vision':
            if len(parts) >= 3:
                action = parts[2].lower()
                if action == 'on':
                    await store.set_setting('vision_enabled', 'true')
                    brain.vision.invalidate_cache()
                    await message.answer('Vision روشن شد.')
                elif action == 'off':
                    await store.set_setting('vision_enabled', 'false')
                    brain.vision.invalidate_cache()
                    await message.answer('Vision خاموش شد.')
                elif action == 'status':
                    enabled = await store.get_setting('vision_enabled', str(config.vision.enabled).lower())
                    await message.answer(
                        f"vision_enabled={enabled}\n"
                        f"model={config.vision.model}\n"
                        f"max_images/user={config.vision.max_images_per_user_per_window}/{config.vision.window_seconds}s\n"
                        f"max_gifs/user={config.vision.max_gifs_per_user_per_window}/{config.vision.window_seconds}s\n"
                        f"cooldown={config.vision.cooldown_seconds}s\n"
                        f"max_size={config.vision.max_file_size_mb}MB\n"
                        f"ext={','.join(config.vision.allowed_extensions)}"
                    )
                elif action == 'model' and len(parts) >= 4:
                    await message.answer('Model vision در config.yaml تغییر کنید و ریستارت کنید.')
                elif action == 'limit' and len(parts) >= 4:
                    await message.answer('Limits در config.yaml قابل تغییرند.')
                else:
                    await message.answer('Subcommands: on, off, status, model, limit')
            else:
                enabled = await store.get_setting('vision_enabled', str(config.vision.enabled).lower())
                await message.answer(
                    f"Vision: {'ON' if enabled == 'true' else 'OFF'}\n"
                    f"Usage: /zero vision [on|off|status|model|limit]"
                )
        elif sub == 'nova':
            if len(parts) >= 3:
                action = parts[2].lower()
                if action == 'status':
                    await message.answer(
                        f"Nova limits:\n"
                        f"max_msgs={config.policy.nova_max_messages_per_window}/{config.policy.nova_window_seconds}s\n"
                        f"bot_chain_max={config.policy.bot_max_chain_turns}/{config.policy.bot_reply_cooldown_seconds}s"
                    )
                elif action == 'reset':
                    # Could reset Nova counters for a specific user
                    await message.answer('Nova counters reset يتطلب user_id. در config.yaml تغییر دهید.')
                else:
                    await message.answer('Subcommands: status, reset')
            else:
                await message.answer(
                    f"Nova (bot-to-bot) limits:\n"
                    f"max_msgs={config.policy.nova_max_messages_per_window}/{config.policy.nova_window_seconds}s\n"
                    f"bot_chain_max={config.policy.bot_max_chain_turns}/{config.policy.bot_reply_cooldown_seconds}s\n"
                    f"Usage: /zero nova [status|reset]"
                )
        elif sub == 'gif':
            if len(parts) >= 3:
                action = parts[2].lower()
                if action in ('on', 'off'):
                    await store.set_setting('gif_enabled', action)
                    await message.answer(f'GIF processing {action}.')
                else:
                    await message.answer('Subcommands: on, off')
            else:
                await message.answer('Usage: /zero gif [on|off]')
        elif sub == 'tgsearch_archived':
            action = parts[2].lower() if len(parts) >= 3 else 'status'
            if action == 'on':
                await message.answer('Telegram Search فعلاً غیرفعال و بایگانی شده است؛ فعال‌سازی از toggle ساده مجاز نیست.')
            elif action == 'off':
                await message.answer('Telegram Search در وضعیت Archived باقی ماند.')
            elif action == 'cache' and len(parts) >= 4 and parts[3].lower() == 'clear':
                count = await store.clear_telegram_search_cache(); tgsearch.invalidate_cache(); await message.answer(f'Telegram Search cache invalidated={count}.')
            elif action == 'cache':
                status = await store.telegram_search_cache_status(); await message.answer(f"cache_active={status['active']}\ncache_expired_or_invalidated={status['expired']}")
            elif action == 'limits' and len(parts) >= 4 and parts[3].lower() == 'reset':
                count = await tgsearch.reset_limits(); await message.answer(f'Telegram Search limits reset={count}.')
            elif action == 'limits':
                rows = await tgsearch.limit_status(); await message.answer(json.dumps(rows, ensure_ascii=False)[:2000] or 'limits=0')
            elif action == 'providers':
                await message.answer('providers: joined_dialogs, telegram_global, channel_inspector, web_telegram_discovery, hybrid_router\nmedia-ready=true\nmedia-search-enabled=false')
            elif action == 'global':
                await message.answer('global_search=available_in_telethon_1.44; account-visibility-limited; no channel discovery')
            elif action == 'joined':
                await message.answer(f'joined_dialogs=bounded({config.telegram_search.max_joined_dialogs_per_run}); session_authorized=checked_on_search')
            elif action == 'inspector':
                await message.answer('public_inspector=enabled; auto_join=false; bounded_recent_messages=3')
            elif action == 'web-discovery':
                await message.answer(f'web_discovery=public HybridWeb interface; enabled={await web.is_tool_enabled()}')
            elif action == 'test':
                query = ' '.join(parts[3:]).strip() or 'Gemini'; hits = await tgsearch.search(query, trace_id='panel-test', chat_id=int(message.chat.id), sender_id=int(message.from_user.id)); await message.answer(f'test_results={len(hits)} source=' + ','.join(sorted({h.provider for h in hits}))[:500])
            elif action == 'status':
                enabled = await store.get_setting('tgsearch_enabled', str(config.telegram_search.enabled).lower()); cache = await store.telegram_search_cache_status(); limits = await tgsearch.limit_status()
                await message.answer(f'enabled={enabled}\nsession_configured={bool(config.telegram_search.session_path)}\nproviders=5\ncache_active={cache["active"]}\ncache_expired={cache["expired"]}\nlimits={json.dumps(limits, ensure_ascii=False)}\nmedia-ready=true\nmedia-search-enabled=false')
            else:
                await message.answer('Subcommands: on, off, status, providers, test, global, joined, inspector, web-discovery, limits, limits reset, cache status, cache clear')
        elif sub == 'knowledge':
            action = parts[2].lower() if len(parts) >= 3 else 'status'
            if action == 'queue':
                clear_web=(len(parts)>=5 and parts[3].lower()=='clear' and parts[4].lower()=='web') or (len(parts)>=5 and parts[3].lower()=='web' and parts[4].lower()=='clear')
                web_queue=(len(parts)>=4 and parts[3].lower() in ('web','stats')) or clear_web
                if web_queue and clear_web: count=await store.clear_web_knowledge_candidates(); await message.answer(f'web queue cleared: {count}')
                elif web_queue: await message.answer(json.dumps(await store.web_knowledge_queue_status(), ensure_ascii=False))
                else: await message.answer('Usage: /zero knowledge queue web | /zero knowledge queue clear web')
            elif action == 'status':
                status = await knowledge.status(); schedule = await knowledge.schedule_status()
                async with store._lock:
                    with store._conn() as conn: run = conn.execute('SELECT status,llm_calls_used,accepted_count,rejected_count FROM knowledge_runs ORDER BY started_at DESC LIMIT 1').fetchone()
                await message.answer(json.dumps({**schedule, 'last_status': run['status'] if run else None, 'llm_calls_used': run['llm_calls_used'] if run else 0, 'accepted_count': run['accepted_count'] if run else 0, 'rejected_count': run['rejected_count'] if run else 0, 'active_items': status['active_items']}, ensure_ascii=False, default=str)[:3500])
            elif action == 'topics':
                await knowledge.ensure_topics()
                async with store._lock:
                    with store._conn() as conn: rows = [dict(r) for r in conn.execute('SELECT * FROM knowledge_topics ORDER BY priority DESC, id').fetchall()]
                await message.answer('\n'.join(f"{r['id']} {r['topic']} enabled={r['enabled']} last={r['last_checked_at']} next={r['next_check_at']}" for r in rows)[:3500])
            elif action in {'enable', 'disable'} and len(parts) == 3:
                schedule = await knowledge.schedule_status()
                if not schedule['job_id']:
                    await message.answer('nightly knowledge job هنوز ساخته نشده.')
                else:
                    await jobs.set_state(int(message.from_user.id), schedule['job_id'], 'enabled' if action == 'enable' else 'disabled')
                    await message.answer(f"nightly knowledge job {action} شد.")
            elif action in {'enable', 'disable'} and len(parts) >= 4:
                async with store._lock:
                    with store._conn() as conn: conn.execute('UPDATE knowledge_topics SET enabled=? WHERE id=? OR topic=?', (1 if action == 'enable' else 0, parts[3], ' '.join(parts[3:]))) ; conn.commit()
                await message.answer(f'knowledge topic {action} شد.')
            elif action == 'run-now':
                # Dry-run is the safe default; explicit owner approval is required for production writes.
                real = len(parts) >= 4 and parts[3].lower() == 'approved'
                result = await knowledge.run_nightly(dry_run=not real, topic_limit=1)
                await message.answer(('Simulation\n' if not real else 'Production run\n') + json.dumps(result, ensure_ascii=False))
            elif action == 'items':
                topic = ' '.join(parts[3:]) if len(parts) >= 4 else ''
                async with store._lock:
                    with store._conn() as conn: rows = [dict(r) for r in conn.execute("SELECT id,title,status,confidence,expires_at,version FROM knowledge_items WHERE (?='' OR topic_id IN (SELECT id FROM knowledge_topics WHERE topic=?)) ORDER BY last_seen_at DESC LIMIT 30", (topic, topic)).fetchall()]
                await message.answer(json.dumps(rows, ensure_ascii=False)[:3500] or 'itemی نیست.')
            elif action == 'inspect' and len(parts) >= 4:
                async with store._lock:
                    with store._conn() as conn: row = conn.execute('SELECT * FROM knowledge_items WHERE id=?', (parts[3],)).fetchone()
                await message.answer(json.dumps(dict(row) if row else {}, ensure_ascii=False)[:3500])
            elif action in {'expire', 'archive'} and len(parts) >= 4:
                status = 'expired' if action == 'expire' else 'archived'
                async with store._lock:
                    with store._conn() as conn: conn.execute('UPDATE knowledge_items SET status=? WHERE id=?', (status, parts[3])); conn.commit()
                await message.answer(f'item {status} شد.')
            elif action == 'stats':
                await message.answer(json.dumps(await knowledge.status(), ensure_ascii=False, default=str))
            elif action == 'budget':
                values = {key: await store.get_setting(key, str(value)) for key, value in knowledge.DEFAULT_BUDGET.items()} if hasattr(knowledge, 'DEFAULT_BUDGET') else {key: await store.get_setting(key, str(value)) for key, value in {'knowledge_nightly_topic_limit':3,'knowledge_nightly_llm_call_limit':3,'knowledge_results_per_topic':3,'knowledge_pages_per_topic':2,'knowledge_runtime_limit_minutes':20}.items()}
                await message.answer(json.dumps(values, ensure_ascii=False))
            elif action == 'backend':
                if len(parts) >= 4 and parts[3].lower() in {'remote','local'}: knowledge.active_backend = parts[3].lower()
                await message.answer(f"backend={knowledge.active_backend} model={knowledge.backends[knowledge.active_backend].model_name}")
            elif action == 'schedule':
                schedule = await knowledge.schedule_status()
                await message.answer(json.dumps(schedule, ensure_ascii=False, default=str) + '\nsettings: topic_limit=1 llm_call_limit=2 results_per_topic=2 pages_per_topic=1 runtime_limit_minutes=15 retry_model=1 retry_web=1 auto_enabled=true')
            else:
                await message.answer('Usage: /zero knowledge [status|topics|enable|disable|run-now|items [topic]|inspect <id>|expire <id>|archive <id>|stats|budget|backend [remote|local]|schedule]')
        elif sub == 'jobs':
            action = parts[2].lower() if len(parts) >= 3 else 'status'
            try:
                if action == 'create':
                    request = ' '.join(parts[3:])
                    if not request:
                        raise JobSecurityError('Usage: /zero jobs create <درخواست طبیعی>')
                    template_id, inputs, schedule, title = parse_natural_job(request)
                    draft = await jobs.create_draft(int(message.from_user.id), await primary_group_id(), title, template_id, inputs, schedule)
                    next_run = datetime.fromtimestamp(draft['next_run_at'], ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M %Z')
                    await message.answer(f"Simulation\n\nJob ID: {draft['job_id']}\nTemplate: {draft['template']} v{draft['template_version']}\nSchedule: {draft['schedule']}\nTimezone: Asia/Tehran\nRisk: {draft['risk']}\nNetwork: {'ON (Google CSE داخلی)' if draft['network'] != 'off' else 'OFF'}\nResources:\n- Python: Disabled\n- Shell: Disabled\n- Runner: Disabled\n- Timeout: 30s\n- Output: 1MB\nNext Run: {next_run}\n\nبرای تأیید:\n/zero jobs approve {draft['job_id']}")
                elif action == 'approve' and len(parts) >= 4:
                    job = await jobs.approve(int(message.from_user.id), parts[3])
                    await message.answer(f"Job enabled: {job['job_id']}\nnext_run={job['next_run_at']}")
                elif action in {'pause', 'resume', 'enable', 'disable'} and len(parts) >= 4:
                    state = {'pause':'paused', 'resume':'enabled', 'enable':'enabled', 'disable':'disabled'}[action]
                    await jobs.set_state(int(message.from_user.id), parts[3], state)
                    await message.answer(f'Job {state} شد.')
                elif action == 'delete' and len(parts) >= 4:
                    await jobs.delete(int(message.from_user.id), parts[3]); await message.answer('Job حذف شد.')
                elif action == 'list':
                    rows = await jobs.list_jobs(int(message.from_user.id)); await message.answer('\n'.join(f"{r['job_id']} {r['template_id']} {r['state']} next={r['next_run_at']}" for r in rows)[:3500] or 'Jobی نیست.')
                elif action == 'status' and len(parts) >= 4:
                    row = await jobs.status(parts[3], int(message.from_user.id)); await message.answer(json.dumps(row, ensure_ascii=False, indent=2)[:3500])
                elif action == 'logs' and len(parts) >= 4:
                    rows = await jobs.logs(parts[3], actor=int(message.from_user.id)); await message.answer(json.dumps(rows, ensure_ascii=False, indent=2)[:3500] or 'Logی نیست.')
                elif action == 'metrics' and len(parts) >= 4:
                    row = await jobs.status(parts[3], int(message.from_user.id)); await message.answer(json.dumps(row.get('metrics', {}), ensure_ascii=False, indent=2) or 'Metricی نیست.')
                elif action in {'allow', 'deny'} and len(parts) >= 4 and parts[3].isdigit():
                    await jobs.grant_cron_admin(int(message.from_user.id), int(parts[3]), action == 'allow')
                    await message.answer(f"Cron Admin {'فعال' if action == 'allow' else 'لغو'} شد.")
                elif action == 'permissions':
                    await message.answer('Owner فقط با owner_user_id config شناخته می‌شود. Cron Admin فقط توسط Owner تعیین/لغو می‌شود.')
                else:
                    templates = await jobs.template_list()
                    available = ', '.join(t['template_id'] for t in templates if t['enabled'])
                    await message.answer('Template Jobs V1 — بدون code/shell/runner\n' + f'Templates: {available}\nUsage: /zero jobs create <درخواست طبیعی> | list | status <id> | approve <id> | pause/resume/delete/logs/metrics <id> | allow/deny <user_id>')
            except JobSecurityError as exc:
                await message.answer(f'Job ساخته/اجرا نشد: {exc}')
        elif sub == 'awareness':
            try:
                key, value = parse_awareness_command(parts[2:])
            except ValueError as exc:
                await message.answer(str(exc))
            else:
                if key != 'status':
                    await store.set_setting(key, str(bool(value)).lower())
                    await message.answer(f'{key}={str(bool(value)).lower()}')
                else:
                    state = await awareness.group_state(await primary_group_id())
                    values = {name: await awareness.enabled(name, True) for name in (
                        'social_awareness_enabled', 'curiosity_enabled', 'human_delay_enabled',
                        'silence_engine_enabled', 'emotion_awareness_enabled', 'reaction_awareness_enabled',
                    )}
                    await message.answer(
                        'Social Awareness\n' + '\n'.join(f'{key}={str(value).lower()}' for key, value in values.items()) +
                        f"\nreputation={state.get('social_reputation', 0)} confidence={float(state.get('social_confidence', 1.0)):.2f}" +
                        '\nUsage: /zero awareness [status|on|off|curiosity on/off|delay on/off|silence on/off|emotion on/off|reaction on/off]'
                    )
        elif sub == 'social':
            try:
                action, value = parse_social_command(parts[2:])
            except ValueError as exc:
                await message.answer(str(exc))
            else:
                setting_actions = {
                    'welcome_on': ('welcome_enabled', 'true', 'Welcome روشن شد.'),
                    'welcome_off': ('welcome_enabled', 'false', 'Welcome خاموش شد.'),
                    'inactive_on': ('inactive_ping_enabled', 'true', 'Inactive ping روشن شد.'),
                    'inactive_off': ('inactive_ping_enabled', 'false', 'Inactive ping خاموش شد.'),
                    'leave_dm_on': ('leave_dm_enabled', 'true', 'Leave DM روشن شد.'),
                    'leave_dm_off': ('leave_dm_enabled', 'false', 'Leave DM خاموش شد.'),
                }
                if action in setting_actions:
                    key, setting_value, reply = setting_actions[action]
                    await store.set_setting(key, setting_value)
                    await message.answer(reply)
                elif action == 'inactive_days':
                    await store.set_setting('inactive_days_threshold', str(value))
                    await message.answer(f'Inactive threshold={value} روز ثبت شد.')
                else:
                    status = await social.status()
                    await message.answer(
                        f"Welcome: {'ON' if status['welcome_enabled'] else 'OFF'}\n"
                        f"Inactive ping: {'ON' if status['inactive_ping_enabled'] else 'OFF'}\n"
                        f"Inactive threshold: {status['inactive_days_threshold']} days\n"
                        f"Inactive daily limit: {status['inactive_ping_daily_limit']}\n"
                        f"Leave DM: {'ON' if status['leave_dm_enabled'] else 'OFF'}\n"
                        'Usage: /zero social [status|welcome on/off|inactive on/off|inactive days <1-90>|leave-dm on/off]'
                    )
        elif sub == 'reactions':
            try:
                action, value = parse_reaction_command(parts[2:])
            except ValueError as exc:
                await message.answer(str(exc))
            else:
                if action == 'on':
                    await store.set_setting('reactions_enabled', 'true')
                    await message.answer('Reactions روشن شد.')
                elif action == 'off':
                    await store.set_setting('reactions_enabled', 'false')
                    await message.answer('Reactions خاموش شد.')
                elif action == 'chance':
                    await store.set_setting('reactions_chance', str(value))
                    await message.answer(f'Reaction chance={value}% ثبت شد.')
                elif action == 'limit':
                    await store.set_setting('reactions_limit', str(value))
                    await message.answer(f'Reaction limit={value}/hour ثبت شد.')
                elif action == 'cooldown':
                    await store.set_setting('reactions_cooldown_seconds', str(value))
                    await message.answer(f'Reaction cooldown={value}s ثبت شد.')
                elif action in {'read_on', 'read_off'}:
                    enabled = action == 'read_on'
                    await store.set_setting('reactions_read_enabled', str(enabled).lower())
                    await message.answer(f"Reaction read {'روشن' if enabled else 'خاموش'} شد.")
                else:
                    enabled = await store.get_setting('reactions_enabled', str(config.reactions.enabled).lower())
                    chance = await store.get_setting('reactions_chance', str(config.reactions.chance_percent))
                    limit = await store.get_setting('reactions_limit', str(config.reactions.max_per_hour))
                    cooldown = await store.get_setting('reactions_cooldown_seconds', str(config.reactions.user_cooldown_seconds))
                    read_enabled = await store.get_setting('reactions_read_enabled', str(config.reactions.read_enabled).lower())
                    sent = await store.count_rate_events(0, 'reaction_sent', 3600)
                    limited = await store.count_rate_events(0, 'reaction_rate_limited', 3600)
                    if action == 'stats':
                        await message.answer(f'Reactions sent last hour={sent}\nrate_limited last hour={limited}\nlimit={limit}/hour')
                    else:
                        await message.answer(
                            f"Reactions: {'ON' if enabled == 'true' else 'OFF'}\n"
                            f"chance={chance}%\nlimit={limit}/hour\nuser_cooldown={cooldown}s\n"
                            f"read={'ON' if read_enabled == 'true' else 'OFF'}\n"
                            'Usage: /zero reactions [on|off|status|chance <0-100>|limit <1-10>|cooldown <seconds>|read on|read off|stats]'
                        )
        elif sub == 'logs':
            log_path = Path(config.logs.listener_log)
            text = log_path.read_text(encoding='utf-8', errors='ignore').splitlines()[-30:]
            await message.answer('\n'.join(text) or 'log خالیه.')
        elif sub == 'requests':
            req_path = zero_home() / "logs" / "requests.log"
            if req_path.exists():
                text = req_path.read_text(encoding='utf-8', errors='ignore').splitlines()[-30:]
                await message.answer('\n'.join(text) or 'requests log خالیه.')
            else:
                await message.answer('requests log وجود ندارد.')
        elif sub == 'debug':
            if len(parts) < 4:
                await message.answer('Usage: /zero debug [webtest|tgtest|all] <query>')
                return
            debug_action = parts[2].lower()
            debug_query = ' '.join(parts[3:])

            if debug_action == 'webtest':
                web_enabled = await brain.web.is_tool_enabled()
                if not web_enabled:
                    await message.answer(f'Web search غیرفعال است. enable={web_enabled}')
                    return
                searx_ok, searx_err = await brain.web.health_check()
                if not searx_ok:
                    await message.answer(f'Google CSE: ❌ {searx_err}')
                    return
                t0 = time.time()
                hits = await debug_web_search_hits(brain.web, debug_query)
                elapsed = time.time() - t0
                result_lines = [f'Web search results for: {debug_query}']
                result_lines.append(f'Results: {len(hits)} | Time: {elapsed:.2f}s | Google CSE: ✅')
                for hit in hits[:5]:
                    result_lines.append(f"• {hit.title[:80]}\n  {hit.url}")
                if not hits:
                    result_lines.append('(no results)')
                await message.answer('\n\n'.join(result_lines))

            elif debug_action == 'tgtest':
                from zero.telegram_search import TelegramSearchClient
                tg = TelegramSearchClient(config, store)
                tg_enabled = await tg.is_tool_enabled()
                if not tg_enabled:
                    await message.answer(f'Telegram search غیرفعال است. enabled={tg_enabled}')
                    return
                t0 = time.time()
                hits = await tg.search(debug_query)
                elapsed = time.time() - t0
                result_lines = [f'Telegram search results for: {debug_query}']
                result_lines.append(f'Results: {len(hits)} | Time: {elapsed:.2f}s | Chats: {len(config.telegram_search.allowed_chat_usernames)}')
                for hit in hits[:5]:
                    link = hit.link or hit.chat
                    result_lines.append(f"• [{hit.chat}] {hit.sender}: {hit.text[:100]}")
                if not hits:
                    result_lines.append('(no results)')
                await message.answer('\n\n'.join(result_lines))

            elif debug_action == 'all':
                t0_total = time.time()
                report = []

                # Web test
                web_enabled = await brain.web.is_tool_enabled()
                searx_ok, searx_err = await brain.web.health_check()
                t0 = time.time()
                web_hits = await debug_web_search_hits(brain.web, debug_query) if (web_enabled and searx_ok) else []
                web_time = time.time() - t0
                report.append(f"🌐 Web: enabled={web_enabled} searxng={'✅' if searx_ok else '❌'+searx_err} results={len(web_hits)} time={web_time:.2f}s")

                # TG test
                from zero.telegram_search import TelegramSearchClient
                tg = TelegramSearchClient(config, store)
                tg_enabled = await tg.is_tool_enabled()
                t0 = time.time()
                tg_hits = await tg.search(debug_query) if tg_enabled else []
                tg_time = time.time() - t0
                report.append(f"📱 TG Search: enabled={tg_enabled} results={len(tg_hits)} time={tg_time:.2f}s")

                report.append(f"⏱ Total: {time.time() - t0_total:.2f}s")
                await message.answer('\n'.join(report))
            else:
                await message.answer('Debug actions: webtest, tgtest, all')
        elif sub == 'start':
            await message.answer(str(start_listener()))
        elif sub == 'stop':
            await message.answer(str(stop_listener()))
        elif sub == 'restart':
            await message.answer(str(restart_listener()))
        else:
            await message.answer('subcommand نامعتبره.')
        logger.info('OWNER_CMD sub=%s', sub)

    # start_polling used to be the last statement of the process, so nothing
    # released the panel's listening socket or closed the aiogram HTTP session:
    # SIGTERM left "Unclosed client session" warnings and tore down in-flight
    # SSE responses mid-write. Teardown is shielded so a cancellation delivered
    # during shutdown cannot abandon it half-done.
    try:
        await dp.start_polling(bot)
    finally:
        await asyncio.shield(_shutdown(logger, panel_api, bot))


async def _shutdown(logger, panel_api, bot) -> None:
    """Release the panel socket and the bot session; never raise.

    Teardown failures are logged and swallowed on purpose: they must not mask
    the exception or signal that caused the process to stop.
    """
    try:
        await panel_api.stop()
    except Exception as exc:
        logger.warning('PANEL_SHUTDOWN_FAILED error=%s', type(exc).__name__)
    try:
        await bot.session.close()
    except Exception as exc:
        logger.warning('BOT_SESSION_CLOSE_FAILED error=%s', type(exc).__name__)
    logger.info('ZERO_PANEL_STOPPED')


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
