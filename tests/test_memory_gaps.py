import json
import sqlite3
import pytest
from zero.experience_memory import ExperienceMemory
from zero.semantic_memory import SemanticUserMemory


def make_exp(db, **kw):
    m = ExperienceMemory(db)
    cid = m.candidate('thread bug', kw.get('root_cause','thread_id mismatch'), kw.get('fix','pass thread_id'), kw.get('evidence',[{'test_name':'test_thread','status':'passed'}]), 'src', confidence=kw.get('confidence', .9), outcome=kw.get('outcome','fixed'), regression_tests=kw.get('regression_tests',[{'regression_test':'test_thread'}]))
    mid = m.approve(cid, 99)
    return m, mid

@pytest.mark.parametrize('field,reason', [('root_cause','missing_root_cause'),('fix','missing_final_fix'),('outcome','missing_outcome')])
def test_experience_verify_required_fields(tmp_path, field, reason):
    m, mid = make_exp(tmp_path/'e.db')
    with m._c() as c: c.execute(f'UPDATE experience_memory SET {"fix" if field=="fix" else field}="" WHERE id=?',(mid,)); c.commit()
    result = m.verify(mid, 7)
    assert result == {'verified':False, 'reason':reason, 'trace_id':result['trace_id']}
    with m._c() as c: assert c.execute('select status from experience_memory where id=?',(mid,)).fetchone()[0] == 'active'

def test_experience_evidence_and_confidence_gates(tmp_path):
    m, mid = make_exp(tmp_path/'e.db')
    with m._c() as c: c.execute('update experience_memory set evidence_json=? where id=?',(json.dumps(['تست شد']),mid)); c.commit()
    assert m.verify(mid, 7)['reason'] == 'missing_evidence'
    with m._c() as c: c.execute('update experience_memory set evidence_json=?,confidence=? where id=?',(json.dumps([{'test_name':'x','status':'passed'}]),.6,mid)); c.commit()
    assert m.verify(mid, 7)['reason'] == 'confidence_too_low'

def test_experience_regression_invalidation_and_audit(tmp_path):
    m, mid = make_exp(tmp_path/'e.db')
    assert m.verify(mid, 7)['verified']
    assert m.retrieve('thread', debug=True)
    assert m.invalidate(mid, 7, 'regression')['invalidated']
    assert m.retrieve('thread', debug=True) == []
    with m._c() as c:
        events=[r[0] for r in c.execute('select event_type from experience_memory_audit where experience_id=? order by id',(mid,))]
    assert events == ['EXPERIENCE_VERIFICATION_REQUESTED','EXPERIENCE_VERIFIED','EXPERIENCE_INVALIDATED']

def make_sem(db, chat, sender):
    m=SemanticUserMemory(db); cid=m.candidate(chat_id=chat,sender_id=sender,category='identity',key='preferred_name',value='A',confidence=.9,evidence_message_ids=[1],source_text='x'); return m,m.approve(cid,sender)

def test_semantic_self_scope_and_owner_override_audit(tmp_path):
    m, mid=make_sem(tmp_path/'s.db',10,20)
    assert m.inspect_for_actor(mid,chat_id=10,sender_id=20,actor_id=20)['id']==mid
    with pytest.raises(PermissionError): m.inspect_for_actor(mid,chat_id=10,sender_id=21,actor_id=21)
    with pytest.raises(PermissionError): m.correct_for_actor(mid,'B',chat_id=10,sender_id=21,actor_id=21)
    assert m.inspect_for_actor(mid,chat_id=999,sender_id=999,actor_id=1,owner_id=1)['id']==mid
    with m._conn() as c:
        events=[r[0] for r in c.execute('select event_type from semantic_memory_audit where item_id=? order by id',(mid,))]
    assert 'SEMANTIC_MEMORY_ACCESS_DENIED' in events and 'SEMANTIC_MEMORY_OWNER_OVERRIDE' in events

def test_semantic_same_sender_different_chat_isolated(tmp_path):
    m,a=make_sem(tmp_path/'s.db',10,20); _,b=make_sem(tmp_path/'s.db',11,20)
    assert {x['chat_id'] for x in m.retrieve(10,20)} == {10}
    assert {x['chat_id'] for x in m.retrieve(11,20)} == {11}
