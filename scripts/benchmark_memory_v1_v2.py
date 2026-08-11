#!/usr/bin/env python3
import asyncio,json,os,statistics,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from zero.memory_context import compose_memory_context
from zero.memory_v2.service import MemoryItem,MemoryV2Service
from zero.models import IncomingMessage
from zero.semantic_memory import SemanticUserMemory
from zero.storage import ZeroStore
def pct(x,p):return sorted(x)[min(len(x)-1,round((len(x)-1)*p))] if x else 0
def score(cases,mode):
 tp=fp=fn=forbid=no_ok=0;lat=[];tokens=[];items=[];diff=[]
 async def one(r,n):
  nonlocal tp,fp,fn,forbid,no_ok
  chat=n%2+1;uid=n%3+1;m=IncomingMessage(chat,'g',uid,'u',r['query'],message_id=n+1);expected=set(r.get('expected_relevant_memory_ids',[]));optional=set(r.get('acceptable_optional_memory_ids',[]));forbidden=set(r.get('forbidden_memory_ids',[]));start=time.perf_counter()
  with tempfile.TemporaryDirectory() as td:
   if mode=='v2':
    s=MemoryV2Service(str(Path(td)/'v2.db'))
    for x in r.get('stored_memories',[]):await s.put(MemoryItem(x['id'],'episode',x.get('scope','group_user'),x['content'],x['content'].casefold(),uid,chat,group_id=chat,subject=x['id'],predicate='benchmark',topics=(x.get('category','event'),),importance=1,confidence=1))
    block,meta=await s.context(m);got=set(meta.get('ids',[]));tok=meta['tokens']
   else:
    st=ZeroStore(str(Path(td)/'v1.db'));sem=SemanticUserMemory(st.db_path)
    for x in r.get('stored_memories',[]):await st.add_long_memory(chat,'benchmark',x['content'],created_by=uid,subject_user_id=uid,confidence=1)
    layered=await st.retrieve_layered_memory(chat,r['query'],sender_id=uid,short_limit=1,medium_limit=4,long_limit=20);block,_=await compose_memory_context(store=st,semantic_memory=sem,message=m,recent=[],layered=layered);got={x['id'] for x in r.get('stored_memories',[]) if x['content'] in block};tok=len(block)//4
  lat.append((time.perf_counter()-start)*1000);tokens.append(tok);items.append(len(got));tp+=len(got&expected);fp+=len(got-expected-optional);fn+=len(expected-got);forbid+=bool(got&forbidden);no_ok+=bool(r.get('expected_no_memory') or not expected) and not got
 for n,r in enumerate(cases):asyncio.run(one(r,n))
 p=tp/(tp+fp) if tp+fp else 1;rec=tp/(tp+fn) if tp+fn else 1
 return {'precision':p,'recall':rec,'f1':2*p*rec/(p+rec) if p+rec else 0,'forbidden_hit_rate':forbid/len(cases),'no_memory_accuracy':no_ok/sum(bool(x.get('expected_no_memory') or not x.get('expected_relevant_memory_ids')) for x in cases),'median_items':statistics.median(items),'p95_items':pct(items,.95),'median_tokens':statistics.median(tokens),'p95_tokens':pct(tokens,.95),'median_latency_ms':statistics.median(lat),'p95_latency_ms':pct(lat,.95)}
def main():
 synthetic_path=Path('tests/fixtures/memory_v2/regression_corpus.jsonl')
 cases=[json.loads(x) for x in synthetic_path.read_text().splitlines()]
 v1=score(cases,'v1');v2=score(cases,'v2')
 gates={
  'precision_at_least_0_95':v2['precision']>=.95,
  'recall_at_least_0_95':v2['recall']>=.95,
  'forbidden_hit_rate_zero':v2['forbidden_hit_rate']==0,
  'no_memory_accuracy_one':v2['no_memory_accuracy']==1,
 }
 gates['passed']=all(gates.values())
 print(json.dumps({'corpus':'synthetic','corpus_kind':'synthetic','cases':len(cases),'preliminary':False,'v1':v1,'v2':v2,'gates':gates},ensure_ascii=False))
 real_path=Path(os.getenv('ZERO_REAL_MEMORY_CORPUS','tests/fixtures/memory_v2/real_anonymized_corpus.jsonl'))
 if not real_path.is_file():
  print(json.dumps({'corpus':'real','status':'BLOCKED','reason':'real_anonymized_corpus_missing','semantic_review':'REQUIRED'},ensure_ascii=False))
  return 0 if gates['passed'] else 1
 real_cases=[json.loads(x) for x in real_path.read_text().splitlines()]
 dev=[r for i,r in enumerate(real_cases) if i%3];holdout=[r for i,r in enumerate(real_cases) if not i%3]
 for split,part in [('development',dev),('holdout',holdout)]:
  print(json.dumps({'corpus':'real','split':split,'cases':len(part),'preliminary':len(part)<30,'v1':score(part,'v1'),'v2':score(part,'v2')},ensure_ascii=False))
 return 0 if gates['passed'] else 1
if __name__=='__main__': raise SystemExit(main())
