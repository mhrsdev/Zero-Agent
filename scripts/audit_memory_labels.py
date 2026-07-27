#!/usr/bin/env python3
"""Blind label audit: never reads V1/V2 output, score, or threshold."""
import json,re
from pathlib import Path
TOKEN=re.compile(r"[\w\u0600-\u06ff]+")
def main():
 rows=[json.loads(x) for x in Path('tests/fixtures/memory_v2/real_anonymized_corpus.jsonl').read_text().splitlines()];out=[]
 for r in rows:
  for m in r.get('stored_memories',[]):
   q=set(TOKEN.findall(r['query'].casefold()));k=set(TOKEN.findall(m['content'].casefold()));overlap=len(q&k)
   # Provenance alone is never semantic ground truth; label uncertainty explicitly.
   out.append({'case_id':r['case_id'],'query':r['query'],'candidate_memory':m['content'],'source_provenance':m.get('provenance'),'lexical_relationship':{'shared_terms':overlap},'semantic_relationship':'unreviewed','answer_necessity':'unreviewed','label_validity':'provenance_only','recommended_label':'AMBIGUOUS','confidence':0.0})
 Path('runtime/review').mkdir(parents=True,exist_ok=True);Path('runtime/review/memory_semantic_label_audit.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out));print(json.dumps({'positive_candidates':len(out),'required':0,'optional':0,'irrelevant':0,'ambiguous':len(out),'invalid_case':0,'status':'SEMANTIC REVIEW REQUIRED'}))
if __name__=='__main__':main()
