#!/usr/bin/env python3
"""Read-only V1 to separate V2 SQLite migration with run-aware rollback."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, sqlite3, sys, time, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from zero.memory_v2.service import MemoryItem, MemoryV2Service
TABLES=('long_term_memory','medium_term_memory','semantic_user_memory','memory_rag_documents','experience_memory','procedural_memory')
def h(v): return hashlib.sha256(json.dumps(v,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()
def j(v,default):
 try:return json.loads(v or '')
 except Exception:return default
def open_source(p):
 c=sqlite3.connect(p);c.row_factory=sqlite3.Row;return c
def init_meta(p):
 c=sqlite3.connect(p);c.executescript('''CREATE TABLE IF NOT EXISTS memory_v2_migration_runs(run_id TEXT PRIMARY KEY,source_hash TEXT,schema_hash TEXT,started_at INTEGER,finished_at INTEGER,status TEXT,dry_run INTEGER,report_json TEXT,tool_version TEXT,rollback_status TEXT);CREATE TABLE IF NOT EXISTS memory_v2_migration_map(run_id TEXT,source_table TEXT,source_key TEXT,source_hash TEXT,target_id TEXT,action TEXT,PRIMARY KEY(run_id,source_table,source_key));CREATE TABLE IF NOT EXISTS memory_v2_migration_quarantine(run_id TEXT,source_table TEXT,source_key TEXT,reason TEXT,source_hash TEXT);''');c.commit();c.close()
def mapper(t,r):
 d=dict(r); now=int(time.time()); chat=d.get('chat_id'); uid=d.get('subject_user_id') or d.get('sender_id'); status=d.get('status') or 'active';
 if status not in {'active','superseded','disputed','expired','deleted'}: status='active'
 if t=='long_term_memory':
  content=str(d.get('content') or '').strip(); typ='project' if str(d.get('category','')).casefold() in {'project','architecture','deployment'} else 'fact'; scope='group_user' if uid is not None else 'group'; return MemoryItem('',typ,scope,content,' '.join(content.casefold().split()),uid,chat,group_id=chat,subject='user' if uid else None,predicate=str(d.get('category') or 'fact'),importance=.8,confidence=float(d.get('confidence') or .6),created_at=d.get('created_at') or now,expires_at=d.get('expires_at'),status=status,source_message_ids=tuple(j(d.get('source_message_ids_json'),[])),source_type='v1_long',metadata={'legacy_id':d.get('memory_id')})
 if t=='medium_term_memory':
  content=str(d.get('summary') or '').strip(); people=j(d.get('participants_json'),[]); return MemoryItem('', 'episode','group' if not people else 'group_user',content,' '.join(content.casefold().split()),people[0] if len(people)==1 else None,chat,group_id=chat,subject='participants',predicate=str(d.get('topic') or 'event'),topics=(str(d.get('topic') or ''),),importance=float(d.get('importance') or .5),confidence=float(d.get('confidence') or .5),created_at=d.get('occurred_at') or now,expires_at=d.get('expires_at'),status=status,source_message_ids=tuple(j(d.get('source_message_ids_json'),[])),source_type='v1_medium',metadata={'legacy_id':d.get('event_id'),'participants':people})
 if t=='semantic_user_memory':
  value=j(d.get('value_json'),d.get('value_json')); content=f"{d.get('category')}.{d.get('key')}={value}"; return MemoryItem('', 'profile','group_user',content,' '.join(content.casefold().split()),d.get('sender_id'),chat,group_id=chat,subject='user',predicate=f"{d.get('category')}.{d.get('key')}",object=str(value),importance=.8,confidence=float(d.get('confidence') or .6),created_at=d.get('first_seen_at') or now,expires_at=d.get('expires_at'),status=status,source_message_ids=tuple(j(d.get('evidence_message_ids_json'),[])),source_type='v1_semantic',metadata={'legacy_id':d.get('id')})
 if t=='experience_memory':
  content=f"{d.get('topic')}: cause={d.get('root_cause')}; fix={d.get('fix')}";return MemoryItem('', 'episode','project',content,' '.join(content.casefold().split()),project_id='zero',subject=str(d.get('topic') or 'experience'),predicate='bug_fix',importance=.9,confidence=float(d.get('confidence') or .6),created_at=d.get('first_seen_at') or now,status=status,source_type='v1_experience',metadata={'legacy_id':d.get('id'),'evidence':j(d.get('evidence_json'),[])})
 if t=='procedural_memory':
  steps=j(d.get('steps_json'),[]);content=f"{d.get('name')}: " + ' | '.join(map(str,steps));return MemoryItem('', 'procedure','project',content,' '.join(content.casefold().split()),project_id='zero',subject=str(d.get('name') or 'procedure'),predicate='workflow',importance=.8,confidence=.8,created_at=d.get('approved_at') or now,status=status,source_type='v1_procedure',metadata={'legacy_id':d.get('id'),'risk_level':d.get('risk_level')})
 return None # RAG is archive-only and deliberately not promoted.
def key(t,r):
 d=dict(r); return str(d.get({'long_term_memory':'memory_id','medium_term_memory':'event_id'}.get(t,'id')))
async def run(a):
 src=Path(a.db);dst=Path(a.v2_db or src.with_name('zero-memory-v2.db')); init_meta(dst); sc=open_source(src); tables={x[0] for x in sc.execute("select name from sqlite_master where type='table'")}; counts={t:(sc.execute(f'select count(*) from {t}').fetchone()[0] if t in tables else 0) for t in TABLES}; schema=h({t:[x[1:3] for x in sc.execute(f'pragma table_info({t})')] for t in TABLES if t in tables});rid=a.run_id or uuid.uuid4().hex; report={'run_id':rid,'tables_found':[t for t in TABLES if t in tables],'tables_missing':[t for t in TABLES if t not in tables],'rows_per_table':counts,'scanned':0,'would_import':0,'imported':0,'quarantined':0,'rejected':0,'archive_only':0}
 c=sqlite3.connect(dst);c.execute('insert or replace into memory_v2_migration_runs values(?,?,?,?,?,?,?,?,?,?)',(rid,h(str(src.resolve())),schema,int(time.time()),None,'running',int(not a.apply),'{}','2',None));c.commit();c.close(); svc=MemoryV2Service(str(dst))
 for t in TABLES:
  if t not in tables:continue
  for r in sc.execute(f'select * from {t}'):
   report['scanned']+=1
   if t=='memory_rag_documents':report['archive_only']+=1;continue
   try:item=mapper(t,r); assert item and item.content
   except Exception as e:
    report['quarantined']+=1;c=sqlite3.connect(dst);c.execute('insert into memory_v2_migration_quarantine values(?,?,?,?,?)',(rid,t,key(t,r),type(e).__name__,h(dict(r))));c.commit();c.close();continue
   if not svc.sanitize(item.content):report['rejected']+=1;continue
   report['would_import']+=1
   if a.apply:
    c=sqlite3.connect(dst); prior=c.execute('select target_id from memory_v2_migration_map where source_table=? and source_key=? and source_hash=? order by rowid limit 1',(t,key(t,r),h(dict(r)))).fetchone();c.close()
    if getattr(a,'fail_after',None) and report['imported']>=a.fail_after:
     c=sqlite3.connect(dst);c.execute("update memory_v2_migration_runs set status='interrupted',finished_at=?,report_json=? where run_id=?",(int(time.time()),json.dumps(report),rid));c.commit();c.close();sc.close();raise RuntimeError('fault_injected_migration_interrupt')
    try: target=prior[0] if prior else await svc.put(item)
    except BaseException:
     c=sqlite3.connect(dst);c.execute("update memory_v2_migration_runs set status='failed',finished_at=?,report_json=? where run_id=?",(int(time.time()),json.dumps(report),rid));c.commit();c.close();sc.close();raise
    c=sqlite3.connect(dst);c.execute('insert or ignore into memory_v2_migration_map values(?,?,?,?,?,?)',(rid,t,key(t,r),h(dict(r)),target,'reused' if prior else 'insert_or_merge'));c.commit();c.close();report['imported']+=0 if prior else 1
 c=sqlite3.connect(dst);c.execute('update memory_v2_migration_runs set finished_at=?,status=?,report_json=? where run_id=?',(int(time.time()),'applied' if a.apply else 'dry_run',json.dumps(report),rid));c.commit();c.close();sc.close();print(json.dumps(report,ensure_ascii=False))
def verify(db,rid):
 c=sqlite3.connect(db);run=c.execute('select status,rollback_status,report_json from memory_v2_migration_runs where run_id=?',(rid,)).fetchone();n=c.execute('select count(*) from memory_v2_migration_map where run_id=?',(rid,)).fetchone()[0];bad=c.execute("select count(*) from memory_v2_migration_map m left join memory_v2_items i on i.id=m.target_id where m.run_id=? and i.id is null",(rid,)).fetchone()[0];incomplete=not run or run[0] not in {'applied','dry_run'};print(json.dumps({'run_id':rid,'status':run[0] if run else 'missing','rollback_status':run[1] if run else None,'mapped':n,'missing_targets':bad,'incomplete':incomplete,'integrity':c.execute('pragma integrity_check').fetchone()[0],'foreign_keys':c.execute('pragma foreign_key_check').fetchall()}));c.close();return bad+int(incomplete)
def rollback(db,rid,fail_after=None):
 c=sqlite3.connect(db);ids=[r[0] for r in c.execute('select target_id from memory_v2_migration_map where run_id=?',(rid,))];changed=0
 for i in ids:
  if fail_after is not None and changed>=fail_after:
   c.execute("update memory_v2_migration_runs set rollback_status='interrupted' where run_id=?",(rid,));c.commit();c.close();raise RuntimeError('fault_injected_rollback_interrupt')
  other=c.execute('select count(*) from memory_v2_migration_map where target_id=? and run_id<>?',(i,rid)).fetchone()[0]
  if not other: changed+=c.execute("update memory_v2_items set status='deleted' where id=? and status<>'deleted'",(i,)).rowcount
  c.commit()
 c.execute("update memory_v2_migration_runs set rollback_status='done' where run_id=?",(rid,));c.commit();c.close();print(json.dumps({'run_id':rid,'soft_deleted':changed}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--v2-db');p.add_argument('--run-id');p.add_argument('--fail-after',type=int);p.add_argument('--rollback-fail-after',type=int);g=p.add_mutually_exclusive_group(required=True);g.add_argument('--dry-run',action='store_true');g.add_argument('--apply',action='store_true');g.add_argument('--verify',action='store_true');g.add_argument('--rollback',action='store_true');a=p.parse_args()
 if a.verify:sys.exit(verify(a.v2_db or Path(a.db).with_name('zero-memory-v2.db'),a.run_id or '')!=0)
 elif a.rollback:rollback(a.v2_db or Path(a.db).with_name('zero-memory-v2.db'),a.run_id or '',a.rollback_fail_after)
 else:asyncio.run(run(a))
