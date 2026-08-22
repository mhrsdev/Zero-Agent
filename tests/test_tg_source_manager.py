from pathlib import Path

from zero.tg_source_manager import extract_candidates, merge_manifest, TelegramSourceManager


def test_extract_and_merge_public_links():
    rows = extract_candidates([{'url': 'https://t.me/s/example/1', 'title': 'x', 'content': ''}], 'technology_ai')
    assert rows[0]['username'] == 'example'
    merged = merge_manifest([{'username': 'example', 'category': 'old'}], rows)
    assert len(merged) == 1
    assert merged[0]['category'] == 'old'


def test_state_files_are_private(tmp_path):
    from zero.fsprivacy import path_is_private
    m = TelegramSourceManager(object(), tmp_path/'manifest.json', tmp_path/'state.json')
    m.save_manifest([{'username': 'example'}])
    m.save_state({'joined': {}})
    assert path_is_private(tmp_path / 'manifest.json')
    assert path_is_private(tmp_path / 'state.json')
