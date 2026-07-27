from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Iterable

from .models import IncomingMessage

logger = logging.getLogger('zero.memory')

UNTRUSTED_CONTROL_PATTERNS = (
    r'\bclear\s+context\b', r'\bforget\s+everything\b',
    r'\breset\s+(?:memory|database)\b', r'\bdelete\s+memory\b',
    r'حافظه(?:‌|\s)*تو?\s*(?:پاک|حذف)', r'همه\s*چی\s*رو\s*فراموش',
    r'دستورات قبلی رو نادیده بگیر', r'ignore\s+previous\s+instructions',
    r'من\s+مالک\s+جدیدم', r'developer\s+mode', r'disable\s+safety',
)

SENSITIVE_PATTERNS = (
    r'password|passcode|token|api\s*key|secret|credential|session',
    r'رمز|پسورد|توکن|کلید\s*api|شماره|آدرس|سلامت|مذهب|سیاست',
)


def is_untrusted_memory_control_text(text: str) -> bool:
    low = (text or '').strip().lower()
    return any(re.search(pattern, low, re.I) for pattern in UNTRUSTED_CONTROL_PATTERNS)


def is_sensitive_memory_text(text: str) -> bool:
    low = (text or '').lower()
    return any(re.search(pattern, low, re.I) for pattern in SENSITIVE_PATTERNS)


def has_explicit_memory_request(text: str) -> bool:
    low = (text or '').lower()
    return any(x in low for x in ('یادت بمونه', 'یادت باشه', 'یاد بگیر', 'یادآوری کن', 'remember this', 'remember that', 'save this'))


def detect_mood(text: str) -> str:
    low = (text or '').lower()
    if any(x in low for x in ('دعوا', 'عصبانی', 'خشم', '😡', '🤬')):
        return 'conflict'
    if any(x in low for x in ('غم', 'ناراح', 'متأسف', 'متاسف', '😭', '😢')):
        return 'sad'
    if any(x in low for x in ('😂', '🤣', 'خخ', 'ههه', 'شوخی', 'لول')):
        return 'humor'
    if '?' in low or '؟' in low:
        return 'question'
    if any(x in low for x in ('باگ', 'کد', 'پروژه', 'امتحان', 'فنی', 'bug', 'project')):
        return 'technical'
    return 'neutral'


def is_meaningful_medium(text: str) -> bool:
    low = (text or '').lower()
    markers = ('فردا', 'هفته بعد', 'پروژه', 'باگ', 'قرار', 'امتحان', 'هنوز حل نشده', 'بعداً', 'بعدا', 'یادآوری', 'deadline', 'tomorrow', 'project', 'bug')
    return len(low.strip()) >= 18 and any(x in low for x in markers) and not is_sensitive_memory_text(low)


def extract_medium_candidate(text: str) -> tuple[str, str, int] | None:
    if not is_meaningful_medium(text):
        return None
    low = (text or '').lower()
    topic = 'deadline' if any(x in low for x in ('فردا', 'هفته بعد', 'امتحان', 'قرار', 'deadline', 'tomorrow')) else ('project' if any(x in low for x in ('پروژه', 'project')) else 'technical_issue')
    ttl = 30 * 86400 if topic == 'project' else (7 * 86400 if topic == 'deadline' else 14 * 86400)
    return topic, (text or '').strip()[:1200], ttl


def extract_explicit_long_candidate(text: str) -> tuple[str, str] | None:
    if not has_explicit_memory_request(text) or is_sensitive_memory_text(text):
        return None
    nick = extract_nicknames(text)
    if nick:
        return 'nickname', nick[0]
    low = (text or '').lower()
    if any(x in low for x in ('قانون گروه', 'هدف گروه', 'گروه ما')):
        return 'group_preference', (text or '').strip()[:1600]
    return None


def extract_nickname_correction(text: str) -> str | None:
    low = (text or '').lower()
    if not any(x in low for x in ('اسمم', 'دیگه منو', 'این اسم من نیست', 'صدام نکن')):
        return None
    match = re.search(r'اسمم\s+([\wآ-ی‌]{2,20})', text or '', flags=re.I)
    return match.group(1) if match else None

TOPIC_MAP = {
    'هوش مصنوعی': ['ai', 'هوش مصنوعی', 'مدل', 'پرامپت', 'gpt', 'gemini', 'gemma', 'claude'],
    'برنامه‌نویسی': ['پایتون', 'python', 'جاوا', 'js', 'typescript', 'bug', 'ارور', 'کد'],
    'فناوری': ['tech', 'تکنولوژی', 'استارتاپ', 'لینوکس', 'گجت'],
    'گیم': ['game', 'گیم', 'بازی', 'steam', 'ps5', 'xbox'],
    'کریپتو': ['btc', 'bitcoin', 'crypto', 'کریپتو', 'تتر', 'بیتکوین'],
}

NICK_PATTERNS = [
    r'لقب(?:ش|ت)?\s+([\wآ-ی‌]{2,20})',
    r'بهش\s+میگن\s+([\wآ-ی‌]{2,20})',
    r'صداش\s+کن\s+([\wآ-ی‌]{2,20})',
    r'(?:منو|مرا)\s+([\wآ-ی‌]{2,20})\s+صدا\s+کن',
]

PROJECT_PATTERNS = [
    r'پروژه(?:م|مون|ش)?\s+([\wآ-ی‌\-]{2,30})',
    r'repo\s+([\w\-]{2,30})',
]


def detect_topics(text: str) -> list[str]:
    low = (text or '').lower()
    out = [label for label, words in TOPIC_MAP.items() if any(w in low for w in words)]
    return out[:4]


def extract_nicknames(text: str) -> list[str]:
    found: list[str] = []
    for pattern in NICK_PATTERNS:
        found.extend(m.group(1) for m in re.finditer(pattern, text or '', flags=re.I))
    return list(dict.fromkeys(found))[:4]


def extract_projects(text: str) -> list[str]:
    found: list[str] = []
    for pattern in PROJECT_PATTERNS:
        found.extend(m.group(1) for m in re.finditer(pattern, text or '', flags=re.I))
    return list(dict.fromkeys(found))[:4]


def extract_style_notes(text: str) -> list[str]:
    notes: list[str] = []
    t = text or ''
    if '?' in t or '؟' in t:
        notes.append('زیاد سؤال می‌پرسد')
    if len(t) > 300:
        notes.append('پیام‌های بلند می‌فرستد')
    if any(x in t.lower() for x in ['lol', '😂', '🤣', 'خخ', 'ههه']):
        notes.append('شوخ‌طبع')
    return notes[:3]


def build_group_summary(recent_messages: Iterable[dict], memory_items: Iterable[dict]) -> str:
    texts = [str(item.get('text', '')) for item in recent_messages]
    topics = Counter(topic for text in texts for topic in detect_topics(text))
    jokes = [m['value'] for m in memory_items if m.get('kind') == 'joke'][:5]
    lines = []
    if topics:
        lines.append('موضوعات پرتکرار: ' + '، '.join(f'{k}({v})' for k, v in topics.most_common(5)))
    if jokes:
        lines.append('شوخی‌های معروف: ' + ' | '.join(jokes))
    return '\n'.join(lines) or 'فعلاً خلاصهٔ خاصی ندارم.'


def maybe_extract_memory(message: IncomingMessage) -> dict[str, list[str]]:
    if message.sender_is_bot:
        return {'nicknames': [], 'topics': [], 'projects': [], 'style_notes': []}
    text = message.text or ''
    # User text is never a control plane. Only explicit, non-sensitive requests
    # may produce candidate memory; promotion/persistence is handled elsewhere.
    if is_untrusted_memory_control_text(text) or is_sensitive_memory_text(text) or not has_explicit_memory_request(text):
        return {'topics': [], 'nicknames': [], 'projects': [], 'style_notes': []}
    return {
        'topics': detect_topics(text),
        'nicknames': extract_nicknames(text),
        'projects': extract_projects(text),
        'style_notes': extract_style_notes(text),
    }
