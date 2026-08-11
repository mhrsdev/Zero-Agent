from __future__ import annotations

import re
from .models import IncomingMessage, Decision
from .config import ZeroConfig


ZERO_CMD_RE = re.compile(r"^/zero(?:@\w+)?(?:\s|$)", re.I)
SEARCH_CMD_RE = re.compile(r"^/(?:search|deep(?:_|-)?search)(?:\s|$)", re.I)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def is_triggered(message: IncomingMessage, config: ZeroConfig, account_username: str = "") -> bool:
    text = message.text or ""
    low = text.lower()
    if message.reply_to_zero:
        return True
    if ZERO_CMD_RE.match(low):
        return True
    if SEARCH_CMD_RE.match(text):
        return True
    if any(word.lower() in low for word in config.persona.trigger_words):
        return True
    if account_username and f"@{account_username.lower()}" in low:
        return True
    return message.mention_zero


def strip_trigger(text: str, account_username: str = "") -> str:
    text = ZERO_CMD_RE.sub("", text or "").strip()
    if account_username:
        text = re.sub(rf"@{re.escape(account_username)}", "", text, flags=re.I).strip()
    return normalize_text(text) or "یه جواب کوتاه و طبیعی بده."


def decide_reply(message: IncomingMessage, triggered: bool, should_interject: bool, spam_blocked: bool) -> Decision:
    if spam_blocked:
        return Decision(False, "spam_blocked")
    if triggered:
        return Decision(True, "triggered", continue_generation=True)
    if should_interject:
        return Decision(True, "interject", interject=True, continue_generation=True)
    return Decision(False, "no_need")
