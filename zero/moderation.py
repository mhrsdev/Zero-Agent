from __future__ import annotations

def is_spammy(text: str, recent_count: int) -> bool:
    compact = ''.join((text or '').split())
    if recent_count >= 12:
        return True
    if len(compact) > 500 and len(set(compact)) < 15:
        return True
    return False
