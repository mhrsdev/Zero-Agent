#!/usr/bin/env python3
import asyncio,json,statistics,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from zero.memory_v2.service import MemoryItem,MemoryV2Service
from zero.models import IncomingMessage

def percentile(xs,p): return sorted(xs)[max(0,min(len(xs)-1,round((len(xs)-1)*p)))] if xs else 0
async def main():
 rows=[json.loads(x) for x in Path('tests/fixtures/memory_v2/regression_corpus.jsonl').read_text().splitlines()]; s=MemoryV2Service('/tmp/zero-memory-v2-labelled.db');
 # isolated fresh run
 import os
 try: os.unlink('/tmp/zero-memory-v2-labelled.db')
 except FileNotFoundError: pass
 s=MemoryV2Service('/tmp/zero-memory-v2-labelled.db'); out=[]
 for n,r in enumerate(rows):
  try: os.unlink('/tmp/zero-memory-v2-labelled.db')
  except FileNotFoundError: pass
  s=MemoryV2Service('/tmp/zero-memory-v2-labelled.db')
  uid=n%3+1;chat=n%2+1
  for x in r['stored_memories']:
   await s.put(MemoryItem(x['id'],'fact','group_user',x['content'],x['content'].casefold(),uid,chat,group_id=chat,importance=1,confidence=1))
  m=IncomingMessage(chat,'g',uid,'u',r['query'],message_id=n+1); t=time.perf_counter();_,meta=await s.context(m);lat=(time.perf_counter()-t)*1000
  got=set(meta.get('ids',[]));exp=set(r['expected_relevant_memory_ids']);forbid=set(r['forbidden_memory_ids']);tp=len(got&exp);fp=len(got-exp);fn=len(exp-got);out.append({'case_id':r['case_id'],'retrieved_ids':sorted(got),'expected_ids':sorted(exp),'forbidden_ids':sorted(forbid),'true_positive':tp,'false_positive':fp,'false_negative':fn,'precision':tp/(tp+fp) if tp+fp else (1 if not exp else 0),'recall':tp/(tp+fn) if tp+fn else 1,'forbidden_hit':bool(got&forbid),'selected_items':meta['selected'],'selected_tokens':meta['tokens'],'retrieval_latency_ms':lat})
 tp=sum(x['true_positive'] for x in out);fp=sum(x['false_positive'] for x in out);fn=sum(x['false_negative'] for x in out);p=tp/(tp+fp) if tp+fp else 1;rc=tp/(tp+fn) if tp+fn else 1
 report={'samples':len(out),'micro_precision':p,'micro_recall':rc,'micro_f1':2*p*rc/(p+rc) if p+rc else 0,'macro_precision':sum(x['precision'] for x in out)/len(out),'macro_recall':sum(x['recall'] for x in out)/len(out),'forbidden_retrieval_rate':sum(x['forbidden_hit'] for x in out)/len(out),'median_tokens':statistics.median(x['selected_tokens'] for x in out),'p95_tokens':percentile([x['selected_tokens'] for x in out],.95),'median_latency_ms':statistics.median(x['retrieval_latency_ms'] for x in out),'p95_latency_ms':percentile([x['retrieval_latency_ms'] for x in out],.95),'cases':out}
 print(json.dumps(report,ensure_ascii=False))
asyncio.run(main())
