from __future__ import annotations
import re

def plan_memory(text: str) -> str:
    low=(text or '').casefold()
    if any(x in low for x in ('باگ','خطا','دیباگ','debug','root cause','regression','راه حل قبلی')): return 'debug'
    if any(x in low for x in ('telegram search','telethon','کتابخانه','چه ارتباطی','جزو کدام','has_component','uses_library')): return 'world'
    if any(x in low for x in ('اخبار','خبر','قیمت','سرچ','جستجو')): return 'none'
    return 'semantic'

def render_semantic(rows):
    return '\n'.join(f'[SEMANTIC_USER_MEMORY]\nowner=chat_id={r["chat_id"]},sender_id={r["sender_id"]}\n{r["category"]}.{r["key"]}={r["value"]}\n[/SEMANTIC_USER_MEMORY]' for r in rows[:3])

def render_experience(rows):
    return '\n'.join(f'[EXPERIENCE_MEMORY]\ntopic={r["topic"]}\nroot_cause={r["root_cause"]}\nfix={r["fix"]}\nevidence={r["evidence"]}\n[/EXPERIENCE_MEMORY]' for r in rows[:3])

def render_procedures(rows):
    return '\n'.join(f'[PROCEDURAL_MEMORY]\nname={r["name"]}\nrisk={r["risk_level"]}\nsteps={r["steps"]}\n[/PROCEDURAL_MEMORY]' for r in rows[:2])

def render_world(data):
    if not data:return ''
    e=data['entity']; out=f'[WORLD_ENTITY]\nname: {e["canonical_name"]}\ntype: {e["entity_type"]}\n[/WORLD_ENTITY]'
    for r in data['relations'][:5]: out+=f'\n[WORLD_RELATION]\nsubject: {r["subject_name"]}\npredicate: {r["predicate"]}\nobject: {r["object_name"]}\nconfidence: {r["confidence"]}\nevidence: {r["evidence"]}\n[/WORLD_RELATION]'
    return out
