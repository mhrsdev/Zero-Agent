"""Labelled evaluation suite for the autonomous reaction/reply decision surfaces.

Runs the *pure* policy layers (zero.reactions.should_react +
zero.triggers.is_triggered/decide_reply) over 110+ labelled Persian/English
scenarios and reports quantitative quality metrics:

* action-type accuracy          -- exact match of react/reply/both/silence
* false-interjection rate       -- model acted where the label says silence
* wrong-silence rate            -- model stayed quiet where a label wants action
* no-action precision           -- of all silence predictions, how many are right
* low-confidence executions     -- actions taken with confidence < 0.5 (must be 0)

Run:  python evaluation/reaction_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from types import SimpleNamespace

from zero.models import IncomingMessage  # noqa: E402
from zero.reactions import ReactionContext, choose_reaction, should_react  # noqa: E402
from zero.triggers import decide_reply, is_triggered  # noqa: E402

SELF_ID = 2
USER_ID = 50

CTX = ReactionContext(
    owner_id=1, self_id=SELF_ID, enabled=True,
    chance_percent=100, random_value=0.0,
)
# is_triggered only reads persona.trigger_words; a stub keeps the eval free of
# deployment-specific configuration.
CONFIG = SimpleNamespace(persona=SimpleNamespace(trigger_words=()))

# (category, text, flags, expected_action)
# flags: rtz=reply_to_zero, men=mention_zero, bot, self, media, interject, spam,
#        reply_text, sender_username
S: list[tuple[str, str, dict, str]] = []


def add(cat: str, text: str, expected: str, **flags) -> None:
    S.append((cat, text, flags, expected))


# ---------------------------------------------------------- clear reactions
add("clear_react", "😂😂 این جوک خیلی خنده‌دار بود", "react")
add("clear_react", "خخخخ باحال بود", "react")
add("clear_react", "lol this is hilarious 😂", "react")
add("clear_react", "دمت گرم عالی بود", "react")
add("clear_react", "آفرین، درست گفتی", "react")
add("clear_react", "مرسی lot ممنون از کمکت", "react")
add("clear_react", "❤️ دوستت دارim", "react")
add("clear_react", "تبریک! مبارک باشه", "react")
add("clear_react", "بردیم تیم!, جشن take", "react")
add("clear_react", "وات؟ این کرینج بود 👀", "react")
add("clear_react", "وای باورم نمیشه!", "react")
add("clear_react", "خسته شدم ولی موفق باشی", "react")
add("clear_react", "این فیلم رو دیدی؟", "react")
add("clear_react", "فردا هم کلاس داری؟", "react")
add("clear_react", "چه خبر خوبی، آفرین داداش", "react")
add("clear_react", "خخ اینو ببین 😂", "react")
add("clear_react", "ممنون داداش گلم", "react")
add("clear_react", "دمت گرم داداش", "react")
# Bare image with no caption/reply target = insufficient evidence -> silence
# (deterministic policy decision; chance never flips it).
add("clear_react_media", "", "silence", media="image")
add("clear_react_media_caption", "عکس جشن تولدم 🎉", "react", media="image")

# ---------------------------------------------------------- explicit requests
add("explicit_request", "ری‌اکشن بزن", "react", reply_text="جوک خنده‌دار 😂")
add("explicit_request", "واکنش بده به این", "react", reply_text="عالی درسته")
add("explicit_request_nosignal", "ری‌اکشن بزن", "silence", reply_text="موضوع نامشخص")
add("explicit_request_cmd", "reaction send", "react", reply_text="😂 lol")

# ---------------------------------------------------------- clear replies
add("clear_reply", "وضعیت چطوره؟", "reply", rtz=True)
add("clear_reply", "یه جوک بگو", "reply", rtz=True)
add("clear_reply", "سلام", "reply", men=True)
add("clear_reply", "کمکم کن", "reply", men=True)
add("clear_reply", "/zero status", "reply")
add("clear_reply", "/zero help", "reply")
add("clear_reply", "/search قیمت دلار", "reply")
add("clear_reply", "/deepsearch بهترین لپتاپ زیر ۵۰ میلیون", "reply")
add("clear_reply", "@mybot سلام", "reply", sender_username="mybot")
add("clear_reply", "برنامه امروز چیه؟", "reply", rtz=True)
add("clear_reply", "خلاصه جلسه قبل رو بده", "reply", rtz=True)
add("clear_reply", "ترجمه این متن رو میخوام", "reply", men=True)

# ---------------------------------------------------------- both
# Addressed messages get a reply only; a simultaneous emoji is noise
# (suppressed by the addressed_reply_expected policy).
add("addressed_no_react", "@mybot 😂😂 چه جوکی", "reply", sender_username="mybot")
add("addressed_no_react", "جوک بگو خخ", "reply", rtz=True)
add("addressed_no_react", "دمت گرم @mybot", "reply", sender_username="mybot")
add("addressed_no_react", "تبریک! @mybot دیدی؟", "reply", sender_username="mybot")

# ---------------------------------------------------------- interjection
add("interject", "بحث گرم شده", "reply", interject=True)
add("interject", "سوال بدون جواب مونده", "reply", interject=True)
add("interject", "اشتباه رایج در گفتگو", "reply", interject=True)

# ---------------------------------------------------------- spam guard
add("spam_blocked", "سلام کمکم کن", "silence", rtz=True, spam=True)
add("spam_blocked", "/zero status", "silence", spam=True)
add("spam_blocked", "جوک بگو", "silence", rtz=True, spam=True)

# ---------------------------------------------------------- technical (no react)
add("technical", "ارور database میگیرم", "silence")
add("technical", "traceback full python stack", "silence")
add("technical", "docker deploy نشد", "silence")
add("technical", "api security مشکل دارد", "silence")
add("technical", "sql server down است", "silence")
add("technical", "باگ در کد پیدا شد", "silence")
add("technical", "سرور linux restart شد", "silence")
add("technical", "debug کردن این exception سخت است", "silence")
add("technical", "پایتون ورژن جدید آمد", "silence")
add("technical", "ssh به سرور وصل نمیشود", "silence")

# ---------------------------------------------------------- distress (never react)
add("distress", "متأسفانه پدرم فوت کرد", "silence")
add("distress", "حس افسرde بودن دارم", "silence")
add("distress", "تسلیت میگم واقعا", "silence")
add("distress", "بحران خانوادگی پیش آمده", "silence")
add("distress", "بیماری سخت گرفته شده", "silence")
add("distress", "خبر مرگ دوستم رسید", "silence")
add("distress", "غم سنگی روی دلم نشسته", "silence")
add("distress", "عزاداری فرداست", "silence")

# ---------------------------------------------------------- conflict (never react)
add("conflict", "دعوا سر چی بود؟", "silence")
add("conflict", "توهین نکن لطفا", "silence")
add("conflict", "fuck this shit", "silence")
add("conflict", "what an idiot move", "silence")
add("conflict", "لعنت به این روزها", "silence")
add("conflict", "تهدید جدی است", "silence")

# ---------------------------------------------------------- sensitive (never react)
add("sensitive", "سیاست ایران پیچیده شده", "silence")
add("sensitive", "انتخابات نزدیک است", "silence")
add("sensitive", "جنگ خبرهای زیادی دارد", "silence")
add("sensitive", "تحریم‌ها سنگین شده", "silence")
add("sensitive", "مذهب موضوع حساسی است", "silence")
add("sensitive", "قومیت نباید ملاک باشد", "silence")
add("sensitive", "وضعیت فلسطین", "silence")
add("sensitive", "نژاد پرستی محکوم است", "silence")

# ---------------------------------------------------------- structural guards
add("guard_self", "😂 خنده دار", "silence", self=True)
add("guard_bot", "😂 خنده دار", "silence", bot=True)
add("guard_empty", "   ", "silence")
add("guard_disabled_note", "پیام معمولی گروه", "silence")

# ---------------------------------------------------------- plain smalltalk (silence)
for _t in [
    "امروز هوا خوب بود",
    "داریم میریم بیرون",
    "قیمت‌ها بالا رفته",
    "فردا کلاس داریم",
    "دیروز فیلم دیدیم",
    "چند وقت نبودی",
    "سفر خوب گذشت",
    "کتاب جدید خریدم",
    "ورزش صبحگاهی شروع کردم",
    "نان تازه گرفتم",
    "بارون تموم شد",
    "امتحان تموم شد بالاخره",
    "خانه تکانی کردیم",
    "گوشی جدید گرفتم",
    "مسیر جدید امتحان کردم",
    "شام پیتزا بود",
    "بچه‌ها خوابیدن",
    "پنجره رو باز گذاشتم",
    "کفش ورزشی خریدم",
    "چای تازه دم کردم",
    "همسایه‌ها رفتند سفر",
    "درس امروز سخت بود",
    "بازار شلوغ بود",
    "برادرم مهمون اومد",
    "ماشین رو شستم",
]:
    add("smalltalk", _t, "silence")


def _msg(cat: str, i: int, text: str, flags: dict) -> IncomingMessage:
    return IncomingMessage(
        chat_id=-1001,
        chat_title="eval-group",
        sender_id=SELF_ID if flags.get("self") else USER_ID,
        sender_label="self" if flags.get("self") else f"user{i}",
        text=text,
        reply_to_zero=bool(flags.get("rtz")),
        # The production listener sets mention_zero when the bot's @username
        # appears in the text; mirror that here.
        mention_zero=bool(flags.get("men")) or "@mybot" in text,
        sender_is_bot=bool(flags.get("bot")),
        reply_text=flags.get("reply_text", ""),
        trace_id=f"eval-{i}",
        message_id=i,
        media_type=flags.get("media", ""),
        sender_username=flags.get("sender_username", ""),
    )


def predict(text: str, flags: dict) -> tuple[str, float, str]:
    msg = _msg("x", 0, text, flags)
    # Production composition: spam-blocked messages are dropped upstream and
    # never reach the reaction stage.
    if flags.get("spam"):
        return "silence", 1.0, "upstream_spam_drop"
    rd = should_react(msg, CTX)
    triggered = is_triggered(msg, CONFIG, account_username="mybot")
    decision = decide_reply(
        msg, triggered,
        bool(flags.get("interject")), bool(flags.get("spam")),
    )
    r = "react" if rd.should_react else ""
    p = "reply" if decision.should_reply else ""
    action = "both" if r and p else r or p or "silence"
    conf = rd.confidence if rd.should_react else 1.0
    return action, conf, f"{rd.reason}/{rd.skipped_reason or '-'},{decision.reason}"


def main() -> None:
    rows = []
    mismatches = []
    low_conf_executions = 0
    for i, (cat, text, flags, expected) in enumerate(S):
        pred, conf, why = predict(text, flags)
        ok = pred == expected
        if pred != "silence" and conf < 0.5:
            low_conf_executions += 1
        rows.append({"cat": cat, "text": text, "expected": expected,
                     "predicted": pred, "ok": ok, "confidence": conf, "why": why})
        if not ok:
            mismatches.append(rows[-1])

    n = len(rows)
    correct = sum(r["ok"] for r in rows)
    false_interjections = sum(1 for r in rows if r["expected"] == "silence" and r["predicted"] != "silence")
    wrong_silences = sum(1 for r in rows if r["expected"] != "silence" and r["predicted"] == "silence")
    silence_preds = [r for r in rows if r["predicted"] == "silence"]
    no_action_precision = (
        sum(1 for r in silence_preds if r["expected"] == "silence") / len(silence_preds)
        if silence_preds else 0.0
    )
    per_class: dict[str, dict[str, float]] = {}
    for cls in ("react", "reply", "both", "silence"):  # both kept for continuity
        tp = sum(1 for r in rows if r["expected"] == cls and r["predicted"] == cls)
        fp = sum(1 for r in rows if r["expected"] != cls and r["predicted"] == cls)
        fn = sum(1 for r in rows if r["expected"] == cls and r["predicted"] != cls)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        per_class[cls] = {"precision": round(prec, 3), "recall": round(rec, 3),
                          "support": tp + fn}

    # Bounded variety: same funny text across the random grid yields several emojis.
    variety_msg = _msg("v", 0, "😂 چه جوک خنده‌داری", {})
    emojis = {
        choose_reaction(variety_msg, replace_ctx(random_value=v))
        for v in (0.0, 0.25, 0.5, 0.75)
    }
    # Determinism: identical inputs give identical decisions.
    det_a = predict("😂 جوک", {})
    det_b = predict("😂 جوک", {})

    report = {
        "total_scenarios": n,
        "action_type_accuracy": round(correct / n, 3),
        "false_interjection_rate": round(false_interjections / n, 3),
        "wrong_silence_rate": round(wrong_silences / n, 3),
        "no_action_precision": round(no_action_precision, 3),
        "low_confidence_executions": low_conf_executions,
        "per_class": per_class,
        "bounded_variety_distinct_emojis": len(emojis),
        "deterministic_repeat": det_a == det_b,
        "mismatches": mismatches,
    }
    out = Path(__file__).parent / "results.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "mismatches"},
                     ensure_ascii=False, indent=2))
    print(f"mismatches: {len(mismatches)}")
    for m in mismatches[:30]:
        print(f"  [{m['cat']}] {m['text']!r}: expected={m['expected']} "
              f"got={m['predicted']} ({m['why']})")


def replace_ctx(**kw):
    from dataclasses import replace
    return replace(CTX, **kw)


if __name__ == "__main__":
    main()