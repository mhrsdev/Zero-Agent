from __future__ import annotations
from .sqlite_tx import sqlite_txn
import json,re,sqlite3,time
from pathlib import Path
SCHEMA='''
CREATE TABLE IF NOT EXISTS world_entities(id INTEGER PRIMARY KEY AUTOINCREMENT,canonical_name TEXT NOT NULL UNIQUE,entity_type TEXT NOT NULL,properties_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'active',version INTEGER NOT NULL DEFAULT 1,valid_until INTEGER);
CREATE TABLE IF NOT EXISTS world_aliases(alias TEXT PRIMARY KEY,entity_id INTEGER NOT NULL,confidence REAL NOT NULL DEFAULT 1.0,evidence_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS world_relations(id INTEGER PRIMARY KEY AUTOINCREMENT,subject_entity_id INTEGER NOT NULL,predicate TEXT NOT NULL,object_entity_id INTEGER NOT NULL,evidence_json TEXT NOT NULL,confidence REAL NOT NULL,valid_until INTEGER,status TEXT NOT NULL DEFAULT 'active',version INTEGER NOT NULL DEFAULT 1,UNIQUE(subject_entity_id,predicate,object_entity_id,version));
CREATE INDEX IF NOT EXISTS idx_world_relation_subject ON world_relations(subject_entity_id,status);
'''
PERSONAL=re.compile(r'password|token|api[_ -]?key|secret|private key|رمز|توکن|کلید خصوصی',re.I)
def migrate_world_model(db_path:str|Path):
 # sqlite_txn closes the handle; a bare `with sqlite3.connect(...)` only commits.
 with sqlite_txn(sqlite3.connect(db_path,timeout=5)) as c:c.execute('PRAGMA busy_timeout=5000');c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON');c.executescript(SCHEMA);c.commit()
class WorldModel:
 def __init__(self,db_path):self.db_path=Path(db_path);self.db_path.parent.mkdir(parents=True,exist_ok=True);migrate_world_model(self.db_path)
 def _c(self):
  c=sqlite3.connect(self.db_path,timeout=5);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=5000');c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON');return c
 def entity(self,name,entity_type,properties=None):
  if PERSONAL.search(name) or PERSONAL.search(json.dumps(properties or {})):raise ValueError('personal_data_rejected')
  with sqlite_txn(self._c()) as c:
   c.execute('INSERT OR IGNORE INTO world_entities(canonical_name,entity_type,properties_json) VALUES(?,?,?)',(name,entity_type,json.dumps(properties or {},ensure_ascii=False)));c.commit();return c.execute('SELECT id FROM world_entities WHERE canonical_name=?',(name,)).fetchone()['id']
 def alias(self,alias,entity_id,evidence,confidence=1.0):
  if not evidence or confidence<.6:raise ValueError('alias_evidence_required')
  with sqlite_txn(self._c()) as c:
   old=c.execute('SELECT entity_id FROM world_aliases WHERE alias=?',(alias,)).fetchone()
   if old and old['entity_id']!=entity_id: raise ValueError('ambiguous_alias')
   c.execute('INSERT OR REPLACE INTO world_aliases(alias,entity_id,confidence,evidence_json) VALUES(?,?,?,?)',(alias,entity_id,confidence,json.dumps(list(evidence))));c.commit()
 def relation(self,subject_id,predicate,object_id,evidence,confidence=.8):
  if not evidence or confidence<.6:raise ValueError('relation_evidence_required')
  with sqlite_txn(self._c()) as c:
   x=c.execute('INSERT INTO world_relations(subject_entity_id,predicate,object_entity_id,evidence_json,confidence,version) VALUES(?,?,?,?,?,1)',(subject_id,predicate,object_id,json.dumps(list(evidence)),confidence));c.commit();return x.lastrowid
 def resolve_query(self,query:str):
  q=(query or '').casefold().replace('زیرو','zero'); predicate=None
  for phrase,pred in {'از چی استفاده':'uses_library','جزو کدام':'has_component','چه ارتباط':'related','ارائه‌دهنده':'provided_by','مال کدام شرکت':'developed_by'}.items():
   if phrase in q: predicate=pred; break
  with sqlite_txn(self._c()) as c: entities=c.execute("SELECT * FROM world_entities WHERE status='active'").fetchall(); aliases=c.execute('SELECT * FROM world_aliases').fetchall()
  best=[]
  for e in entities:
   names=[e['canonical_name']]+[a['alias'] for a in aliases if a['entity_id']==e['id']]
   score=max((3 if n.casefold() in q else len(set(n.casefold().split()) & set(q.split())) for n in names),default=0)
   if score: best.append((score,e))
  if not best:return None
  e=max(best,key=lambda x:x[0])[1]
  with sqlite_txn(self._c()) as c:
   sql="SELECT r.*,s.canonical_name subject_name,o.canonical_name object_name FROM world_relations r JOIN world_entities s ON s.id=r.subject_entity_id JOIN world_entities o ON o.id=r.object_entity_id WHERE r.status='active' AND (r.subject_entity_id=? OR r.object_entity_id=?)"
   args=[e['id'],e['id']]
   if predicate: sql+=' AND r.predicate=?';args.append(predicate)
   rs=c.execute(sql+' LIMIT 5',args).fetchall()
   if not rs and predicate=='uses_library':
    rs=c.execute("SELECT r.*,s.canonical_name subject_name,o.canonical_name object_name FROM world_relations r JOIN world_entities s ON s.id=r.subject_entity_id JOIN world_entities o ON o.id=r.object_entity_id WHERE r.status='active' AND r.predicate='has_component' AND r.subject_entity_id=? LIMIT 5",(e['id'],)).fetchall()
    for first in rs:
     child=c.execute("SELECT r.*,s.canonical_name subject_name,o.canonical_name object_name FROM world_relations r JOIN world_entities s ON s.id=r.subject_entity_id JOIN world_entities o ON o.id=r.object_entity_id WHERE r.status='active' AND r.predicate='uses_library' AND r.subject_entity_id=? LIMIT 5",(first['object_entity_id'],)).fetchall()
     rs=list(rs)+list(child)
  return {'entity':dict(e),'relations':[dict(r)|{'evidence':json.loads(r['evidence_json'])} for r in rs[:5]]}

 def retrieve(self,name):
  with sqlite_txn(self._c()) as c:
   e=c.execute('SELECT * FROM world_entities WHERE canonical_name=? OR id=(SELECT entity_id FROM world_aliases WHERE alias=?)',(name,name)).fetchone()
   if not e:return None
   rs=c.execute("SELECT r.*,s.canonical_name subject_name,o.canonical_name object_name FROM world_relations r JOIN world_entities s ON s.id=r.subject_entity_id JOIN world_entities o ON o.id=r.object_entity_id WHERE r.subject_entity_id=? AND r.status='active'",(e['id'],)).fetchall()
   return {'entity':dict(e),'relations':[dict(r)|{'evidence':json.loads(r['evidence_json'])} for r in rs]}
