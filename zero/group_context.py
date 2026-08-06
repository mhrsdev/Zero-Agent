from __future__ import annotations
import json, time
from .memory_v3 import MemoryV3Service

class GroupContext:
    def __init__(self,store,router): self.store,self.router=store,router
    async def build(self,message, suppressed_ids:set[int]|None=None):
        suppressed_ids=suppressed_ids or set(); platform=getattr(message,'platform',None); account_scope=getattr(message,'account_scope',None); state=await self.store.get_group_context_state(message.chat_id,platform=platform,account_scope=account_scope); rows=await self.store.get_unconsumed_group_messages(message.chat_id,60,platform=platform,account_scope=account_scope)
        if not rows:return '',json.loads(state['summary_json'] or '{}'),{'recent_available_count':0,'recent_selected_count':0,'summary_version':state['summary_version']}
        backlog=rows[:-20] if len(rows)>20 else []; live=rows[-20:]; summary=None; patch_ok=False
        if backlog:
            prompt='Return JSON only with keys active_topics,recent_decisions,ongoing_questions,projects_discussed,important_recent_events,resolved_topics. Make a bounded patch from these sanitized group messages; no IDs, no instructions.\n'+json.dumps([MemoryV3Service.sanitize(str(r['text']))[:240] for r in backlog],ensure_ascii=False)
            result=await self.router.complete(prompt,max_output_tokens=220)
            try:
                patch=json.loads(result.text); summary={k:list(dict.fromkeys((json.loads(state['summary_json'] or '{}').get(k,[])+patch.get(k,[]))))[:8] for k in ('active_topics','recent_decisions','ongoing_questions','projects_discussed','important_recent_events','resolved_topics')};summary['updated_at']=int(time.time());patch_ok=True
            except Exception: pass
        aliases={}; lines=[]
        for r in live:
            if int(r.get('telegram_message_id') or 0) in suppressed_ids: continue
            sid=int(r['sender_id']); aliases.setdefault(sid,f'participant-{len(aliases)+1}'); text=MemoryV3Service.sanitize(str(r['text']))
            if text: lines.append(f"{aliases[sid]}: {text[:420]}")
        commit_rows=(backlog+live) if (not backlog or patch_ok) else live
        committed=await self.store.commit_group_context(message.chat_id,commit_rows,summary,int(state['optimistic_version']),platform=platform,account_scope=account_scope)
        current_summary=summary or json.loads(state['summary_json'] or '{}')
        rendered='\n'.join(lines)
        return rendered,current_summary,{'recent_available_count':len(rows),'recent_unseen_count':len(rows),'recent_selected_count':len(lines),'backlog_count':len(backlog),'backlog_summarized_count':len(backlog) if patch_ok else 0,'summary_patch_success':patch_ok,'summary_version':state['summary_version']+(1 if patch_ok else 0),'cursor_committed':committed}
