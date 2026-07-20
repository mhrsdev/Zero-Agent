import sqlite3

import pytest

from zero.semantic_memory import SemanticUserMemory, migrate_semantic_user_memory


def test_semantic_memory_candidate_approval_scope_correction_forget_and_ttl(tmp_path):
    db = tmp_path / 'semantic.db'
    migrate_semantic_user_memory(db)
    mem = SemanticUserMemory(db)
    cid = mem.candidate(chat_id=1, sender_id=10, category='identity', key='preferred_name', value='محمد', confidence=0.9, evidence_message_ids=[7], source_text='اسم من محمده')
    assert mem.retrieve(1, 10) == []
    mid = mem.approve(cid, reviewer_id=99)
    assert mem.retrieve(1, 10)[0]['value'] == 'محمد'
    assert mem.retrieve(1, 11) == []
    corrected = mem.correct(mid, 'مهدی', reviewer_id=99)
    assert corrected != mid and mem.retrieve(1, 10)[0]['value'] == 'مهدی'
    assert mem.forget(1, 10) == 1 and mem.retrieve(1, 10) == []


def test_extract_explicit_requires_a_declaration_not_a_command():
    mem = SemanticUserMemory(':memory:')
    assert mem.extract_explicit('زیرو حافظه منو بنویس') == []
    assert mem.extract_explicit('نوا زیرو چتای منو پاک کنید') == []
    assert mem.extract_explicit('اسم من حمیده') == [
        {'category': 'identity', 'key': 'preferred_name', 'value': 'حمیده', 'confidence': .9},
    ]
    assert mem.extract_explicit('من یاسین هستم') == [
        {'category': 'identity', 'key': 'preferred_name', 'value': 'یاسین', 'confidence': .9},
    ]
    assert mem.extract_explicit('منو YSN صدا کن') == [
        {'category': 'identity', 'key': 'nickname', 'value': 'YSN', 'confidence': .9},
    ]


def test_semantic_memory_rejects_sensitive_low_confidence_and_migrates(tmp_path):
    db=tmp_path/'semantic.db'; mem=SemanticUserMemory(db)
    with pytest.raises(ValueError): mem.candidate(chat_id=1,sender_id=1,category='interest',key='x',value='api key secret',confidence=.9)
    cid=mem.candidate(chat_id=1,sender_id=1,category='interest',key='x',value='coding',confidence=.5)
    with pytest.raises(ValueError): mem.approve(cid, reviewer_id=1)
    with sqlite3.connect(db) as con:
        names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'semantic_user_memory','semantic_user_memory_candidates'} <= names
