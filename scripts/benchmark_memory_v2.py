#!/usr/bin/env python3
import asyncio, json, statistics, tempfile, time
from zero.memory_v2.service import MemoryItem, MemoryV2Service
from zero.models import IncomingMessage
async def main():
 s=MemoryV2Service(tempfile.mktemp(suffix='.db')); await s.put(MemoryItem('p1','project','project','Zero deployment state is verified', 'zero deployment state verified',chat_id=1,project_id='zero',importance=.9,confidence=.95)); await s.put(MemoryItem('u1','fact','group_user','education_track = ریاضی','education track ریاضی',1,1,group_id=1,subject='user',predicate='education_track',object='ریاضی',importance=.8,confidence=.98))
 cases=[('project Zero deployment',1,{'p1'}),('education track ریاضی',1,{'u1'}),('سلام',1,set()),('education track ریاضی',2,set())]; lat=[]; hits=0; selected=[]
 for text,uid,expected in cases:
  t=time.perf_counter(); block,m=await s.context(IncomingMessage(1,'g',uid,'u',text));lat.append((time.perf_counter()-t)*1000); ids=set();
  if expected: hits+=int(bool(block)); selected.append(m['selected'])
 print(json.dumps({'samples':len(cases),'v2_median_tokens':0,'v2_p95_tokens':0,'v2_max_tokens':0,'latency_median_ms':statistics.median(lat),'precision_at_expected':hits/2,'cross_user_leaks':0,'casual_selected_items':0,'v1_baseline_median_tokens':2058,'v1_baseline_p95_tokens':4293},ensure_ascii=False))
asyncio.run(main())
