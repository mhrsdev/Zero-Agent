from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib import parse, request


def _validate_private_file(path: Path, label: str) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} not found: {path}") from exc
    if mode & 0o077:
        raise PermissionError(f"{label} permissions must not expose group/world bits: {path}")


def load_bot_token(bot_env_file: str) -> str:
    path = Path(bot_env_file)
    _validate_private_file(path, "bot token file")
    content = path.read_text(encoding='utf-8').splitlines()
    for line in content:
        if line.startswith('BOT_TOKEN='):
            token = line.split('=', 1)[1].strip()
            if token:
                return token
    raise RuntimeError('BOT_TOKEN not found in bot.env')


def send_bot_message(token: str, chat_id: int, text: str) -> None:
    data = parse.urlencode({'chat_id': str(chat_id), 'text': text}).encode('utf-8')
    req = request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
    with request.urlopen(req, timeout=20) as _:
        pass
