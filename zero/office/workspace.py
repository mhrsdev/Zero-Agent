from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePath
import re
import shutil
import unicodedata


class WorkspaceError(ValueError):
    pass


def sanitize_filename(filename: str, *, max_length: int = 120) -> str:
    if "\x00" in (filename or ""):
        raise WorkspaceError("null_byte")
    raw = Path((filename or "file").replace("\\", "/")).name
    raw = "".join(ch for ch in unicodedata.normalize("NFKC", raw) if unicodedata.category(ch) not in {"Cc", "Cf", "Cs"})
    suffix = Path(raw).suffix.lower()[:10]
    stem = raw[:-len(suffix)] if suffix else raw
    clean = []
    for ch in stem:
        if ch.isalnum() or ch in {"-", "."}:
            clean.append(ch)
        elif ch in {" ", "_", ";", ":"}:
            clean.append("_")
    safe_stem = re.sub(r"[_.]{2,}", "_", "".join(clean)).strip("._-") or "file"
    suffix = "".join(ch for ch in suffix if ch.isascii() and (ch.isalnum() or ch == "."))
    room = max(1, max_length - len(suffix))
    return safe_stem[:room] + suffix


def safe_path(root: str | Path, relative: str | Path) -> Path:
    base = Path(root).resolve()
    value = str(relative)
    if "\x00" in value:
        raise WorkspaceError("null_byte")
    candidate_input = Path(value)
    if candidate_input.is_absolute() or ".." in candidate_input.parts:
        raise WorkspaceError("path_escape")
    candidate = (base / candidate_input).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise WorkspaceError("path_escape") from exc
    return candidate


@dataclass(frozen=True, slots=True)
class JobWorkspace:
    root: Path
    input: Path
    working: Path
    preview: Path
    output: Path
    logs: Path

    @property
    def directories(self) -> tuple[str, ...]:
        return ("input", "working", "preview", "output", "logs")

    def install_input(self, source: str | Path, filename: str) -> Path:
        target = safe_path(self.input, sanitize_filename(filename))
        shutil.copyfile(Path(source), target, follow_symlinks=False)
        os.chmod(target, 0o400)
        return target


def create_workspace(root: str | Path, *, chat_id: int, job_id: str) -> JobWorkspace:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(job_id)):
        raise WorkspaceError("invalid_job_id")
    base = Path(root).resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    job_root = safe_path(base, f"{int(chat_id)}/{job_id}")
    job_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    paths: dict[str, Path] = {}
    for name in ("input", "working", "preview", "output", "logs"):
        path = safe_path(job_root, name)
        path.mkdir(mode=0o700)
        paths[name] = path
    return JobWorkspace(job_root, paths["input"], paths["working"], paths["preview"], paths["output"], paths["logs"])


def open_workspace(root: str | Path, *, chat_id: int, job_id: str) -> JobWorkspace:
    base = Path(root).resolve()
    job_root = safe_path(base, f"{int(chat_id)}/{job_id}")
    if not job_root.is_dir():
        raise WorkspaceError("workspace_missing")
    paths = {name: safe_path(job_root, name) for name in ("input", "working", "preview", "output", "logs")}
    if not all(path.is_dir() for path in paths.values()):
        raise WorkspaceError("workspace_incomplete")
    return JobWorkspace(job_root, paths["input"], paths["working"], paths["preview"], paths["output"], paths["logs"])
