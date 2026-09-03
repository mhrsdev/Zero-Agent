from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_has_no_admin_admin_literal_bypass():
    files = [
        ROOT / "zero" / "panel_store.py",
        ROOT / "zero" / "panel_api.py",
    ]
    blob = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert 'password == "Admin"' not in blob
    assert "password == 'Admin'" not in blob
    assert "allow_weak" not in blob


def test_router_has_no_urlopen():
    source = (ROOT / "zero" / "router.py").read_text(encoding="utf-8")
    assert "urlopen" not in source
