from __future__ import annotations
import json, re, time
from dataclasses import dataclass
from datetime import datetime, timedelta

OPS={'recall_profile','find_statements','find_events','find_decisions','find_preferences','find_relationship_facts','continue_project','compare_past_and_present'}
EVIDENCE={'profile_facts','memory_items','historical_messages','recent_turns','decisions_events','mixed'}
TIMES={'none','today','last_night','last_week','recent_days','before_restart'}

@dataclass(frozen=True)
class RetrievalPlan:
    version:int; needs_memory:bool; operation:str; actors:tuple[str,...]=(); subjects:tuple[str,...]=(); time_kind:str='none'; evidence_mode:str='memory_items'

def metadata(message):
    mentions=re.findall(r'(?<!\w)@([A-Za-z0-9_]{4,})',message.text or '')
    refs=['self',*[f'mention:{i}' for i in range(len(mentions))]]
    if message.reply_sender_id: refs.append('reply_target')
    return {'references':refs,'mention_count':len(mentions),'has_reply_target':bool(message.reply_sender_id),'current_chat_only':True}

def parse(text:str, available:set[str])->RetrievalPlan|None:
    try: raw=json.loads(text)
    except (TypeError,json.JSONDecodeError): return None
    if not isinstance(raw,dict) or raw.get('version')!=1 or not isinstance(raw.get('needs_memory'),bool): return None
    if not raw['needs_memory']: return RetrievalPlan(1,False,'recall_profile')
    op=raw.get('operation'); mode=raw.get('evidence_mode'); tk=(raw.get('time') or {}).get('kind','none')
    actors=tuple(x.strip()[:64] for x in raw.get('actors',[]) if isinstance(x,str) and x.strip() and not re.search(r'\d{7,}|[/\\]|select|sqlite',x,re.I))[:4]
    subjects=tuple(x.strip()[:64] for x in raw.get('subjects',[]) if isinstance(x,str) and x.strip() and not re.search(r'\d{7,}|[/\\]|select|sqlite',x,re.I))[:4]
    if op not in OPS or mode not in EVIDENCE or tk not in TIMES: return None
    return RetrievalPlan(1,True,op,actors,subjects,tk,mode)

def window(kind:str, now:float|None=None):
    now=now or time.time(); local=datetime.fromtimestamp(now).astimezone()
    if kind=='today': start=local.replace(hour=0,minute=0,second=0,microsecond=0)
    elif kind=='last_night':
        start=local.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=1)
        return start.timestamp(),local.replace(hour=6,minute=0,second=0,microsecond=0).timestamp()
    elif kind=='last_week': start=local-timedelta(days=7)
    elif kind=='recent_days': start=local-timedelta(days=3)
    elif kind=='before_restart': start=local-timedelta(days=30)
    else: return None
    return start.timestamp(),local.timestamp()

def prompt(message, meta):
    return 'Return JSON only. Plan memory retrieval; never request another chat or invent identifiers. Schema: {"version":1,"needs_memory":bool,"operation":"recall_profile|find_statements|find_events|find_decisions|find_preferences|find_relationship_facts|continue_project|compare_past_and_present","actors":[reference],"subjects":[reference],"time":{"kind":"none|today|last_night|last_week|recent_days|before_restart"},"evidence_mode":"profile_facts|memory_items|historical_messages|recent_turns|decisions_events|mixed"}. Available references: '+json.dumps(meta,ensure_ascii=False)+'\nUser text: '+(message.text or '')[:1200]
