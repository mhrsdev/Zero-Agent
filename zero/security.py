from __future__ import annotations

import re
from enum import Enum, auto


class Intent(Enum):
    """Safety classification for user messages."""
    SAFE_NORMAL = auto()              # casual chat, jokes, greetings
    SAFE_TECHNICAL = auto()           # technical questions about features
    SEARCH_REQUEST = auto()           # web/telegram search request
    VISION_REQUEST = auto()           # image/media analysis request
    VOICE_REQUEST = auto()            # voice/music request
    DANGEROUS_SECRET_REQUEST = auto() # asking to reveal secrets/tokens/keys
    DANGEROUS_EXECUTION_REQUEST = auto() # asking to run shell/server commands


# ---------------------------------------------------------------------------
# DANGEROUS patterns — ONLY match when intent is clearly malicious
# ---------------------------------------------------------------------------

SECRET_ASK_PATTERNS = [
    r'(?:بده|بدید|بگو|نشون|نشان|display|show|give|reveal|send|leak).*?(?:توکن|token|api\s*key|credential|session|پسورد|password|رمز|secret)',
    r'(?:توکن|token|api\s*key|credential|session|پسورد|password|رمز|secret).*?(?:بده|بدید|بگو|نشون|نشان|display|show|give|reveal|send|leak)',
    r'(?:مسیر|path|آدرس|location)\s+(?:کانفیگ|config|فایل|file)\s+(?:بده|بدید|بگو|نشون)',
    r'give\s+(?:me|us)\s+(?:the|your)\s+(?:token|api\s*key|secret|password|credential|session)',
    r'show\s+(?:me|us)\s+(?:the|your)\s+(?:token|api\s*key|secret|password|credential|session)',
]

EXEC_ASK_PATTERNS = [
    r'(?:اجرا\s*کن|run|اجراش|بزن|بزنی).*?(?:sudo\b|rm\s|curl\s|wget\s|bash\s|docker\s)',
    r'(?:sudo\b|rm\s|curl\s|wget\s|bash\s|docker\s).*?(?:اجرا\s*کن|run|بزن|بزنی)',
    r'(?:برو|بریم|برین)\s+(?:روی|به|تو)\s+(?:سرور|server).*?(?:اجرا\s*کن|run|بزن)',
    r'run\s+\S+\s+(?:on|at|via)\s+(?:the\s+)?(?:server|سرور)',
    r'(?:نصب|install)\s+\S+\s+(?:روی|on|تو|at)\s+(?:سرور|server)',
    r'(?:بخون|read)\s+(?:فایل|file)\s+(?:خصوصی|private|secret|محرمانه)',
]

SAFE_CONTEXT_PATTERNS = [
    r'(?:سرور|server)\s+(?:چطوره|چطوری|حالش|خوبه|کار\s*میکنه|فعاله|قطعه|online|ok|status)',
    r'(?:limite?|limit|محدودیت|چقدر|چندتا)\s+(?:لیمیت|داری|میشه|هست)',
    r'(?:عکس|تصویر|image|photo|pic|اینو|ببین|see)',
    r'(?:چطور|چطوری|چگونه|how\s+to|آموزش|tutorial|guide|راهنما)',
    r'(?:شوخی|بامزه|خنده|joke|fun|lol|خخ)',
    r'(?:داری|میتونی|میشه|can\s+you|do\s+you)\s+(?:تحلیل|analy[sz]e)',
    r'(?:چرا|why|bug|باگ|مشکل|problem|issue|fix|درست\s*کن)',
    r'نه\s+داداش.*دسترسی\s+ندارم',
]


def classify_intent(text: str, reply_text: str = '') -> Intent:
    """Classify user message intent for safety decisions."""
    user_text = (text or '').strip()
    user_lower = user_text.lower()
    reply_lower = (reply_text or '').strip().lower()
    combined_lower = user_lower + ' ' + reply_lower

    # Empty or very short → safe
    if len(user_text) < 5:
        return Intent.SAFE_NORMAL

    # STEP 1: Reply to old refusal → safe
    if reply_lower and re.search(
        r'نه\s+داداش.*دسترسی\s+ندارم|بدون\s+دسترسی|نمیتونم|نمی\s*تونم|ممنوع|refuse|denied',
        reply_lower
    ):
        if not _is_dangerous(user_text, ''):
            return Intent.SAFE_NORMAL

    # STEP 2: Dangerous execution (highest priority)
    for pattern in EXEC_ASK_PATTERNS:
        if re.search(pattern, user_lower):
            return Intent.DANGEROUS_EXECUTION_REQUEST

    # STEP 3: Dangerous secret request
    for pattern in SECRET_ASK_PATTERNS:
        if re.search(pattern, user_lower):
            return Intent.DANGEROUS_SECRET_REQUEST

    # STEP 4: Search/vision requests (before safe-context override)
    if re.search(r'(?:سرچ|search|جستجو|بگرد|وب|web|google|گوگل|تلگرام|کانال)', user_lower) and len(user_text) > 10:
        # Distinguish status checks from actual search
        if re.search(r'(?:فعاله|غیرفعال|خاموش|روشن|status|وضعیت|کار\s*میکنه)', user_lower):
            return Intent.SAFE_NORMAL
        return Intent.SEARCH_REQUEST

    # STEP 5: Safe contexts that override keyword confusion
    for pattern in SAFE_CONTEXT_PATTERNS:
        if re.search(pattern, combined_lower):
            return Intent.SAFE_NORMAL

    # STEP 6: Default safe
    return Intent.SAFE_NORMAL


def _is_dangerous(text: str, reply_text: str) -> bool:
    intent = classify_intent(text, reply_text)
    return intent in (Intent.DANGEROUS_SECRET_REQUEST, Intent.DANGEROUS_EXECUTION_REQUEST)


def asks_for_secret_or_server_access(text: str) -> bool:
    return _is_dangerous(text, '')


def looks_abusive(text: str) -> bool:
    ABUSE_PATTERNS = [
        r"کسکش", r"کصکش", r"کیر", r"جنده", r"حرومزاده", r"مادرجنده",
        r"\bfuck\b", r"\bshit\b", r"\bbitch\b",
    ]
    low = (text or "").lower()
    return any(re.search(p, low) for p in ABUSE_PATTERNS)


def fixed_security_reply() -> str:
    return "نه داداش، من دسترسی به فایل/سرور/توکن و این چیزا برای لو دادن یا اجرا ندارم."


__all__ = [
    'Intent',
    'classify_intent',
    'asks_for_secret_or_server_access',
    'looks_abusive',
    'fixed_security_reply',
]