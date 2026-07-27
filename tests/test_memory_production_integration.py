from zero.experience_memory import ExperienceMemory
from zero.procedural_memory import ProceduralMemory
from zero.world_model import WorldModel

def test_experience_natural_queries_and_weak_match(tmp_path):
 e=ExperienceMemory(tmp_path/'x.db'); cid=e.candidate('IncomingMessage thread_id','IncomingMessage فاقد thread_id','افزودن thread_id به مدل',['pytest:1'],'thread_id',.9); e.approve(cid,1)
 assert e.retrieve('قبلاً باگ thread_id داشتیم؟',True,3)
 assert e.retrieve('چیز کاملاً بی‌ربط',True,3)==[]
 assert e.retrieve('thread_id',False)==[]

def test_procedure_panel_lifecycle_and_matching(tmp_path):
 p=ProceduralMemory(tmp_path/'p.db'); cid=p.candidate('Debug Workflow',['compileall','focused tests','restart'],['pytest:1']); assert p.retrieve('debug workflow') is None; pid=p.approve(cid,9); dup=p.candidate('Debug Workflow',['compileall','focused tests','restart'],['pytest:2']); assert p.approve(dup,9)==pid; changed=p.candidate('Debug Workflow',['compileall','focused tests','restart','logs'],['pytest:3']); new_pid=p.approve(changed,9); assert new_pid != pid; assert p.retrieve('برای دیباگ چه روالی داریم؟')['id']==new_pid; p.deprecate(new_pid,9); assert p.retrieve('debug workflow') is None

def test_world_natural_query_relation(tmp_path):
 w=WorldModel(tmp_path/'w.db'); z=w.entity('Zero','system'); t=w.entity('Telegram Search','component'); l=w.entity('Telethon','library'); w.relation(z,'has_component',t,['arch:1']); w.relation(t,'uses_library',l,['code:1']); out=w.resolve_query('زیرو برای سرچ تلگرام از چی استفاده می‌کنه؟'); assert out and any(r['predicate']=='has_component' for r in out['relations'])
