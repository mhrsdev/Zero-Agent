from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import zipfile
import xml.etree.ElementTree as ET

from zero.config import OfficeLimitsConfig
from .text import normalize_text


MIMES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
MAIN_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": "pptx",
}
UNSUPPORTED_EXTENSIONS = {".docm", ".xlsm", ".pptm", ".doc", ".xls", ".ppt"}
EXECUTABLE_EXTENSIONS = {".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".js", ".vbs", ".scr", ".msi", ".sh"}
DANGEROUS_FORMULA = re.compile(r"(?i)(WEBSERVICE|HYPERLINK|RTD|DDE|CALL|REGISTER\.ID|IMPORTXML|FILTERXML)\s*\(|\[[^\]]+\]|https?://|file://")
CELL_REF = re.compile(r"^([A-Z]{1,4})([1-9][0-9]*)$")


class PreflightError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreflightReport:
    format: str
    detected_mime: str
    normalized_text: str
    extracted_characters: int
    input_size_bytes: int
    uncompressed_size_bytes: int
    zip_entries: int
    slides: int = 0
    sheets: int = 0
    non_empty_cells: int = 0
    max_row: int = 0
    max_column: int = 0
    formulas: int = 0
    embedded_images: int = 0


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_number(letters: str) -> int:
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value


def _parse_xml(data: bytes) -> ET.Element:
    upper = data[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PreflightError("xml_entity")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise PreflightError("malformed_xml") from exc


def _safe_archive_name(name: str) -> None:
    value = name.replace("\\", "/")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\x00" in value:
        raise PreflightError("archive_traversal")


def inspect_ooxml(path: str | Path, *, declared_mime: str, limits: OfficeLimitsConfig) -> PreflightReport:
    file_path = Path(path)
    suffix = file_path.suffix.casefold()
    if suffix in UNSUPPORTED_EXTENSIONS:
        raise PreflightError("unsupported_format")
    if suffix not in {".docx", ".xlsx", ".pptx"}:
        raise PreflightError("unsupported_format")
    expected_format = suffix[1:]
    expected_mime = MIMES[expected_format]
    if declared_mime and declared_mime.casefold() != expected_mime.casefold():
        raise PreflightError("format_mismatch")
    try:
        size = file_path.stat().st_size
        if size <= 0 or size > limits.max_file_size_mb * 1024 * 1024:
            raise PreflightError("file_size")
        with file_path.open("rb") as stream:
            if stream.read(4) != b"PK\x03\x04":
                raise PreflightError("magic_bytes")
    except OSError as exc:
        raise PreflightError("unreadable_file") from exc

    try:
        archive = zipfile.ZipFile(file_path)
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise PreflightError("malformed_zip") from exc
    with archive:
        if len(infos) > limits.max_zip_entries:
            raise PreflightError("zip_entry_limit")
        total_uncompressed = 0
        names: set[str] = set()
        for info in infos:
            _safe_archive_name(info.filename)
            names.add(info.filename)
            if info.flag_bits & 0x1:
                raise PreflightError("encrypted_file")
            total_uncompressed += int(info.file_size)
            if total_uncompressed > limits.max_uncompressed_size_mb * 1024 * 1024:
                raise PreflightError("uncompressed_size")
            if info.file_size and info.file_size / max(1, info.compress_size) > limits.max_compression_ratio:
                raise PreflightError("compression_ratio")
            if info.filename.casefold().endswith(".xml") and info.file_size > limits.max_xml_entry_mb * 1024 * 1024:
                raise PreflightError("xml_entry_size")
            lower = info.filename.casefold()
            if "vbaproject" in lower or lower.endswith(("vbaproject.bin", "macrosheet.bin")):
                raise PreflightError("macro_enabled")
            if "/embeddings/" in f"/{lower}" or "oleobject" in lower:
                raise PreflightError("embedded_object")
            if Path(lower).suffix in EXECUTABLE_EXTENSIONS:
                raise PreflightError("embedded_executable")
        if "[Content_Types].xml" not in names:
            raise PreflightError("missing_content_types")
        content_types_data = archive.read("[Content_Types].xml")
        content_types = _parse_xml(content_types_data)
        actual_formats: set[str] = set()
        for node in content_types.iter():
            content_type = str(node.attrib.get("ContentType", ""))
            if "macroenabled" in content_type.casefold() or "vba" in content_type.casefold():
                raise PreflightError("macro_enabled")
            if content_type in MAIN_CONTENT_TYPES:
                actual_formats.add(MAIN_CONTENT_TYPES[content_type])
        if actual_formats != {expected_format}:
            raise PreflightError("format_mismatch")

        text_parts: list[str] = []
        slides = sheets = cells = formulas = max_row = max_column = 0
        images = sum(1 for name in names if "/media/" in f"/{name.casefold()}" and not name.endswith("/"))
        for info in infos:
            lower = info.filename.casefold()
            if not lower.endswith((".xml", ".rels")):
                continue
            data = archive.read(info)
            root = _parse_xml(data)
            if lower.endswith(".rels"):
                for node in root.iter():
                    if _local(node.tag) == "Relationship" and str(node.attrib.get("TargetMode", "")).casefold() == "external":
                        raise PreflightError("external_relationship")
                continue
            if expected_format == "docx" and lower.startswith("word/") and not lower.startswith("word/comments"):
                for node in root.iter():
                    if _local(node.tag) == "t" and node.text:
                        text_parts.append(node.text)
            elif expected_format == "pptx":
                if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", lower):
                    slides += 1
                if lower.startswith(("ppt/slides/", "ppt/notesslides/")):
                    for node in root.iter():
                        if _local(node.tag) == "t" and node.text:
                            text_parts.append(node.text)
            elif expected_format == "xlsx":
                if lower == "xl/workbook.xml":
                    for node in root.iter():
                        if _local(node.tag) == "sheet":
                            sheets += 1
                            if node.attrib.get("name"):
                                text_parts.append(str(node.attrib["name"]))
                elif lower == "xl/sharedstrings.xml":
                    for node in root.iter():
                        if _local(node.tag) == "t" and node.text:
                            text_parts.append(node.text)
                elif re.fullmatch(r"xl/worksheets/sheet[0-9]+\.xml", lower):
                    if not sheets:
                        sheets += 1
                    for node in root.iter():
                        local = _local(node.tag)
                        if local == "row":
                            try:
                                max_row = max(max_row, int(node.attrib.get("r", "0")))
                            except ValueError:
                                raise PreflightError("malformed_cell_reference")
                        elif local == "c":
                            ref = str(node.attrib.get("r", ""))
                            match = CELL_REF.fullmatch(ref)
                            if ref and not match:
                                raise PreflightError("malformed_cell_reference")
                            has_content = any(_local(child.tag) in {"v", "f", "is"} for child in node)
                            if has_content:
                                cells += 1
                            if match:
                                max_row = max(max_row, int(match.group(2)))
                                max_column = max(max_column, _column_number(match.group(1)))
                        elif local == "f" and node.text:
                            formulas += 1
                            text_parts.append(node.text)
                            if DANGEROUS_FORMULA.search(node.text):
                                raise PreflightError("dangerous_formula")
                        elif local in {"v", "t"} and node.text:
                            text_parts.append(node.text)
        if expected_format == "pptx" and slides > limits.max_slides:
            raise PreflightError("slide_limit")
        if expected_format == "xlsx":
            if sheets > limits.max_sheets:
                raise PreflightError("sheet_limit")
            if cells > limits.max_non_empty_cells:
                raise PreflightError("cell_limit")
            if max_row > limits.max_rows_per_sheet:
                raise PreflightError("row_limit")
            if max_column > limits.max_columns_per_sheet:
                raise PreflightError("column_limit")
            if formulas > limits.max_formulas:
                raise PreflightError("formula_limit")
        if images > limits.max_embedded_images:
            raise PreflightError("image_limit")
        normalized = normalize_text(" ".join(text_parts))
        return PreflightReport(
            format=expected_format, detected_mime=expected_mime, normalized_text=normalized,
            extracted_characters=len(normalized), input_size_bytes=size,
            uncompressed_size_bytes=total_uncompressed, zip_entries=len(infos), slides=slides,
            sheets=sheets, non_empty_cells=cells, max_row=max_row, max_column=max_column,
            formulas=formulas, embedded_images=images,
        )
