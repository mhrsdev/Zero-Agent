#!/usr/bin/env python3
"""Local-only, provenance-first anonymized corpus review queue builder."""
import argparse,collections,hashlib,json,re,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from zero.memory_v2.service import SECRET
DETECTORS={'phone':re.compile(r'(?<!\d)(?:\+?98|0)?9\d{9}(?!\d)'),'email':re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b'),'ip_address':re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),'telegram_handle':re.compile(r'(?<!\w)@[A-Za-z0-9_]{4,}'),'long_numeric_id':re.compile(r'\b\d{7,}\b'),'file_path':re.compile(r'(?:/home/|/root/|[A-Za-z]:\\)\S+'),'url':re.compile(r'https?://\S+'),'jwt':re.compile(r'\beyJ[\w-]+\.[\w-]+\.[\w-]+'),'database_url':re.compile(r'\b(?:postgres|mysql|mongodb)://\S+'),'private_key':re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),'password':re.compile(r'(?i)\bpassword\s*[:=]\s*\S+'),'api_key':re.compile(r'(?i)\bapi[_ -]?key\s*[:=]\s*\S+'),'telegram_token':re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{20,}\b')}
def pseudo(kind,value):return f'anon-{kind}-{hashlib.sha256(str(value).encode()).hexdigest()[:10]}'
def sanitize(text,stats):
 text=str(text or '').replace('\u200b','');hits=[]
 if SECRET.search(text):stats['possible_secret_low_confidence']+=1;return None,['possible_secret_low_confidence']
 for name,rx in DETECTORS.items():
  found=rx.findall(text)
  if found:stats[name]+=len(found);hits.append(name);text=rx.sub(f'[REDACTED_{name.upper()}]',text)
 return ' '.join(text.split())[:600],hits
def category(text,memories):
 t=text.casefold(); mt=' '.join(x.get('category','') for x in memories).casefold()
 if 'project' in mt or any(x in t for x in ('zero','زیرو','پروژه','deploy','باگ','خطا')):return 'project_continuation'
 if any(x in mt for x in ('preference','communication','identity')):return 'preference_recall'
 if any(x in t for x in ('سلام','خوبی','مرسی')):return 'casual'
 if '?' in t or '؟' in t:return 'unrelated_query'
 return 'memory_recall' if memories else 'conversation'
def memory_rows(c):
 out=[]
 for table,idcol,textcol,sourcecol,catcol in [('long_term_memory','memory_id','content','source_message_ids_json','category'),('medium_term_memory','event_id','summary','source_message_ids_json','topic'),('semantic_user_memory','id','value_json','evidence_message_ids_json','category')]:
  try:
   for r in c.execute(f"select {idcol} id,chat_id,{textcol} text,{sourcecol} sources,{catcol} category,status from {table} where status='active'"):
    for mid in json.loads(r['sources'] or '[]'):out.append((str(mid),dict(r)))
  except (sqlite3.DatabaseError,json.JSONDecodeError):continue
 return collections.defaultdict(list, {k:[v for kk,v in out if kk==k] for k,_ in out})
def main():
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--dry-run',action='store_true');p.add_argument('--apply',action='store_true');p.add_argument('--limit',type=int,default=30);a=p.parse_args();stats=collections.Counter();src=Path(a.source);events=[]
 if src.suffix=='.db':
  c=sqlite3.connect(src);c.row_factory=sqlite3.Row;by_message=memory_rows(c)
  for r in c.execute("select id,chat_id,sender_id,text,telegram_message_id,created_at from recent_messages where role='user' order by id desc limit 5000"):
   clean,hits=sanitize(r['text'],stats)
   if not clean or not 8<=len(clean)<=600 or hits:continue
   mems=by_message.get(str(r['telegram_message_id']),[]);safe=[]
   for m in mems:
    body,mhits=sanitize(m['text'],stats)
    if body and not mhits:safe.append({'id':pseudo('memory',m['id']),'content':body,'scope':'group_user','category':m['category'],'provenance':'source_message_match'})
   events.append((r,clean,safe))
  c.close()
 else:
  for i,line in enumerate(src.read_text(errors='replace').splitlines()):
   clean,hits=sanitize(line,stats)
   if clean and not hits and ('RECEIVED' in clean or 'MEMORY_' in clean):events.append(({'id':i,'chat_id':0,'sender_id':0,'created_at':0},clean,[]))
 pos=[x for x in events if x[2]];neg=[x for x in events if not x[2]];selected=[]
 # ponytail: round-robin buckets; upgrade to weighted sampler only when corpus grows beyond review scale.
 for pool,target in ((pos,max(0,a.limit-10)),(neg,10)):
  buckets=collections.defaultdict(list)
  for e in pool:buckets[category(e[1],e[2])].append(e)
  while len(selected)<target if pool is pos else len(selected)<a.limit and any(buckets.values()):
   for k in sorted(buckets):
    if buckets[k] and (len(selected)<target if pool is pos else len(selected)<a.limit):selected.append(buckets[k].pop())
   if not any(buckets.values()):break
 # fill from remaining positive when no negatives/categories exist
 for e in pos:
  if len(selected)>=a.limit:break
  if e not in selected:selected.append(e)
 queue=[]
 for n,(r,clean,mems) in enumerate(selected[:a.limit],1):
  queue.append({'review_id':f'review-{n:03d}','source_type':'real_anonymized','category_guess':category(clean,mems),'anonymized_messages':[{'message_id':f'anon-msg-{n}','sender_id':pseudo('user',r['sender_id']),'chat_id':pseudo('chat',r['chat_id']),'text':clean,'time_bucket':'historical'}],'candidate_memories':mems,'query':clean,'detectors_triggered':[],'safe_for_review':True,'recommended_action':'accept' if mems else 'needs_review'})
 report={'raw_candidates':len(events),'positive_provenance_candidates':len(pos),'eligible':len(queue),'category_distribution':dict(collections.Counter(category(x[1],x[2]) for x in selected)),'detectors':dict(stats),'output':str(a.output)};print(json.dumps(report,ensure_ascii=False))
 if a.apply:Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in queue))
if __name__=='__main__':main()
