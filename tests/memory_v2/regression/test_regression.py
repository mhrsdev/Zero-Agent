import asyncio, json
from pathlib import Path
from zero.memory_v2.service import MemoryItem, MemoryV2Service
from zero.models import IncomingMessage

def test_corpus_has_required_minimum_and_categories():
    rows=[json.loads(x) for x in Path('tests/fixtures/memory_v2/regression_corpus.jsonl').read_text().splitlines()]
    assert len(rows)>=50 and {'casual','education_track_correction','multi_user_group','project_continuation','superseded','forwarded','bot','restart','session_change'} <= {x['category'] for x in rows}

def test_long_conversation_stays_bounded(tmp_path):
    async def run():
        s=MemoryV2Service(str(tmp_path/'v2.db')); m=IncomingMessage(1,'g',1,'u','zero deployment',message_id=1)
        for i in range(500):
            await s.put(MemoryItem('', 'fact','group_user',f'zero deployment step {i}','zero deployment',1,1,group_id=1,subject='deploy',predicate=f'step{i}',importance=.8,confidence=.9))
        block,meta=await s.context(m)
        assert meta['selected']<=5 and meta['tokens']<=700 and len(block)<3000
    asyncio.run(run())
