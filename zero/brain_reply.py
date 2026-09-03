"""No-media reply path extracted from ZeroBrain."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime

from .brain_generate import reply_char_limit, sanitize_internal_search_status
from .group_policy import GroupPolicy
from .memory import build_group_summary
from .models import Decision, IncomingMessage
from .prompts import build_reply_prompt
from .security import Intent
from .triggers import strip_trigger
from .turn_options import prompt_option_block, web_allowed
from .web import build_search_query, is_current_price_or_market_query, is_deep_search_request, needs_web_search
from .web_search.truth import build_news_fallback, build_numeric_fallback, numeric_fallback_eligible, sanitize_source_display, source_link
from .brain_generate import build_live_market_disclosure, is_telegram_search_request, parse_search_command

logger = logging.getLogger('zero.brain')

_SEARCH_TIMEOUT_SECONDS = 45.0
_DEEP_SEARCH_TIMEOUT_SECONDS = 45.0


class BrainReplyMixin:
    """Text reply path (search + memory context + generation). Mixed into ZeroBrain."""

    async def _handle_no_media(self, message: IncomingMessage, decision: Decision, intent: Intent) -> tuple[Decision, str]:
        recent = await self.store.get_recent(message.chat_id, limit=100)
        if self.v1_memory_runtime_enabled:
            logger.info('MEMORY_RETRIEVAL_STARTED trace_id=%s chat_id=%s query_terms=%s', message.trace_id or '-', message.chat_id, len(re.findall(r'[\wآ-ی‌]{3,}', (message.text or '').lower())))
            layered = await self.store.retrieve_layered_memory(message.chat_id, message.text, sender_id=message.sender_id, short_limit=1, medium_limit=4, long_limit=6)
            social_plus_rows = await self.social_plus.context(message.chat_id, message.text)
            media_rows = [row for row in await self.store.get_recent_media_context(message.chat_id, message.text, limit=5) if int(row.get('sender_id') or 0) == int(message.sender_id)]
        else:
            layered = {'short': [], 'medium': [], 'long': []}; social_plus_rows = []; media_rows = []
            logger.info('MEMORY_V1_RUNTIME_DISABLED trace_id=%s', message.trace_id or '-')
        followup_info = await self._media_followup_info(message)
        if followup_info:
            logger.info('MEMORY_MEDIA_CONTEXT_RETRIEVED trace_id=%s chat_id=%s sender_id=%s media_message_id=%s media_type=%s age_seconds=%s reason=validated_followup', message.trace_id or '-', message.chat_id, message.sender_id, followup_info['media_message_id'], followup_info['media_type'], followup_info['age_seconds'])
            matching = next((r for r in media_rows if int(r.get('message_id') or 0) == followup_info['media_message_id']), None)
            if matching and not (matching.get('summary') or '').strip():
                return decision, f"یک {followup_info['media_type']} فرستادی، ولی محتوایش را دقیق تحلیل نکردم."
        if media_rows:
            logger.info('MEMORY_MEDIA_CONTEXT_RETRIEVED trace_id=%s chat_id=%s count=%s', message.trace_id or '-', message.chat_id, len(media_rows))
        logger.info('MEMORY_RETRIEVED trace_id=%s chat_id=%s short_count=%s medium_count=%s long_count=%s', message.trace_id or '-', message.chat_id, len(layered['short']), len(layered['medium']), len(layered['long']))
        group_summary = build_group_summary(recent, [])
        memory_lines = []
        for row in media_rows:
            detail = row.get('summary') or f"یک {row.get('media_type','media')} فرستاده شد؛ تحلیل دقیق ثبت نشده است"
            memory_lines.append(f"[SHORT_MEDIA_CONTEXT] message_id={row.get('message_id')} type={row.get('media_type')} caption={row.get('caption','')[:160]} detail={detail[:240]}")
        for row in layered['short']:
            memory_lines.append(f"[SHORT_CONTEXT] topic={row.get('active_topic','')} mood={row.get('mood','neutral')} should_reply={row.get('should_reply',0)}")
        for row in social_plus_rows[:6]:
            if row.get('kind') == 'thread':
                memory_lines.append(f"[SOCIAL_THREAD] topic={row.get('topic','')} summary={row.get('summary','')[:240]}")
            elif row.get('kind') == 'inside_joke':
                memory_lines.append(f"[INSIDE_JOKE] phrase={row.get('phrase','')[:80]}")
        for row in layered['medium']:
            memory_lines.append(f"[MEDIUM_MEMORY] {row.get('topic','')}: {row.get('summary','')}")
        for row in layered['long']:
            memory_lines.append(f"[LONG_MEMORY] [{row.get('category','')}] {row.get('content','')}")
        # Defense in depth: storage resolves conflicts, but prompt construction must
        # never expose duplicate keys or two active values for the same memory key.
        deduped = []
        seen_keys = set()
        for line in memory_lines:
            normalized = re.sub(r'\s+', ' ', line.casefold()).strip()
            tag = re.match(r'\[([^]]+)\]', normalized)
            body = normalized[tag.end():].strip() if tag else normalized
            if tag and tag.group(1) == 'long_memory':
                category = re.match(r'\[([^]]+)\]', body)
                key = ('long_memory', category.group(1).strip() if category else body)
            elif tag and tag.group(1) in {'medium_memory', 'social_thread', 'inside_joke'}:
                key = (tag.group(1), re.split(r':|=', body, maxsplit=1)[0].strip())
            else:
                key = (tag.group(1) if tag else 'raw', normalized)
            if key in seen_keys:
                logger.info('MEMORY_CONTEXT_DEDUPED trace_id=%s chat_id=%s key=%s', message.trace_id or '-', message.chat_id, key[0])
                continue
            seen_keys.add(key)
            deduped.append(line)
        memory_lines = deduped
        short_lines = [x for x in memory_lines if x.startswith(('[SHORT_', '[SOCIAL_', '[INSIDE_'))]
        medium_lines = [x for x in memory_lines if x.startswith('[MEDIUM_')]
        long_lines = [x for x in memory_lines if x.startswith('[LONG_')]
        budget = {'short': 10000, 'medium': 8000, 'long': 7000}
        before = len(memory_lines)
        def trim_lines(lines, limit):
            out, used = [], 0
            for line in lines:
                if used + len(line) > limit: break
                out.append(line); used += len(line)
            return out
        memory_lines = trim_lines(short_lines, budget['short']) + trim_lines(medium_lines, budget['medium']) + trim_lines(long_lines, budget['long'])
        dropped = before - len(memory_lines)
        if dropped:
            logger.info('MEMORY_CONTEXT_TRIMMED trace_id=%s chat_id=%s dropped_count=%s', message.trace_id or '-', message.chat_id, dropped)
        logger.info('MEMORY_BUDGET_APPLIED trace_id=%s chat_id=%s short_tokens=%s medium_tokens=%s long_tokens=%s total_memory_tokens=%s dropped_count=%s final_context_ratio=%.3f', message.trace_id or '-', message.chat_id, len(' '.join(short_lines))//4, len(' '.join(medium_lines))//4, len(' '.join(long_lines))//4, len(' '.join(memory_lines))//4, dropped, min(1.0, len(' '.join(memory_lines)) / 10400))
        layered_memory_context = '\n'.join(memory_lines)
        logger.info('MEMORY_CONTEXT_BUILT trace_id=%s chat_id=%s lines=%s', message.trace_id or '-', message.chat_id, len(memory_lines))
        memory_context = ''
        mode = await self._mode()
        clean_user_text = strip_trigger(message.text, self.config.listener.account_username)
        search_command = parse_search_command(message.text)
        if search_command:
            clean_user_text = search_command[1]
        trace_id = message.trace_id or '-'
        web_context = ''
        telegram_context = ''
        live_market_disclosure = ''
        market_searched_at = ''
        web_outcome = None
        target_user_id, target_kind = self._memory_target(message)
        identity_lookup = target_kind != 'speaker' and bool(re.search(r'کیه|کی هست|میشناسی|who is|who are', message.text or '', re.I))
        scoped_memory = self._memory_for(message)
        policy = self._group_policy_for(message)
        if not policy.memory_enabled or policy.memory_inject_depth == 'off':
            memory_context, memory_meta = '', {'selected': 0, 'tokens': 0}
        else:
            memory_context, memory_meta = await scoped_memory.context(
                message,
                target_user_id=target_user_id,
                identity_lookup=identity_lookup,
            )
            memory_context = self._apply_prompt_budget(memory_context)
        if memory_context and target_kind != 'speaker':
            memory_context = f'Retrieval target: {target_kind}; do not attribute these facts to the current speaker.\n' + memory_context
        await scoped_memory.metric(trace_id, 'active', {
            'selected_items': memory_meta.get('selected', 0),
            'selected_tokens': memory_meta.get('tokens', 0),
            'target_kind': target_kind,
            'target_is_speaker': target_kind == 'speaker',
            'context_total_tokens': len(memory_context) // 4,
        })
        logger.info(
            'MEMORY_CONTEXT_COMPOSED trace_id=%s chat_id=%s sender_id=%s chars=%s target_ids=%s',
            trace_id, message.chat_id, message.sender_id, len(memory_context),
            memory_meta.get('target_user_ids', []),
        )
        search_mode, search_text = search_command or ('', clean_user_text)
        deep_search = search_mode == 'deep' or is_deep_search_request(clean_user_text)
        is_telegram_request = is_telegram_search_request(clean_user_text)
        web_enabled = web_allowed(policy, await self.web.is_tool_enabled())
        natural_web_intent = bool(search_text and needs_web_search(search_text, reply_text=message.reply_text))
        market_tool_intent = bool(search_text and is_current_price_or_market_query(search_text))
        web_intent = bool(search_text and (search_command or natural_web_intent) and not market_tool_intent)
        logger.info('WEB_SEARCH_ENABLED_CHECK trace_id=%s enabled=%s intent=%s natural=%s market_tool=%s mode=%s', trace_id, web_enabled, web_intent, natural_web_intent, market_tool_intent, search_mode or 'natural')
        if not web_enabled:
            logger.info('WEB_SEARCH_SKIPPED trace_id=%s reason=disabled', trace_id)
            if web_intent:
                return decision, 'وب‌سرچ فعلاً فعال نیست.'
        elif not web_intent:
            logger.info('WEB_SEARCH_SKIPPED trace_id=%s reason=intent_not_detected', trace_id)
        else:
            if deep_search:
                # Quotas come from config and 0 means unlimited; this deployment
                # ships unlimited. The global check runs BEFORE the per-user
                # reservation on purpose: reserving the user's slot first spent it
                # even when the install-wide capacity was already full, so a user
                # was charged for a deep search that never ran.
                web_cfg = self.config.web
                global_limit = int(web_cfg.deep_search_global_hourly or 0)
                if global_limit > 0:
                    global_allowed, global_used = await self.store.try_reserve_rate_event(0, 'deep_search_global', 3600, global_limit)
                    if not global_allowed:
                        logger.info('DEEP_SEARCH_GLOBAL_LIMIT trace_id=%s used=%s limit=%s', trace_id, global_used, global_limit)
                        return Decision(True, 'deep_search_global_limit'), 'ظرفیت سرچ عمیق فعلاً پر شده؛ کمی بعد دوباره امتحان کن.'
                is_owner = message.sender_id == self.config.owner_user_id
                user_limit = int((web_cfg.deep_search_owner_hourly if is_owner else web_cfg.deep_search_user_hourly) or 0)
                if user_limit > 0:
                    allowed, used = await self.store.try_reserve_rate_event(message.sender_id, 'deep_search', 3600, user_limit)
                    if not allowed:
                        logger.info('DEEP_SEARCH_RATE_LIMIT trace_id=%s sender_id=%s used=%s limit=%s', trace_id, message.sender_id, used, user_limit)
                        return Decision(True, 'deep_search_rate_limit'), 'سهمیهٔ سرچ عمیق این ساعتت تموم شده؛ کمی بعد دوباره امتحان کن.'
                if global_limit <= 0 and user_limit <= 0:
                    logger.info('DEEP_SEARCH_UNLIMITED trace_id=%s sender_id=%s', trace_id, message.sender_id)
            try:
                run_search = self.web.run(
                    search_text,
                    reply_text=message.reply_text,
                    recent_messages=recent,
                    trace_id=trace_id,
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    message_id=message.message_id,
                    thread_id=message.thread_id,
                    reply_to_message_id=message.reply_to_message_id,
                    search_session_id=f'{message.chat_id}:{message.sender_id}:{message.thread_id or message.reply_to_message_id or "root"}',
                    force_search=bool(search_command),
                    deep=deep_search,
                )
                web_outcome = await asyncio.wait_for(run_search, timeout=_DEEP_SEARCH_TIMEOUT_SECONDS if deep_search else _SEARCH_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                if deep_search:
                    logger.warning('DEEP_SEARCH_TIMEOUT trace_id=%s timeout_seconds=%s', trace_id, _DEEP_SEARCH_TIMEOUT_SECONDS)
                    return decision, 'سرچ عمیق به سقف زمانی رسید و نتیجهٔ قابل‌اعتماد کامل نشد؛ کمی بعد دوباره امتحان کن.'
                logger.warning('WEB_SEARCH_TIMEOUT trace_id=%s timeout_seconds=%s', trace_id, _SEARCH_TIMEOUT_SECONDS)
                return decision, 'جستجو در این نوبت کامل نشد؛ کمی بعد دوباره امتحان کن.'
            except Exception as exc:
                logger.warning('WEB_SEARCH_FAILED trace_id=%s exception_type=%s', trace_id, type(exc).__name__)
                return decision, 'جستجو در این نوبت کامل نشد؛ کمی بعد دوباره امتحان کن.'
            logger.info('WEB_OUTCOME trace_id=%s result_count=%s all_providers_failed=%s no_results=%s context_chars=%s', trace_id, len(web_outcome.results), web_outcome.all_providers_failed, web_outcome.no_results, len(web_outcome.context or ''))
            if web_outcome.clarification_required:
                reply = 'چی رو بگردم؟ موضوع، اسم، قیمت یا خبری که می‌خوای بررسی کنم رو هم بگو.'
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_REQUEST_CONTEXT_CLEARED trace_id=%s source_count=0', trace_id)
                return decision, reply
            if not web_outcome.intent.supported:
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=unsupported_intent', trace_id)
                return decision, ''
            if web_outcome.results:
                web_context = web_outcome.context
                if web_outcome.intent.category == 'current_price_or_market_query':
                    market_searched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                    searched_at_utc = market_searched_at
                    live_market_disclosure = build_live_market_disclosure(
                        web_outcome.results[0].title,
                        web_outcome.results[0].url,
                        searched_at_utc,
                    )
            elif web_outcome.intent.category == 'url_inspection' and (web_outcome.all_providers_failed or web_outcome.no_results):
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=url_unreadable', trace_id)
                return decision, 'این لینک را به محتوای قابل‌خواندن تبدیل نکردم؛ لینک منبع اصلی یا اسکرین‌شات را بفرست تا دقیق بررسی کنم.'
            elif web_outcome.all_providers_failed:
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=providers_failed', trace_id)
                return decision, 'فعلاً وب‌سرچ در دسترس نیست؛ کمی بعد دوباره امتحان کن.'
            elif web_outcome.no_results:
                self.web.mark_response_sent(trace_id=trace_id, result_count=0, guarded=True)
                logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=no_results', trace_id)
                return decision, 'برای این جستجو نتیجه‌ای پیدا نکردم.'

        from .telegram_search import TelegramSearchClient
        tg = TelegramSearchClient(self.config, self.store, web=self.web)
        tg_enabled = await tg.is_tool_enabled()
        tg_intent = is_telegram_request
        logger.info('TG_SEARCH_ENABLED_CHECK trace_id=%s enabled=%s intent=%s', trace_id, tg_enabled, tg_intent)
        if not tg_enabled:
            logger.info('TG_SEARCH_ARCHIVED trace_id=%s reason=legacy_telegram_search_disabled', trace_id)
            if tg_intent:
                logger.info('TG_INTERNAL_STATUS_SUPPRESSED trace_id=%s reason=archived', trace_id)
                return decision, ''
            logger.info('TG_SEARCH_SKIPPED trace_id=%s reason=disabled', trace_id)
        elif not tg_intent:
            logger.info('TG_SEARCH_SKIPPED trace_id=%s reason=intent_not_detected', trace_id)
        else:
            from .telegram_search import TelegramSearchIntentDetector, TelegramSearchRequest, TelegramSearchContextBuilder
            tg_intent = TelegramSearchIntentDetector().detect(search_text)
            if search_mode in {'telegram', 'combined'}:
                tg_intent = tg_intent.__class__('telegram_message_search', search_text, confidence=1.0)
            tg_query = build_search_query(tg_intent.query or search_text, reply_text=message.reply_text, recent_messages=recent)
            logger.info('TG_SEARCH_INTENT_DETECTED trace_id=%s intent=%s query_hash=%s', trace_id, tg_intent.name, __import__('hashlib').sha256(tg_query.encode()).hexdigest()[:16])
            if tg_intent.name == 'unsupported_private_access':
                telegram_context = 'Telegram private/invite-only access is unsupported without explicit owner policy.'
            elif not tg_query.strip():
                logger.warning('TG_SEARCH_NO_RESULTS trace_id=%s reason=empty_query', trace_id)
            else:
                try:
                    req = TelegramSearchRequest(trace_id=trace_id, chat_id=message.chat_id, sender_id=message.sender_id, search_session_id=f'{message.chat_id}:{message.sender_id}:{message.reply_to_message_id or "root"}', thread_id=message.thread_id, reply_to_message_id=message.reply_to_message_id, query=tg_query, intent=tg_intent.name, limit=5, inspect_target=tg_intent.target, allow_web_fallback=True)
                    logger.info('TG_SEARCH_ROUTED trace_id=%s intent=%s', trace_id, tg_intent.name)
                    outcomes = await tg.search_request(req)
                    telegram_context = TelegramSearchContextBuilder().build(outcomes, limit=5, max_chars=6000)
                    result_count = sum(len(x.results) for x in outcomes)
                    logger.info('TG_SEARCH_CONTEXT_BUILT trace_id=%s result_count=%d', trace_id, result_count)
                    if not result_count:
                        telegram_context = 'در محدوده قابل‌دسترسی این حساب و منابع عمومی نتیجه‌ای پیدا نکردم.'
                        logger.info('TG_SEARCH_NO_RESULTS trace_id=%s', trace_id)
                except Exception as e:
                    telegram_context = 'جستجوی Telegram در این نوبت در دسترس نبود؛ نتیجه‌ای ساخته نمی‌شود.'
                    logger.warning('TG_SEARCH_PROVIDER_FAILED trace_id=%s exception_type=%s', trace_id, type(e).__name__)

        doc_context=''; bundle_suppressed=set(); bundle_entries=[]
        if os.getenv('ZERO_GROUP_DOCUMENT_BUNDLING_ENABLED','false').lower()=='true':
            bundle_suppressed=self.document_bundles.active_part_ids(message.chat_id); bundle_entries=self.document_bundles.live_entries(message.chat_id)
            doc_context, _, _ = self.document_bundles.reference(message)
            if doc_context: memory_context='\n'.join(x for x in (memory_context,doc_context) if x)
        live_group_context=''; group_summary=''
        if os.getenv('ZERO_HYBRID_GROUP_CONTEXT_ENABLED','false').lower()=='true' and message.chat_id<0:
            try:
                live_group_context, summary_payload, group_meta=await self.group_context.build(message,bundle_suppressed)
                if bundle_entries: live_group_context='\n'.join([*bundle_entries,live_group_context])
                group_summary=json.dumps(summary_payload,ensure_ascii=False)
                await self.memory_v3.metric(trace_id,'group_context',group_meta)
            except Exception:
                live_group_context=''; group_summary=''
        prompt = build_reply_prompt(
            self.config, mode=mode, sender_label=message.sender_label,
            user_text=clean_user_text, reply_text=message.reply_text,
            recent=live_group_context.splitlines(), group_summary=group_summary,
            web_context=web_context, telegram_context=telegram_context, memory_context=memory_context,
            chat_id=message.chat_id, sender_id=message.sender_id, message_id=message.message_id, sender_is_bot=message.sender_is_bot, thread_id=message.thread_id, reply_to_message_id=message.reply_to_message_id,
            reply_sender_id=message.reply_sender_id, reply_sender_label=message.reply_sender_label, reply_sender_is_bot=message.reply_sender_is_bot,
            deep_research=deep_search,
        )
        extra = prompt_option_block(policy)
        if extra:
            prompt = extra + prompt
        if policy.provider_profile:
            setattr(self.router, 'turn_profile', policy.provider_profile)
        # Prompt size is the per-message cost of this system and nothing measured
        # it: the legacy router reads len(prompt) only to order providers, and
        # input_tokens is reported by the ProviderRegistry, which is not the
        # default composition. Logged per block so a block that starts growing is
        # attributable instead of showing up only on the bill.
        logger.info(
            'PROMPT_BUILT trace_id=%s chars=%d memory_chars=%d web_chars=%d group_summary_chars=%d recent_lines=%d deep=%s',
            trace_id, len(prompt), len(memory_context or ''), len(web_context or ''),
            len(group_summary or ''), len(live_group_context.splitlines()), deep_search,
        )
        tool_evidence: dict = {}
        reply_text = await self._generate_with_knowledge_tool(message, prompt, chat_id=message.chat_id, evidence=tool_evidence)
        raw_link_requested = bool(re.search(r'(?:لینک\s+خام|url\s+خام|raw\s+(?:link|url))', clean_user_text, re.I))
        if web_outcome and 'WEB_STATUS:' in (reply_text or ''):
            status_reply = 'فعلاً سرویس‌های جست‌وجو پاسخ ندادند؛ کمی بعد دوباره امتحان کن.' if web_outcome.all_providers_failed else 'برای این جست‌وجو نتیجه قابل‌اعتمادی پیدا نکردم.'
            logger.info('WEB_INTERNAL_STATUS_SUPPRESSED trace_id=%s status=present', trace_id)
            reply_text = status_reply
        if web_outcome and web_outcome.results:
            fallback_eligible = numeric_fallback_eligible(web_outcome.intent.category)
            logger.info('WEB_FALLBACK_ELIGIBILITY trace_id=%s query_hash=%s intent=%s category=%s eligible=%s reason=category_policy', trace_id, __import__('hashlib').sha256(web_outcome.plan.query.encode()).hexdigest()[:16], web_outcome.intent.kind.value, web_outcome.intent.category, fallback_eligible)
            logger.info('LLM_RESPONSE_BEFORE_GUARD trace_id=%s chars=%d', trace_id, len(reply_text or ''))
            guard = self.web.guard_answer(reply_text, web_outcome.results, trace_id=trace_id, trusted_text=tool_evidence.get('trusted_text', ''))
            logger.info('TRUTHFULNESS_GUARD_DECISION trace_id=%s reason=%s accepted=%s', trace_id, guard.reason or 'none', guard.allowed)
            guard_fallback_used = False
            if not guard.allowed:
                news_fallback = build_news_fallback(web_outcome.results, market_searched_at) if web_outcome.intent.category in {'latest_news', 'research_analysis'} else ''
                fallback = news_fallback or (build_numeric_fallback(web_outcome.results, market_searched_at) if fallback_eligible else '')
                if fallback:
                    reply_text = fallback
                    guard_fallback_used = True
                    logger.info('TRUTHFULNESS_GUARD_FALLBACK_USED trace_id=%s used=true', trace_id)
                else:
                    if not fallback_eligible: logger.info('WEB_FALLBACK_REJECTED trace_id=%s reason=category_not_numeric_or_news', trace_id)
                    if web_outcome.intent.category in {'current_price_or_market_query', 'market_rate', 'exchange_rate', 'numeric_live_value'}:
                        reply_text = 'قیمت قابل‌تأییدی از منابع زنده دریافت نکردم؛ برای جلوگیری از اعلام عدد اشتباه، این نوبت قیمت نمی‌فرستم.'
                    else:
                        seen=set(); source_items=[]
                        for item in web_outcome.results[:3]:
                            key=item.url.rstrip('/').lower()
                            if key in seen: continue
                            seen.add(key); source_items.append(source_link(item.url, item.publisher))
                        logger.info('WEB_SOURCE_DEDUPED trace_id=%s before=%d after=%d', trace_id, min(3,len(web_outcome.results)), len(source_items))
                        reply_text = 'پاسخ تولیدشده ادعای بدون پشتوانه داشت و ارسال نشد. منابع واقعی:\n' + '\n'.join(f'• {x}' for x in source_items)
                    logger.info('TRUTHFULNESS_GUARD_FALLBACK_USED trace_id=%s used=false', trace_id)
            if live_market_disclosure and not guard_fallback_used:
                reply_text = f'{reply_text.rstrip()}{live_market_disclosure}'
            reply_text = sanitize_source_display(reply_text, web_outcome.results, raw_link_requested)
            if deep_search:
                seen_domains: set[str] = set()
                deep_sources: list[str] = []
                for item in web_outcome.results:
                    domain = item.url.split('://', 1)[-1].split('/', 1)[0].lower().removeprefix('www.')
                    if domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    deep_sources.append(source_link(item.url, item.publisher))
                    if len(deep_sources) >= 15:
                        break
                if deep_sources:
                    appendix = '\n\nمنابع بررسی‌شده:\n' + '\n'.join(f'• {source}' for source in deep_sources)
                    body_limit = max(0, reply_char_limit(self.config, message.text) - len(appendix))
                    reply_text = reply_text[:body_limit].rstrip() + appendix
                    logger.info('DEEP_SEARCH_SOURCE_APPENDIX trace_id=%s unique_domains=%d', trace_id, len(deep_sources))
            logger.info('WEB_SOURCE_DEDUPED trace_id=%s source_count=%d unique_source_count=%d', trace_id, len(web_outcome.results), len({item.url.rstrip('/').lower() for item in web_outcome.results}))
            self.web.mark_response_sent(
                trace_id=trace_id,
                result_count=len(web_outcome.results),
                guarded=guard.allowed or guard_fallback_used,
            )
        reply_text = sanitize_source_display(reply_text, [], raw_link_requested)
        if web_intent:
            logger.info('WEB_REQUEST_CONTEXT_CLEARED trace_id=%s query_hash=%s source_count=%d', trace_id, __import__('hashlib').sha256((web_outcome.plan.query if web_outcome else clean_user_text).encode()).hexdigest()[:16], len(web_outcome.results) if web_outcome else 0)
        return decision, reply_text
