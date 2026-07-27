from types import SimpleNamespace

from telethon.tl import types

from zero.vision import is_gif_media, is_video_media, media_mime_type


def test_telegram_animated_mp4_is_gif_and_video():
    event = SimpleNamespace(
        media=True,
        photo=None,
        document=SimpleNamespace(
            mime_type='video/mp4',
            attributes=[types.DocumentAttributeAnimated()],
        ),
    )
    assert is_gif_media(event)
    assert is_video_media(event)
    assert media_mime_type(event) == 'video/mp4'


def test_webp_sticker_keeps_image_mime():
    event = SimpleNamespace(
        media=True,
        photo=None,
        document=SimpleNamespace(mime_type='image/webp', attributes=[]),
    )
    assert not is_gif_media(event)
    assert not is_video_media(event)
    assert media_mime_type(event) == 'image/webp'
