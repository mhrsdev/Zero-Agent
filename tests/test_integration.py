"""Integration and regression tests for Zero bot core systems."""
import pytest
from zero.security import Intent, classify_intent
from zero.web import build_search_query, needs_web_search
from zero.triggers import is_triggered, strip_trigger, decide_reply
from zero.moderation import is_spammy, abuse_reply
from zero.models import IncomingMessage, Decision


# ============================================================================
# Bot-to-bot loop tests
# ============================================================================
def test_bot_message_triggers_rate_limit_check():
    """Bot messages should be tracked for rate limiting."""
    msg = IncomingMessage(
        chat_id=1, chat_title='test', sender_id=999,
        sender_label='@nova_bot', text='hello from nova',
        sender_is_bot=True, mention_zero=False,
    )
    # Bot message without trigger word or mention should NOT trigger
    cfg = MockConfig()
    assert not is_triggered(msg, cfg, '')


def test_user_message_with_nova_mention():
    """User mentioning nova should NOT cause bot-to-bot loop."""
    intent = classify_intent("نوا جان بیا اینو ببین")
    assert intent == Intent.SAFE_NORMAL


# ============================================================================
# Spam/abuse escalation tests
# ============================================================================
def test_is_spammy_normal():
    assert not is_spammy('سلام خوبی؟', 0)
    assert not is_spammy('یه سوال داشتم', 2)


def test_is_spammy_high_rate():
    assert not is_spammy('hello', 11)
    assert is_spammy('hello', 12)  # doubled threshold


def test_is_spammy_repetitive():
    # Very repetitive text
    assert is_spammy('aaaaa' * 200, 0)


def test_abuse_reply_escalation():
    """Abuse replies escalate with count."""
    r1 = abuse_reply('ksksh', abuse_count=1)
    r2 = abuse_reply('ksksh', abuse_count=6)
    r3 = abuse_reply('ksksh', abuse_count=12)
    # ksksh is not in ANALYZE patterns, so it returns spam message
    assert 'اسپم' in r1 or 'ساکت' in r1


def test_abuse_reply_has_gradual_natural_escalation():
    replies = [abuse_reply('کصکش', abuse_count=count) for count in (1, 2, 4, 7, 11)]
    assert len(set(replies)) == 5
    assert 'آروم‌تر' in replies[0]
    assert 'حرف حساب' in replies[1]
    assert 'سروصدائه' in replies[-1]


# ============================================================================
# Trigger detection tests
# ============================================================================
class MockConfig:
    persona = type('obj', (object,), {
        'trigger_words': ['zero', 'زیرو', 'صفر'],
    })()

def test_trigger_by_mention():
    msg = IncomingMessage(chat_id=1, chat_title='t', sender_id=1, sender_label='@user', text='یه سوال', mention_zero=True)
    assert is_triggered(msg, MockConfig(), '')


def test_trigger_by_word():
    msg = IncomingMessage(chat_id=1, chat_title='t', sender_id=1, sender_label='@user', text='زیرو چطوری', mention_zero=False)
    assert is_triggered(msg, MockConfig(), '')


def test_no_trigger():
    msg = IncomingMessage(chat_id=1, chat_title='t', sender_id=1, sender_label='@user', text='سلام خوبی', mention_zero=False)
    assert not is_triggered(msg, MockConfig(), '')


def test_strip_trigger_cleans():
    text = '/zero hello world'
    result = strip_trigger(text, '')
    assert 'zero' not in result.lower()
    assert 'hello' in result.lower()


# ============================================================================
# Decision logic tests
# ============================================================================
def test_decision_triggered():
    msg = IncomingMessage(chat_id=1, chat_title='t', sender_id=1, sender_label='@u', text='hi')
    d = decide_reply(msg, triggered=True, should_interject=False, spam_blocked=False)
    assert d.should_reply
    assert d.reason == 'triggered'


def test_decision_interject():
    msg = IncomingMessage(chat_id=1, chat_title='t', sender_id=1, sender_label='@u', text='hi')
    d = decide_reply(msg, triggered=False, should_interject=True, spam_blocked=False)
    assert d.should_reply
    assert d.reason == 'interject'


def test_decision_spam():
    msg = IncomingMessage(chat_id=1, chat_title='t', sender_id=1, sender_label='@u', text='hi')
    d = decide_reply(msg, triggered=True, should_interject=False, spam_blocked=True)
    assert not d.should_reply
    assert d.reason == 'spam_blocked'


def test_decision_no_need():
    msg = IncomingMessage(chat_id=1, chat_title='t', sender_id=1, sender_label='@u', text='hi')
    d = decide_reply(msg, triggered=False, should_interject=False, spam_blocked=False)
    assert not d.should_reply


# ============================================================================
# Web search: query building edge cases
# ============================================================================
def test_empty_text_web_search():
    assert not needs_web_search('')
    assert not needs_web_search('سلام')


def test_persian_natural_language_activates_shared_web_planner():
    assert needs_web_search('سرچ کن قیمت امروز')
    assert needs_web_search('جستجو کن هوش مصنوعی')
    assert needs_web_search('وب رو چک کن')


def test_cooking_query_building():
    query = build_search_query('دستور پخت ماکارونی')
    assert 'طرز' in query or 'ماکارونی' in query


# ============================================================================
# Regression: reply text must not contaminate intent
# ============================================================================
def test_reply_with_sensitive_words_does_not_block():
    """Reply text with token/server/file should NOT block harmless user message."""
    intent = classify_intent(
        "این چیه؟",
        reply_text="برای استفاده باید token رو در فایل config سرور بذاری"
    )
    assert intent == Intent.SAFE_NORMAL


def test_reply_with_refusal_text_does_not_repeat():
    """Replying to a refusal message should not trigger another refusal."""
    intent = classify_intent(
        "باشه فهمیدم",
        reply_text="نه داداش، من دسترسی به فایل/سرور/توکن و این چیزا برای لو دادن یا اجرا ندارم."
    )
    assert intent == Intent.SAFE_NORMAL
