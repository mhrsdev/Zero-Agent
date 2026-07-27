from zero.procedural_memory import ProceduralMemory

def test_procedural_requires_approval_and_tracks_runs(tmp_path):
 m=ProceduralMemory(tmp_path/'p.db'); cid=m.candidate('debug workflow',['inspect','test'],['trace:x'],risk_level='normal')
 assert m.retrieve('debug workflow') is None
 pid=m.approve(cid,reviewer_id=7); p=m.retrieve('debug workflow'); assert p['id']==pid and p['approved_by']==7
 m.record(pid,'trace-1','success'); m.record(pid,'trace-2','failure','TimeoutError'); p=m.retrieve('debug workflow'); assert p['success_count']==1 and p['failure_count']==1
