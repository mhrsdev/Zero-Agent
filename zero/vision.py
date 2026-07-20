from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import ZeroConfig

logger = logging.getLogger('zero.vision')


class VisionRateLimiter:
    def __init__(self, config: ZeroConfig, store):
        self.config = config
        self.store = store
        self._image_counts: dict[int, list[float]] = {}
        self._gif_counts: dict[int, list[float]] = {}
        self._last_request: dict[int, float] = {}
        self._initialized = False

    async def _ensure_initialized(self, user_id: int) -> None:
        """Load existing counts from database on first use."""
        if self._initialized:
            return
        # Could preload all users here, but lazy load per user is fine
        self._initialized = True

    def _clean_old(self, counts: dict[int, list[float]], now: float) -> None:
        window = self.config.vision.window_seconds
        for user_id in list(counts.keys()):
            counts[user_id] = [ts for ts in counts[user_id] if now - ts < window]
            if not counts[user_id]:
                del counts[user_id]

    async def check_image_limit(self, user_id: int) -> tuple[bool, str]:
        now = time.time()
        # Check in-memory first (fast path)
        self._clean_old(self._image_counts, now)
        count = len(self._image_counts.get(user_id, []))
        last = self._last_request.get(user_id, 0)
        if now - last < self.config.vision.cooldown_seconds:
            return False, f"Cooldown ({int(self.config.vision.cooldown_seconds - (now - last))}s)"
        allowed, total_count = await self.store.try_reserve_rate_event(user_id, 'image', self.config.vision.window_seconds, self.config.vision.max_images_per_user_per_window, vision=True)
        if not allowed:
            return False, f"Limited images ({total_count}/{self.config.vision.max_images_per_user_per_window}) in window"
        self._image_counts.setdefault(user_id, []).append(now)
        self._last_request[user_id] = now
        return True, ""

    async def check_gif_limit(self, user_id: int) -> tuple[bool, str]:
        now = time.time()
        self._clean_old(self._gif_counts, now)
        count = len(self._gif_counts.get(user_id, []))
        last = self._last_request.get(user_id, 0)
        if now - last < self.config.vision.cooldown_seconds:
            return False, f"Cooldown ({int(self.config.vision.cooldown_seconds - (now - last))}s)"
        allowed, total_count = await self.store.try_reserve_rate_event(user_id, 'gif', self.config.vision.window_seconds, self.config.vision.max_gifs_per_user_per_window, vision=True)
        if not allowed:
            return False, f"Limited GIFs ({total_count}/{self.config.vision.max_gifs_per_user_per_window}) in window"
        self._gif_counts.setdefault(user_id, []).append(now)
        self._last_request[user_id] = now
        return True, ""

    async def record_image(self, user_id: int) -> None:
        now = time.time()
        self._image_counts.setdefault(user_id, []).append(now)
        self._last_request[user_id] = now
        await self.store.add_vision_rate_event(user_id, 'image')

    async def record_gif(self, user_id: int) -> None:
        now = time.time()
        self._gif_counts.setdefault(user_id, []).append(now)
        self._last_request[user_id] = now
        await self.store.add_vision_rate_event(user_id, 'gif')


async def download_media(event, config: ZeroConfig, max_size_mb: int = 10) -> str | None:
    """Download media from Telegram event to temp file. Returns local path or None."""
    try:
        if not event.media:
            return None
        max_bytes = max_size_mb * 1024 * 1024
        file = await event.download_media(file=bytes)
        if isinstance(file, (bytes, bytearray)):
            if len(file) > max_bytes:
                return None
            mime = media_mime_type(event)
            suffix = mimetypes.guess_extension(mime or '') or Path(getattr(getattr(event, 'file', None), 'name', '') or 'media').suffix.lower()
            if suffix == '.jpe': suffix = '.jpg'
            if suffix not in config.vision.allowed_extensions and mime not in {'video/mp4', 'video/webm', 'application/x-tgsticker'}:
                suffix = ".jpg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(file)
                return f.name
        elif isinstance(file, str):
            if os.path.getsize(file) > max_bytes:
                return None
            return file
        return None
    except Exception:
        return None


def is_image_media(event) -> bool:
    if not event.media:
        return False
    from telethon.tl.types import DocumentAttributeImageSize, DocumentAttributeFilename
    if event.photo:
        return True
    if event.document:
        mime = getattr(event.document, 'mime_type', '') or ''
        if mime.startswith('image/'):
            return True
        for attr in getattr(event.document, 'attributes', []):
            if isinstance(attr, DocumentAttributeImageSize):
                return True
            if isinstance(attr, DocumentAttributeFilename):
                ext = Path(attr.file_name).suffix.lower()
                if ext in {'.jpg', '.jpeg', '.png', '.webp', '.gif'}:
                    return True
    return False


def is_gif_media(event) -> bool:
    if not event.document:
        return False
    mime = getattr(event.document, 'mime_type', '') or ''
    if mime == 'image/gif':
        return True
    for attr in getattr(event.document, 'attributes', []):
        from telethon.tl.types import DocumentAttributeAnimated, DocumentAttributeFilename
        if isinstance(attr, DocumentAttributeAnimated):
            return True
        if isinstance(attr, DocumentAttributeFilename):
            if attr.file_name.lower().endswith('.gif'):
                return True
    return False


def is_video_media(event) -> bool:
    if not getattr(event, 'document', None):
        return False
    if (getattr(event.document, 'mime_type', '') or '').startswith('video/'):
        return True
    from telethon.tl.types import DocumentAttributeVideo
    return any(isinstance(attr, DocumentAttributeVideo) for attr in getattr(event.document, 'attributes', []))


def media_mime_type(event) -> str:
    if getattr(event, 'photo', None):
        return 'image/jpeg'
    return getattr(getattr(event, 'document', None), 'mime_type', '') or 'image/jpeg'


async def extract_video_frames(media_path: str, max_frames: int = 3) -> list[str]:
    """Extract representative JPEG frames so GIF/MP4 never becomes text-only input."""
    def _extract() -> list[str]:
        try:
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', media_path],
                capture_output=True, text=True, timeout=10, check=False,
            )
            try: duration = max(0.0, float((probe.stdout or '').strip()))
            except ValueError: duration = 0.0
            if duration <= 0:
                offsets = [0.0]
            elif max_frames <= 1:
                offsets = [min(0.2, max(0.0, duration / 2))]
            else:
                offsets = sorted(set([0.0, duration / 2, max(0.0, duration - 0.15)]))[:max_frames]
            paths=[]
            for offset in offsets:
                fd, frame = tempfile.mkstemp(suffix='.jpg')
                os.close(fd)
                result = subprocess.run(
                    ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{offset:.3f}',
                     '-i', media_path, '-frames:v', '1', '-vf', 'scale=1280:-2', frame],
                    capture_output=True, timeout=20, check=False,
                )
                if result.returncode == 0 and os.path.getsize(frame) > 0:
                    paths.append(frame)
                else:
                    Path(frame).unlink(missing_ok=True)
            return paths
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
    frames = await asyncio.to_thread(_extract)
    logger.info('VISION_FRAMES_EXTRACTED source=%s count=%s', Path(media_path).suffix.lower() or 'media', len(frames))
    return frames


def is_sticker_media(event) -> bool:
    if not event.document:
        return False
    for attr in getattr(event.document, 'attributes', []):
        from telethon.tl.types import DocumentAttributeSticker
        if isinstance(attr, DocumentAttributeSticker):
            return True
    return False


async def analyze_image_with_gemini(image_path: str | list[str], prompt: str, api_key: str, model: str, mime_type: str | None = None) -> str:
    """Analyze image using Gemini Vision API (async, non-blocking)."""
    import base64
    import json
    import urllib.request

    paths = [image_path] if isinstance(image_path, str) else list(image_path)
    parts = [{"text": prompt}]
    for path in paths[:4]:
        with open(path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        parts.append({"inline_data": {"mime_type": mime_type or mimetypes.guess_type(path)[0] or "image/jpeg", "data": b64}})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{
            "parts": [
                *parts,
            ]
        }],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 500}
    }

    def _call():
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
        return data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()

    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.warning('VISION_PROVIDER_FAILED mime=%s exception_type=%s', mime_type or 'unknown', type(e).__name__)
        return ""


def build_vision_prompt(*, question: str, caption: str | None = None) -> str:
    parts = [
        "تو Zero هستی، یه دوست و رفیقِ گروه. این عکس، GIF یا ویدئو را واقعاً بررسی کن و یه پاسخ کوتاه، طبیعی و فارسی بده.",
        "برای GIF/ویدئو فقط بر اساس فریم‌ها و محتوای قابل مشاهده جواب بده؛ اگر حرکت یا محتوای کافی قابل بررسی نیست، صادقانه بگو.",
        "اگر عکس حاویزسی اسکرین‌شات یا کد/ارور هست، توضیح بده چی هست و اگه میشه راه‌حل بده.",
        "اگر میم یا جوک هست، راجع بهش تیکه بزن.",
        "اگر متن فارسی/انگلسی داخل عکسه (OCR)، استخراج کن.",
        "اگه اطلاعات حساس (API key، پسورد، توکن) دیدی، فقط بگو 'اطلاعات حساس تشخیص داده شد' و نشون نده.",
        "اگر کاربر درباره خود تصویر سؤال کرده، به همان سؤال جواب بده و متن پیام قبلی را تکرار نکن.",
        "حداکثر ۳-۴ جمله.",
    ]
    if question:
        parts.append(f"سؤال کاربر: {question}")
    if caption:
        parts.append(f"کپشن تصویر: {caption}")
    return "\n".join(parts)


class VisionProcessor:
    def __init__(self, config: ZeroConfig, router_keys: list[str], store):
        self.config = config
        self.router_keys = router_keys
        self.store = store
        self.limiter = VisionRateLimiter(config, store)
        self._key_index = 0
        self._cached_enabled: bool | None = None  # force refresh on next check

    def _next_key(self) -> str:
        if not self.router_keys:
            return ""
        key = self.router_keys[self._key_index]
        self._key_index = (self._key_index + 1) % len(self.router_keys)
        return key

    async def is_tool_enabled(self) -> bool:
        """Check if vision is enabled: DB override > config YAML default."""
        db_val = await self.store.get_setting('vision_enabled')
        if db_val is not None and db_val not in ('null', 'None', ''):
            return db_val.lower() == 'true'
        return bool(self.config.vision.enabled)

    def invalidate_cache(self) -> None:
        """Force next is_tool_enabled() to re-read from DB."""
        self._cached_enabled = None

    async def analyze(self, image_path: str, *, question: str = '') -> str | None:
        """Compatibility adapter for stored static stickers."""
        api_key = self._next_key()
        if not api_key:
            logger.warning('VISION_UNAVAILABLE reason=missing_api_key')
            return None
        prompt = build_vision_prompt(question=question)
        return await analyze_image_with_gemini(image_path, prompt, api_key, self.config.vision.model, 'image/webp') or None

    async def process(self, event, *, question: str = "") -> str | None:
        if not await self.is_tool_enabled():
            logger.info('VISION_SKIPPED reason=disabled')
            return None

        user_id = event.sender_id
        caption = getattr(event, 'raw_text', '') or ''

        gif_or_video = is_gif_media(event) or is_video_media(event)
        if gif_or_video:
            ok, msg = await self.limiter.check_gif_limit(user_id)
            if not ok:
                logger.info('VISION_SKIPPED reason=rate_limit media=gif_or_video')
                return None
        else:
            ok, msg = await self.limiter.check_image_limit(user_id)
            if not ok:
                logger.info('VISION_SKIPPED reason=rate_limit media=image')
                return None

        local_path = await download_media(event, self.config, self.config.vision.max_file_size_mb)
        if not local_path:
            logger.warning('VISION_SKIPPED reason=download_failed media_mime=%s', media_mime_type(event))
            return None

        try:
            prompt = build_vision_prompt(question=question, caption=caption if caption else None)
            api_key = self._next_key()
            if not api_key:
                logger.warning('VISION_UNAVAILABLE reason=missing_api_key')
                return None
            frame_paths = await extract_video_frames(local_path) if gif_or_video else []
            analysis_paths = frame_paths or [local_path]
            analysis_mime = 'image/jpeg' if frame_paths else media_mime_type(event)
            logger.info('VISION_ANALYSIS_INPUT media_mime=%s frames=%s', media_mime_type(event), len(frame_paths))
            result = await analyze_image_with_gemini(analysis_paths, prompt, api_key, self.config.vision.model, analysis_mime)
            return result or None
        finally:
            try:
                if local_path and os.path.exists(local_path):
                    os.unlink(local_path)
                for frame in locals().get('frame_paths', []):
                    Path(frame).unlink(missing_ok=True)
            except Exception:
                pass