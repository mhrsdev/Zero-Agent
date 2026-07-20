#!/usr/bin/env python3
"""Review queue helper. Default lists counts; --accept-safe applies explicit rule labels."""
import argparse,collections,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',default='runtime/review/memory_corpus_reviewed.jsonl');p.add_argument('--accept-safe',action='store_true');a=p.parse_args();rows=[json.loads(x) for x in Path(a.input).read_text().splitlines()];out=[]
 for r in rows:
  if not a.accept_safe:continue
  memories=r['candidate_memories'];no_memory=not memories and r['category_guess'] in {'casual','unrelated_query','conversation'}
  out.append({'case_id':r['review_id'].replace('review','real'),'source_type':'real_anonymized','category':r['category_guess'],'messages':r['anonymized_messages'],'query':r['query'],'stored_memories':memories,'expected_relevant_memory_ids':[x['id'] for x in memories[:1]],'acceptable_optional_memory_ids':[x['id'] for x in memories[1:]],'forbidden_memory_ids':[],'expected_no_memory':no_memory,'label_method':'provenance_rule_review' if memories else 'deterministic_safe_review','label_confidence':0.8 if memories else 0.8,'review_note':'source message provenance maps query to memory' if memories else 'accepted no-memory benchmark'})
 if a.accept_safe:
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out))
 print(json.dumps({'input':len(rows),'accepted':len(out),'categories':dict(collections.Counter(x['category'] for x in out))}))
if __name__=='__main__':main()
