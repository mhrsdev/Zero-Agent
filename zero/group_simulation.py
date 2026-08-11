from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .brain import ZeroBrain
from .config import ZeroConfig
from .models import IncomingMessage
from .router import IndependentRouter
from .storage import ZeroStore

CHAT_ID = -1001389001
ACCOUNT_SCOPE = "simulation"
ZERO_USER_ID = 900000


@dataclass(frozen=True, slots=True)
class SimulationMember:
    user_id: int
    label: str
    username: str
    display_name: str
    identity_group: str
    abusive: bool
    preference: str


def build_members() -> tuple[SimulationMember, ...]:
    rows = (
        (1001, "علی", "ali_one", "علی", "exact_ali", False, "قهوه تلخ"),
        (1002, "علی", "ali_two", "علی", "exact_ali", True, "چای دارچین"),
        (1003, "علی", "ali_three", "علی", "exact_ali", False, "آب پرتقال"),
        (1004, "رضا", "reza_fa", "رضا", "cross_script_reza", False, "شطرنج"),
        (1005, "Reza", "reza_en", "Reza", "cross_script_reza", True, "موسیقی راک"),
        (1006, "REZA", "reza_caps", "REZA", "cross_script_reza", False, "پیتزای سبزیجات"),
        (1007, "سارا", "sara", "سارا", "unique", False, "فیلم علمی تخیلی"),
        (1008, "Nima", "nima", "Nima", "unique", False, "فوتبال"),
        (1009, "مریم", "maryam", "مریم", "unique", False, "کتاب داستان"),
        (1010, "Kian", "kian", "Kian", "unique", True, "بازی گروهی"),
        (1011, "نگار", "negar", "نگار", "unique", False, "هوای خنک"),
        (1012, "Arman", "arman", "Arman", "unique", False, "برنامه‌نویسی پایتون"),
        (1013, "یاسمین", "yasamin", "یاسمین", "unique", False, "موسیقی سنتی"),
        (1014, "Pouya", "pouya", "Pouya", "unique", True, "گیم شبانه"),
        (1015, "الهام", "elham", "الهام", "unique", False, "چای سبز"),
    )
    return tuple(SimulationMember(*row) for row in rows)


_NORMAL_TEMPLATES = (
    "بچه‌ها امتحان فردا ساعت چند شروع میشه؟",
    "کسی تکلیف ریاضی رو حل کرده؟ سوال آخرش عجیب بود.",
    "امشب برای بازی گروهی پایه‌اید یا همه درس دارید؟",
    "من تازه رسیدم، بحث درباره چی بود؟",
    "فیلم دیشب رو دیدید؟ آخرش خیلی غیرمنتظره بود.",
    "برای پروژه بهتره کارها رو بین چند نفر تقسیم کنیم.",
    "هوا امروز خوبه، بعد کلاس بریم یه دور کوتاه؟",
    "این آهنگه رو شنیدید؟ ریتمش خیلی خوبه.",
    "فردا یکی یادآوری کنه فایل ارائه رو بفرستم.",
    "به نظرم اول مسئله ساده‌تر رو حل کنیم بعد بریم سراغ سختش.",
    "من با این برنامه مشکلی ندارم، فقط زمانش رو هماهنگ کنید.",
    "کسی خبر داره نتیجه مسابقه چی شد؟",
    "ناهار چی گرفتید؟ من هنوز تصمیم نگرفتم.",
    "اینترنت من امروز خیلی کند شده، برای شما هم همینه؟",
    "یه عکس از جزوه بفرستید، اون صفحه رو ندارم.",
    "فکر کنم جواب قبلی درست بود ولی دوباره چکش کنیم.",
    "امروز کلاس خیلی طولانی شد، همه خسته شدن.",
    "برای آخر هفته چه برنامه‌ای دارید؟",
)

_ABUSIVE_TEXTS = (
    "خفه شو بابا، بذار بقیه هم حرف بزنن.",
    "زیادی حرف میزنی، بحث رو الکی کش نده.",
    "لازم نیست جواب بدی، ولش کن دیگه.",
    "گمشو بابا، این حرفت خیلی مسخره بود.",
)

_ASSISTANT_TEXTS = (
    "خلاصه‌اش اینه که اول زمان رو مشخص کنید و بعد کارها رو تقسیم کنید.",
    "برای اینکه قاطی نشه، جوابم به همین پیام متصل می‌مونه.",
    "اگر منظورت ادامه همین رشته است، مرحله بعد را کوتاه و مشخص بگو.",
)


def _reset_sqlite(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def _event_dict(message: IncomingMessage, role: str, *, abusive: bool = False) -> dict[str, Any]:
    return {
        "message_id": int(message.message_id),
        "reply_to_message_id": message.reply_to_message_id,
        "sender_id": int(message.sender_id),
        "sender_label": message.sender_label,
        "sender_username": message.sender_username,
        "role": role,
        "text": message.text,
        "abusive": abusive,
        "platform": message.platform,
        "account_scope": message.account_scope,
        "chat_id": int(message.chat_id),
    }


async def run_simulation(
    output_dir: str | Path,
    *,
    message_count: int = 1000,
    seed: int = 1389,
    config_path: str | Path = Path(__file__).resolve().parents[1] / "config" / "zero.example.yaml",
) -> dict[str, Any]:
    if message_count < 120:
        raise ValueError("message_count must be at least 120 for the reply-chain scenarios")

    started = time.perf_counter()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    main_db = output / "simulation.db"
    memory_v3_db = output / "simulation-v3.db"
    for db in (main_db, memory_v3_db):
        _reset_sqlite(db)
    for artifact in ("group-simulation-report.json", "messages.jsonl", "members.json"):
        path = output / artifact
        if path.exists():
            path.unlink()

    previous_home = os.environ.get("ZERO_HOME")
    previous_v3 = os.environ.get("ZERO_MEMORY_V3_DB")
    os.environ["ZERO_HOME"] = str(output / "runtime")
    os.environ["ZERO_MEMORY_V3_DB"] = str(memory_v3_db)
    try:
        cfg = ZeroConfig.load(config_path)
        cfg = cfg.model_copy(update={"memory": cfg.memory.model_copy(update={"db_path": str(main_db)})})
        store = ZeroStore(str(main_db))
        brain = ZeroBrain(cfg, store, IndependentRouter(cfg))
    finally:
        _restore_env("ZERO_HOME", previous_home)
        _restore_env("ZERO_MEMORY_V3_DB", previous_v3)

    brain.zero_user_id = ZERO_USER_ID
    rng = random.Random(seed)
    members = build_members()
    abusive_members = tuple(member for member in members if member.abusive)
    assistant_inputs = set(range(40, message_count + 1, 40)) | set(range(100, 112))
    forced_members = {
        50: members[0],
        51: members[1],
        52: members[2],
        60: members[3],
        61: members[4],
        62: members[5],
    }

    events: dict[int, dict[str, Any]] = {}
    human_messages: dict[int, IncomingMessage] = {}
    recent_event_ids: list[int] = []
    abusive_message_count = 0
    abusive_sender_ids: set[int] = set()
    feedback_recorded: set[int] = set()

    with (output / "messages.jsonl").open("w", encoding="utf-8") as corpus:
        for message_id in range(1, message_count + 1):
            abusive = False
            if message_id <= len(members):
                member = members[message_id - 1]
                text = f"بچه‌ها ترجیح می‌دم {member.preference}، اگه چیزی گرفتید همین رو بگیرید."
            elif message_id in forced_members:
                member = forced_members[message_id]
                text = f"پیام زنجیره هویتی شماره {message_id} از {member.label}."
            elif 100 <= message_id <= 111:
                member = members[(message_id - 100) % len(members)]
                text = f"ادامه زنجیره ریپلای مرحله {message_id - 99}."
            elif message_id % 29 == 0:
                abuse_index = (message_id // 29 - 1) % len(abusive_members)
                member = abusive_members[abuse_index]
                text = _ABUSIVE_TEXTS[abuse_index]
                abusive = True
            else:
                member = members[rng.randrange(len(members))]
                text = _NORMAL_TEMPLATES[rng.randrange(len(_NORMAL_TEMPLATES))]

            if message_id in (50, 60, 100):
                reply_to = None
            elif message_id in (51, 52, 61, 62):
                reply_to = message_id - 1
            elif 101 <= message_id <= 111:
                reply_to = 1_000_000 + message_id - 1
            elif message_id > len(members) and recent_event_ids and rng.random() < 0.34:
                reply_to = rng.choice(recent_event_ids[-16:])
            else:
                reply_to = None

            parent = events.get(int(reply_to or 0))
            message = IncomingMessage(
                chat_id=CHAT_ID,
                chat_title="Zero Simulation Group",
                sender_id=member.user_id,
                sender_label=member.label,
                sender_username=member.username,
                sender_display_name=member.display_name,
                text=text,
                message_id=message_id,
                reply_to_message_id=reply_to,
                reply_text=str(parent.get("text", "")) if parent else "",
                reply_sender_id=int(parent["sender_id"]) if parent else None,
                reply_sender_label=str(parent["sender_label"]) if parent else "",
                reply_sender_is_bot=bool(parent and parent["role"] == "assistant"),
                reply_to_zero=bool(parent and parent["role"] == "assistant"),
                mention_zero=message_id in assistant_inputs,
                trace_id=f"sim-{message_id}",
                platform="telegram",
                account_scope=ACCOUNT_SCOPE,
            )
            human_messages[message_id] = message
            await brain.remember_message(message)
            await brain.memory_v3.observe(message)

            event = _event_dict(message, "user", abusive=abusive)
            events[message_id] = event
            recent_event_ids.append(message_id)
            corpus.write(json.dumps(event, ensure_ascii=False) + "\n")

            if abusive:
                abusive_message_count += 1
                abusive_sender_ids.add(member.user_id)
                if member.user_id not in feedback_recorded:
                    await brain.social_awareness.record_feedback(CHAT_ID, member.user_id, text)
                    feedback_recorded.add(member.user_id)

            if message_id in assistant_inputs:
                assistant_message_id = 1_000_000 + message_id
                assistant_text = _ASSISTANT_TEXTS[message_id % len(_ASSISTANT_TEXTS)]
                await brain.remember_reply(message, assistant_text, telegram_message_id=assistant_message_id)
                assistant = IncomingMessage(
                    chat_id=CHAT_ID,
                    chat_title="Zero Simulation Group",
                    sender_id=ZERO_USER_ID,
                    sender_label="Zero",
                    sender_display_name="Zero",
                    text=assistant_text,
                    sender_is_bot=True,
                    message_id=assistant_message_id,
                    reply_to_message_id=message_id,
                    thread_id=message.thread_id,
                    trace_id=f"sim-zero-{message_id}",
                    platform="telegram",
                    account_scope=ACCOUNT_SCOPE,
                )
                assistant_event = _event_dict(assistant, "assistant")
                events[assistant_message_id] = assistant_event
                recent_event_ids.append(assistant_message_id)
                corpus.write(json.dumps(assistant_event, ensure_ascii=False) + "\n")

    profiles: list[dict[str, Any]] = []
    for member in members:
        profile = await store.get_profile(CHAT_ID, member.user_id)
        if profile:
            profiles.append(profile)

    isolation_failures: list[dict[str, Any]] = []
    for member in members:
        probe = IncomingMessage(
            chat_id=CHAT_ID,
            chat_title="Zero Simulation Group",
            sender_id=member.user_id,
            sender_label=member.label,
            sender_username=member.username,
            sender_display_name=member.display_name,
            text="ترجیح من چی بود؟",
            message_id=2_000_000 + member.user_id,
            platform="telegram",
            account_scope=ACCOUNT_SCOPE,
        )
        context, _ = await brain.memory_v3.context(probe)
        missing = member.preference not in context
        leaked = [other.user_id for other in members if other.user_id != member.user_id and other.preference in context]
        if missing or leaked:
            isolation_failures.append({"user_id": member.user_id, "missing_own": missing, "leaked_user_ids": leaked})

    direct_context = await brain.memory_v3.thread_context(human_messages[52], max_depth=8)
    cross_context = await brain.memory_v3.thread_context(human_messages[62], max_depth=8)
    long_probe = IncomingMessage(
        chat_id=CHAT_ID,
        chat_title="Zero Simulation Group",
        sender_id=members[-1].user_id,
        sender_label=members[-1].label,
        text="ادامه می‌دیم؟",
        message_id=3_000_000,
        reply_to_message_id=1_000_111,
        platform="telegram",
        account_scope=ACCOUNT_SCOPE,
    )
    long_context = await brain.memory_v3.thread_context(long_probe, max_depth=16)
    nearest_human = next((row.sender_id for row in long_context.ancestors if row.role == "user"), None)

    social_state = await brain.social_awareness.group_state(CHAT_ID)
    conflict_decision = brain.social_awareness.evaluate(
        IncomingMessage(
            chat_id=CHAT_ID,
            chat_title="Zero Simulation Group",
            sender_id=1002,
            sender_label="علی",
            text="زیرو دعوا نکنید، خفه شو.",
            mention_zero=True,
            trace_id="sim-conflict",
        )
    )

    with brain.memory_v3._conn() as conn:
        stored_messages = int(conn.execute("SELECT count(*) FROM memory_v3_messages WHERE chat_id=?", (CHAT_ID,)).fetchone()[0])
        human_sender_count = int(conn.execute("SELECT count(DISTINCT sender_id) FROM memory_v3_messages WHERE chat_id=? AND role='user'", (CHAT_ID,)).fetchone()[0])
        reply_edge_count = int(conn.execute("SELECT count(*) FROM memory_v3_messages WHERE chat_id=? AND reply_to_message_id IS NOT NULL", (CHAT_ID,)).fetchone()[0])
        personal_memory_owner_count = int(conn.execute("SELECT count(DISTINCT owner_user_id) FROM memory_v3_items WHERE chat_id=? AND scope='personal' AND status='active'", (CHAT_ID,)).fetchone()[0])
        memory_integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    with store._conn() as conn:
        main_integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])

    messages_jsonl_lines = sum(1 for _ in (output / "messages.jsonl").open("r", encoding="utf-8"))
    exact_duplicate_profiles = sum(1 for profile in profiles if profile.get("display_name") == "علی")
    cross_script_profiles = sum(1 for profile in profiles if profile.get("display_name") in {"رضا", "Reza", "REZA"})

    checks = {
        "incoming_count": message_count == 1000,
        "member_count": len(members) == 15,
        "human_identity_count": human_sender_count == 15 and len(profiles) == 15,
        "exact_duplicate_identity": exact_duplicate_profiles == 3,
        "cross_script_identity": cross_script_profiles == 3,
        "abusive_distribution": len(abusive_sender_ids) == 4 and abusive_message_count >= 20,
        "memory_owner_count": personal_memory_owner_count == 15,
        "memory_isolation": not isolation_failures,
        "direct_reply_chain": [row.sender_id for row in direct_context.ancestors[:2]] == [1002, 1001],
        "cross_script_reply_chain": [row.sender_id for row in cross_context.ancestors[:2]] == [1005, 1004],
        "long_reply_chain": len(long_context.ancestors) == 16,
        "nearest_human_after_zero": nearest_human == human_messages[111].sender_id,
        "reply_edges": reply_edge_count >= 30,
        "social_feedback": int(social_state.get("social_reputation", 0)) < 0,
        "conflict_silence": conflict_decision.should_ignore and not conflict_decision.should_reply,
        "integrity": main_integrity == "ok" and memory_integrity == "ok",
        "corpus_matches_storage": messages_jsonl_lines == stored_messages,
    }

    report: dict[str, Any] = {
        "passed": all(checks.values()),
        "checks": checks,
        "seed": seed,
        "incoming_messages": message_count,
        "stored_messages": stored_messages,
        "assistant_messages": stored_messages - message_count,
        "messages_jsonl_lines": messages_jsonl_lines,
        "member_count": len(members),
        "human_sender_count": human_sender_count,
        "profile_count": len(profiles),
        "exact_duplicate_profiles": exact_duplicate_profiles,
        "cross_script_profiles": cross_script_profiles,
        "abusive_member_count": len(abusive_sender_ids),
        "clean_member_count": len(members) - len(abusive_sender_ids),
        "abusive_message_count": abusive_message_count,
        "personal_memory_owner_count": personal_memory_owner_count,
        "memory_isolation_failures": isolation_failures,
        "direct_reply_ancestor_ids": [row.sender_id for row in direct_context.ancestors],
        "cross_script_reply_ancestor_ids": [row.sender_id for row in cross_context.ancestors],
        "long_chain_depth": len(long_context.ancestors),
        "long_chain_roles": [row.role for row in long_context.ancestors],
        "nearest_human_after_zero": nearest_human,
        "nearest_human_after_zero_matches": nearest_human == human_messages[111].sender_id,
        "reply_edge_count": reply_edge_count,
        "social_reputation": int(social_state.get("social_reputation", 0)),
        "social_confidence": float(social_state.get("social_confidence", 1.0)),
        "conflict_should_ignore": bool(conflict_decision.should_ignore and not conflict_decision.should_reply),
        "sqlite_integrity": {"main": main_integrity, "memory_v3": memory_integrity},
        "duration_seconds": round(time.perf_counter() - started, 3),
        "artifacts": {
            "main_db": str(main_db),
            "memory_v3_db": str(memory_v3_db),
            "messages": str(output / "messages.jsonl"),
            "members": str(output / "members.json"),
            "report": str(output / "group-simulation-report.json"),
        },
    }

    (output / "members.json").write_text(
        json.dumps([asdict(member) for member in members], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "group-simulation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not report["passed"]:
        failed = [name for name, value in checks.items() if not value]
        raise AssertionError(f"simulation checks failed: {failed}")
    return report
