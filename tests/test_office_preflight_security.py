from __future__ import annotations

import os
from pathlib import Path
import zipfile

import pytest

from zero.config import OfficeLimitsConfig
from zero.office.preflight import PreflightError, inspect_ooxml
from zero.office.workspace import WorkspaceError, create_workspace, safe_path, sanitize_filename


CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
}
MIMES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def make_ooxml(path: Path, fmt: str, *, extra: dict[str, bytes | str] | None = None, content_type: str | None = None):
    main_part = {"docx": "/word/document.xml", "xlsx": "/xl/workbook.xml", "pptx": "/ppt/presentation.xml"}[fmt]
    types = f'''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="{main_part}" ContentType="{content_type or CONTENT_TYPES[fmt]}"/></Types>'''
    defaults = {
        "docx": {"word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>سلام دنیا</w:t></w:r></w:p></w:body></w:document>'},
        "xlsx": {
            "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="نمرات" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>نام</t></is></c><c r="B1"><f>SUM(B2:B3)</f><v>3</v></c></row></sheetData></worksheet>',
        },
        "pptx": {
            "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst><p:sldId id="1"/></p:sldIdLst></p:presentation>',
            "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>عنوان فارسی</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
            "ppt/notesSlides/notesSlide1.xml": '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>یادداشت سخنران</a:t></p:notes>',
        },
    }[fmt]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        for name, value in defaults.items():
            archive.writestr(name, value)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx"])
def test_valid_ooxml_type_is_detected_from_extension_mime_magic_and_content_types(tmp_path, fmt):
    path = tmp_path / f"safe.{fmt}"
    make_ooxml(path, fmt)
    report = inspect_ooxml(path, declared_mime=MIMES[fmt], limits=OfficeLimitsConfig())
    assert report.format == fmt
    assert report.detected_mime == MIMES[fmt]
    assert report.extracted_characters == len(report.normalized_text)
    assert "فارسی" in report.normalized_text or "سلام" in report.normalized_text or "نمرات" in report.normalized_text


def test_word_count_includes_headers_footers_tables_and_textboxes(tmp_path):
    path = tmp_path / "input.docx"
    make_ooxml(path, "docx", extra={
        "word/header1.xml": '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:t>سربرگ</w:t></w:hdr>',
        "word/footer1.xml": '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:t>پابرگ</w:t></w:ftr>',
        "word/textbox.xml": '<w:txbx xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:t>جعبه متن</w:t></w:txbx>',
    })
    report = inspect_ooxml(path, declared_mime=MIMES["docx"], limits=OfficeLimitsConfig())
    assert all(part in report.normalized_text for part in ("سربرگ", "پابرگ", "جعبه متن"))


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("../../etc/passwd.docx", "passwd.docx"),
        ("report;$(id).docx", "report_id.docx"),
        ("\u202eexe.docx", "exe.docx"),
        ("a" * 400 + ".docx", "a" * 115 + ".docx"),
    ],
)
def test_filename_sanitization(filename, expected):
    assert sanitize_filename(filename) == expected


@pytest.mark.parametrize("name", ["../other/output.docx", "/etc/passwd", "a\x00.docx"])
def test_safe_path_rejects_traversal_absolute_and_null(tmp_path, name):
    with pytest.raises(WorkspaceError):
        safe_path(tmp_path, name)


def test_safe_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "job"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError):
        safe_path(root, "link/escape.docx")


def test_workspace_isolated_and_input_is_read_only(tmp_path):
    workspace = create_workspace(tmp_path, chat_id=-100, job_id="abc-123")
    assert set(workspace.directories) == {"input", "working", "preview", "output", "logs"}
    assert workspace.root == (tmp_path / "-100" / "abc-123").resolve()
    source = tmp_path / "source.docx"
    source.write_bytes(b"PK-test")
    copied = workspace.install_input(source, "unsafe;name.docx")
    assert copied.parent == workspace.input
    assert copied.name == "unsafe_name.docx"
    assert copied.stat().st_mode & 0o222 == 0


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("fake.xlsx", MIMES["xlsx"], "format_mismatch"),
        ("fake.docm", "application/vnd.ms-word.document.macroEnabled.12", "unsupported_format"),
        ("fake.doc", "application/msword", "unsupported_format"),
    ],
)
def test_extension_spoof_macro_and_legacy_are_rejected(tmp_path, name, mime, expected):
    path = tmp_path / name
    make_ooxml(path, "docx")
    with pytest.raises(PreflightError, match=expected):
        inspect_ooxml(path, declared_mime=mime, limits=OfficeLimitsConfig())


def test_malformed_zip_is_rejected(tmp_path):
    path = tmp_path / "bad.docx"
    path.write_bytes(b"PK\x03\x04not-a-real-zip")
    with pytest.raises(PreflightError, match="malformed_zip"):
        inspect_ooxml(path, declared_mime=MIMES["docx"], limits=OfficeLimitsConfig())


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"../escape": "x"}, "archive_traversal"),
        ({"word/vbaProject.bin": b"macro"}, "macro_enabled"),
        ({"word/embeddings/oleObject1.bin": b"MZ"}, "embedded_object"),
        ({"word/_rels/document.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" TargetMode="External" Target="https://evil.invalid/x"/></Relationships>'}, "external_relationship"),
        ({"word/custom.xml": '<!DOCTYPE x [<!ENTITY y "boom">]><x>&y;</x>'}, "xml_entity"),
    ],
)
def test_dangerous_archive_features_are_rejected(tmp_path, extra, code):
    path = tmp_path / "bad.docx"
    make_ooxml(path, "docx", extra=extra)
    with pytest.raises(PreflightError, match=code):
        inspect_ooxml(path, declared_mime=MIMES["docx"], limits=OfficeLimitsConfig())


def test_zip_bomb_ratio_and_uncompressed_limit_are_rejected(tmp_path):
    path = tmp_path / "bomb.docx"
    make_ooxml(path, "docx", extra={"word/huge.xml": "A" * 2_000_000})
    with pytest.raises(PreflightError, match="compression_ratio"):
        inspect_ooxml(path, declared_mime=MIMES["docx"], limits=OfficeLimitsConfig(max_compression_ratio=10))
    with pytest.raises(PreflightError, match="uncompressed_size"):
        inspect_ooxml(path, declared_mime=MIMES["docx"], limits=OfficeLimitsConfig(max_uncompressed_size_mb=1, max_compression_ratio=10_000))


def test_dangerous_excel_formula_is_rejected_but_safe_formula_is_counted(tmp_path):
    safe = tmp_path / "safe.xlsx"
    make_ooxml(safe, "xlsx")
    assert inspect_ooxml(safe, declared_mime=MIMES["xlsx"], limits=OfficeLimitsConfig()).formulas == 1
    bad = tmp_path / "bad.xlsx"
    make_ooxml(bad, "xlsx", extra={"xl/worksheets/sheet2.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f>WEBSERVICE("https://evil")</f></c></row></sheetData></worksheet>'})
    with pytest.raises(PreflightError, match="dangerous_formula"):
        inspect_ooxml(bad, declared_mime=MIMES["xlsx"], limits=OfficeLimitsConfig())


def test_structural_limits_apply_independently_of_character_count(tmp_path):
    path = tmp_path / "wide.xlsx"
    make_ooxml(path, "xlsx", extra={"xl/worksheets/sheet2.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="10001"><c r="A10001"><v>1</v></c></row></sheetData></worksheet>'})
    with pytest.raises(PreflightError, match="row_limit"):
        inspect_ooxml(path, declared_mime=MIMES["xlsx"], limits=OfficeLimitsConfig(max_rows_per_sheet=10_000))
