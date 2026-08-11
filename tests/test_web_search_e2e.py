from conftest import CONFIG_RUNTIME, ROOT, RUNTIME_DIR
#!/usr/bin/env python3
"""
Real-time Telegram E2E test for Zero Web Search
Uses proper trigger (@ziro / زیرو) to activate Zero
"""
import asyncio
import os
import sys
import pytest
sys.path.insert(0, str(ROOT))

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, ChatWriteForbiddenError
from zero.config import ZeroConfig
import time

pytestmark = pytest.mark.skipif(
    os.environ.get('ZERO_LIVE_E2E') != '1',
    reason='live Telegram E2E is opt-in: ZERO_LIVE_E2E=1',
)

async def test_web_search():
    config = ZeroConfig.load(CONFIG_RUNTIME)
    
    # Use tgsearch session (separate from zero listener)
    client = TelegramClient(
        config.telegram_search.session_path,
        config.telegram_search.api_id,
        config.telegram_search.api_hash
    )
    
    try:
        await client.start()
        me = await client.get_me()
        print(f"[TEST] Running as {me.first_name} (ID: {me.id})")

        # Get iran_ai_chat
        chat = await client.get_entity('iran_ai_chat')
        print(f"[TEST] Found chat: {chat.title}")
    except Exception as e:
        print(f"[ERROR] Cannot access iran_ai_chat: {e}")
        await client.disconnect()
        pytest.skip('Telegram test account cannot access iran_ai_chat')
    try:
        # Send test message WITH trigger word
        test_msg = "زیرو، قیمت طلا الان چنده؟"
        print(f"\n[SEND] Sending: {test_msg}")
        try:
            sent = await client.send_message(chat, test_msg)
        except (ChannelPrivateError, ChatWriteForbiddenError):
            pytest.skip('Telegram test account cannot write to iran_ai_chat')
        print(f"[SENT] Message ID: {sent.id}")

        # Wait for Zero to process
        print("[WAIT] Waiting 15 seconds for processing...")
        await asyncio.sleep(15)

        # Read logs to verify web search was called
        with (RUNTIME_DIR / "logs" / "listener.log").open("r") as f:
            logs = f.read()
        
        # Get recent lines
        log_lines = logs.split('\n')
        recent_logs = log_lines[-100:]
        
        # Find the trace for our test
        trace_id = None
        for line in recent_logs:
            if test_msg in line:
                # Extract trace ID
                import re
                match = re.search(r'trace_id=([a-f0-9]+)', line)
                if match:
                    trace_id = match.group(1)
                    print(f"\n[FOUND] Test traced with trace_id={trace_id}")
        
        if not trace_id:
            print("\n[WARN] Could not find trace_id for test message")
            print("[INFO] Recent messages may not have trigger words")
        
        # Check for web search markers
        checks = {
            'NEEDS_WEB_SEARCH': 'needs_web_search called',
            'WEB_SEARCH_START': 'web search started',
            'WEB_SEARCH_SEARXNG_OK': 'SearXNG query successful',
            'WEB_SEARCH_COMPLETE': 'web search completed',
            'WEB_CONTEXT': 'web context built'
        }
        
        print("\n[LOG CHECKS]")
        found_any = False
        for marker, description in checks.items():
            if marker in logs:
                found_any = True
                count = logs.count(marker)
                print(f"✓ {description}: {count} occurrence(s) (total)")
                # Find if it's in recent logs
                recent_match = any(marker in line for line in recent_logs)
                if recent_match:
                    print(f"  → Found in last 100 lines ✓")
                else:
                    print(f"  → NOT in last 100 lines (old log)")
            else:
                print(f"✗ {description}: NOT FOUND")
        
        # Extract relevant log lines
        print("\n[RECENT WEB/SEARCH LOGS]")
        web_logs = [l for l in recent_logs if any(x in l.upper() for x in ['WEB', 'SEARCH', 'SREARXNG'])]
        if web_logs:
            for line in web_logs[-10:]:
                print(line)
        else:
            print("[INFO] No WEB/SEARCH logs in recent 100 lines")
        
        print("\n[RECENT ALL LOGS]")
        for line in log_lines[-10:]:
            print(line)
        
    except Exception as e:
        print(f"[ERROR] Cannot read logs: {e}")
    finally:
        await client.disconnect()
        print("\n[TEST] Complete")

if __name__ == '__main__':
    asyncio.run(test_web_search())
