from __future__ import annotations

from .security import looks_abusive


def is_spammy(text: str, recent_count: int) -> bool:
    compact = ''.join((text or '').split())
    if recent_count >= 12:
        return True
    if len(compact) > 500 and len(set(compact)) < 15:
        return True
    return False


def abuse_reply(text: str, abuse_count: int = 1) -> str:
    if looks_abusive(text):
        if abuse_count >= 11:
            return 'این دیگه بحث نیست، فقط سروصدائه؛ هر وقت حرف حساب داشتی بگو.'
        if abuse_count >= 7:
            return 'این حجم عصبانیت برای یه پیام کوچیک؟ خودت هم می‌دونی کم آوردی.'
        if abuse_count >= 4:
            return 'کم‌کم داری ثابت می‌کنی حرفی برای گفتن نداری.'
        if abuse_count >= 2:
            return 'فحش دادن آسونه؛ یه حرف حساب هم بلدی؟'
        return 'عه، آروم‌تر رفیق 😄'
    return 'کمتر اسپم کن، وگرنه ترجیح می‌دم ساکت بمونم.'
