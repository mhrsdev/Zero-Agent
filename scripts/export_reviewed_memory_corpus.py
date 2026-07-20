#!/usr/bin/env python3
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from build_anonymized_memory_corpus import DETECTORS
from zero.memory_v2.service import SECRET
def main():
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args();rows=[json.loads(x) for x in Path(a.input).read_text().splitlines()];seen=set()
 for r in rows:
  if r['case_id'] in seen:raise SystemExit('duplicate case id')
  seen.add(r['case_id']);blob=json.dumps(r,ensure_ascii=False)
  if SECRET.search(blob) or any(rx.search(blob) for rx in DETECTORS.values()):raise SystemExit('privacy scan failed')
  if set(r['expected_relevant_memory_ids'])&set(r['forbidden_memory_ids']):raise SystemExit('label overlap')
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows));print(json.dumps({'exported':len(rows),'privacy_scan':'pass'}))
if __name__=='__main__':main()
