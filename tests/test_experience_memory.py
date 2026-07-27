import sqlite3
from zero.experience_memory import ExperienceMemory

def test_experience_requires_evidence_and_is_debug_only(tmp_path):
    m=ExperienceMemory(tmp_path/'x.db')
    try: m.candidate('web','bad','fix',[], 'x')
    except ValueError: pass
    else: raise AssertionError('evidence gate')
    cid=m.candidate('web search','numeric guard rejected','normalize digits',['trace:a','test:pass'],'guard fixture')
    assert m.retrieve('web',debug=False)==[]
    mid=m.approve(cid,reviewer_id=1)
    rows=m.retrieve('web',debug=True)
    assert rows and rows[0]['id']==mid and 'trace:a' in rows[0]['evidence']
