#!/usr/bin/env python3
"""One-time conservative V2 backfill from trusted V1 user messages; no raw text is logged."""
import argparse, asyncio, hashlib, json, sqlite3, time, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from zero.memory_v2 import MemoryV2Service
from zero.memory_v2.service import MemoryItem
from zero.semantic_memory import SemanticUserMemory

def main():
 p=argparse.ArgumentParser();p.add_argument('--v1-db',default='runtime/state/zero.db');p.add_argument('--v2-db',default='runtime/state/zero-memory-v2.db');p.add_argument('--state-file',default='runtime/state/memory-v2-backfill.json');p.add_argument('--dry-run',action='store_true');p.add_argument('--apply',action='store_true');a=p.parse_args()
 if a.apply==a.dry_run: raise SystemExit('choose exactly one of --dry-run/--apply')
 state=Path(a.state_file)
 if a.apply and state.exists(): raise SystemExit('backfill already completed; state file exists')
 src=sqlite3.connect(a.v1_db);src.row_factory=sqlite3.Row; extractor=SemanticUserMemory(a.v1_db);v2=MemoryV2Service(a.v2_db)
 if not v2.healthy: raise SystemExit('V2 unavailable')
 stats={'messages_scanned':0,'candidate_facts':0,'rejected':0,'duplicates':0,'inserted':0,'superseded':0,'sources':['recent_messages:user','v1_semantic/long/medium already migrated']};seen=set()
 async def run():
  for r in src.execute("select chat_id,sender_id,text,telegram_message_id from recent_messages where role='user' order by id"):
   stats['messages_scanned']+=1; text=r['text'] or ''; digest=hashlib.sha256(text.encode()).hexdigest()
   if digest in seen:continue
   seen.add(digest)
   for f in extractor.extract_explicit(text):
    value=str(f['value']).strip(); content=f"{f['category']}.{f['key']}={value}"; clean=v2.sanitize(content)
    if not clean:stats['rejected']+=1;continue
    stats['candidate_facts']+=1
    c=sqlite3.connect(a.v2_db);before=c.execute("select id from memory_v2_items where scope='group_user' and chat_id=? and user_id=? and subject='user' and predicate=? and status='active'",(r['chat_id'],r['sender_id'],f"{f['category']}.{f['key']}")).fetchone();c.close()
    item=MemoryItem('', 'profile','group_user',clean,' '.join(clean.casefold().split()),r['sender_id'],r['chat_id'],group_id=r['chat_id'],subject='user',predicate=f"{f['category']}.{f['key']}",object=value,importance=.8,confidence=f['confidence'],source_message_ids=(int(r['telegram_message_id'] or 0),),source_type='one_time_backfill',metadata={'source':'trusted_v1_user_history'})
    if a.dry_run: continue
    iid=await v2.put(item)
    if before and before[0]==iid:stats['duplicates']+=1
    elif before:stats['superseded']+=1;stats['inserted']+=1
    else:stats['inserted']+=1
 asyncio.run(run());src.close()
 if a.apply:
  state.parent.mkdir(parents=True,exist_ok=True);state.write_text(json.dumps({'completed_at':int(time.time()),'stats':stats},indent=2));state.chmod(0o600)
 print(json.dumps(stats,ensure_ascii=False))
if __name__=='__main__':main()
