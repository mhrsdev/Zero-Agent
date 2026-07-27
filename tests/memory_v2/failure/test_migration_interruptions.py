import sqlite3,subprocess,sys
from zero.storage import ZeroStore

def cmd(*args): return subprocess.run([sys.executable,'scripts/migrate_memory_v1_to_v2.py',*args],cwd='.',capture_output=True,text=True)
def source(tmp_path):
 p=tmp_path/'v1.db';ZeroStore(str(p))
 with sqlite3.connect(p) as c:
  c.execute("insert into long_term_memory values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('m1',1,1,'project','zero deploy',.9,'[]',1,1,1,None,'active',1,1,'normal'))
  c.execute("insert into long_term_memory values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('m2',1,1,'preference','concise reply',.9,'[]',2,2,2,None,'active',1,1,'normal'))
 return p

def test_interrupted_apply_is_detected_and_rollback_recoverable(tmp_path):
 src=source(tmp_path);dst=tmp_path/'v2.db';r=cmd('--db',str(src),'--v2-db',str(dst),'--apply','--run-id','r1','--fail-after','1');assert r.returncode!=0
 v=cmd('--db',str(src),'--v2-db',str(dst),'--verify','--run-id','r1');assert v.returncode!=0 and 'incomplete' in v.stdout
 rb=cmd('--db',str(src),'--v2-db',str(dst),'--rollback','--run-id','r1');assert rb.returncode==0
 clean=cmd('--db',str(src),'--v2-db',str(dst),'--apply','--run-id','r2');assert clean.returncode==0
 assert cmd('--db',str(src),'--v2-db',str(dst),'--verify','--run-id','r2').returncode==0

def test_interrupted_rollback_resumes_and_is_idempotent(tmp_path):
 src=source(tmp_path);dst=tmp_path/'v2.db';assert cmd('--db',str(src),'--v2-db',str(dst),'--apply','--run-id','r1').returncode==0
 r=cmd('--db',str(src),'--v2-db',str(dst),'--rollback','--run-id','r1','--rollback-fail-after','0');assert r.returncode!=0
 with sqlite3.connect(dst) as c: assert c.execute("select rollback_status from memory_v2_migration_runs where run_id='r1'").fetchone()[0]=='interrupted'
 assert cmd('--db',str(src),'--v2-db',str(dst),'--rollback','--run-id','r1').returncode==0
 assert cmd('--db',str(src),'--v2-db',str(dst),'--rollback','--run-id','r1').returncode==0
