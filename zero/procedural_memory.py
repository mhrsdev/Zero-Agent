from __future__ import annotations
import json, sqlite3, time, re
from pathlib import Path
SCHEMA='''
CREATE TABLE IF NOT EXISTS procedural_memory_candidates(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,steps_json TEXT NOT NULL,risk_level TEXT NOT NULL,evidence_json TEXT NOT NULL,created_at INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS procedural_memory(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,steps_json TEXT NOT NULL,risk_level TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'active',approved_by INTEGER,approved_at INTEGER,success_count INTEGER NOT NULL DEFAULT 0,failure_count INTEGER NOT NULL DEFAULT 0,UNIQUE(name,version));
CREATE TABLE IF NOT EXISTS procedural_memory_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,procedure_id INTEGER NOT NULL,trace_id TEXT NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL,error_type TEXT);
CREATE INDEX IF NOT EXISTS idx_procedural_active ON procedural_memory(status,name);
'''
def migrate_procedural_memory(db_path:str|Path):
 with sqlite3.connect(db_path,timeout=5) as c:c.execute('PRAGMA busy_timeout=5000');c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON');c.executescript(SCHEMA);c.commit()
class ProceduralMemory:
 def __init__(self,db_path):self.db_path=Path(db_path);self.db_path.parent.mkdir(parents=True,exist_ok=True);migrate_procedural_memory(self.db_path)
 def _c(self):
  c=sqlite3.connect(self.db_path,timeout=5);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=5000');c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON');return c
 def candidate(self,name,steps,evidence,risk_level='normal'):
  if not steps or not evidence or risk_level not in {'low','normal','high'}:raise ValueError('invalid_procedure_candidate')
  with self._c() as c:
   x=c.execute('INSERT INTO procedural_memory_candidates(name,steps_json,risk_level,evidence_json,created_at) VALUES(?,?,?,?,?)',(name,json.dumps(list(steps)),risk_level,json.dumps(list(evidence)),int(time.time())));c.commit();return x.lastrowid
 def approve(self,candidate_id,reviewer_id):
  with self._c() as c:
   row=c.execute("SELECT * FROM procedural_memory_candidates WHERE id=? AND status='pending'",(candidate_id,)).fetchone()
   if not row:raise ValueError('candidate_not_found')
   active=c.execute("SELECT * FROM procedural_memory WHERE name=? AND status='active' ORDER BY version DESC LIMIT 1",(row['name'],)).fetchone()
   if active and active['steps_json'] == row['steps_json'] and active['risk_level'] == row['risk_level']:
    c.execute("UPDATE procedural_memory_candidates SET status='approved' WHERE id=?",(candidate_id,));c.commit();return int(active['id'])
   c.execute("UPDATE procedural_memory SET status='deprecated' WHERE name=? AND status='active'",(row['name'],))
   v=(c.execute('SELECT COALESCE(MAX(version),0) v FROM procedural_memory WHERE name=?',(row['name'],)).fetchone()['v'] or 0)+1
   x=c.execute('INSERT INTO procedural_memory(name,steps_json,risk_level,version,approved_by,approved_at) VALUES(?,?,?,?,?,?)',(row['name'],row['steps_json'],row['risk_level'],v,reviewer_id,int(time.time())));c.execute("UPDATE procedural_memory_candidates SET status='approved' WHERE id=?",(candidate_id,));c.commit();return x.lastrowid
 def reject(self,candidate_id:int,reviewer_id:int):
  with self._c() as c:c.execute("UPDATE procedural_memory_candidates SET status='rejected' WHERE id=? AND status='pending'",(candidate_id,));c.commit()
 def deprecate(self,procedure_id:int,reviewer_id:int):
  with self._c() as c:c.execute("UPDATE procedural_memory SET status='deprecated' WHERE id=? AND status='active'",(procedure_id,));c.commit()
 def inspect(self,procedure_id:int):
  with self._c() as c:
   r=c.execute('SELECT * FROM procedural_memory WHERE id=?',(procedure_id,)).fetchone(); return dict(r)|{'steps':json.loads(r['steps_json'])} if r else None
 def retrieve(self,name:str):
  q=set(re.findall(r'[a-z0-9_]{3,}|[آ-ی]{3,}',(name or '').casefold()))
  if any(x in q for x in ('دیباگ','debug','خطا','باگ')): q.update({'debug','workflow'})
  with self._c() as c: rows=c.execute("SELECT * FROM procedural_memory WHERE status='active' ORDER BY version DESC").fetchall()
  ranked=[]
  for r in rows:
   hay=' '.join((r['name'],r['steps_json'],r['risk_level'])).casefold(); score=len(q & set(re.findall(r'[a-z0-9_]{3,}|[آ-ی]{3,}',hay)))
   if score: ranked.append((score,r))
  if not ranked:return None
  r=max(ranked,key=lambda x:x[0])[1]; return dict(r)|{'steps':json.loads(r['steps_json'])}
 def record(self,procedure_id,trace_id,status,error_type=None):
  if status not in {'success','failure'}:raise ValueError('invalid_run_status')
  with self._c() as c:
   c.execute('INSERT INTO procedural_memory_runs(procedure_id,trace_id,status,created_at,error_type) VALUES(?,?,?,?,?)',(procedure_id,trace_id,status,int(time.time()),error_type));c.execute(f'UPDATE procedural_memory SET {"success_count" if status=="success" else "failure_count"}={"success_count" if status=="success" else "failure_count"}+1 WHERE id=?',(procedure_id,));c.commit()
