from __future__ import annotations

from pathlib import Path
import shutil
import time
from typing import Any

from zero.config import OfficeConfig
from .db import OfficeRepository
from .workspace import safe_path, WorkspaceError


def cleanup_expired_workspaces(repository: OfficeRepository, config: OfficeConfig, *, now: int | None = None) -> dict[str, int]:
    now = int(time.time()) if now is None else int(now)
    removed = skipped = failures = 0
    with repository.connect() as conn:
        rows = conn.execute(
            """
            SELECT j.id,j.chat_id,j.status,j.completed_at,j.updated_at,j.quota_state,o.status AS delivery_status
              FROM office_jobs j LEFT JOIN office_delivery_outbox o ON o.job_id=j.id
             WHERE j.status IN ('completed','failed','cancelled','expired')
            """
        ).fetchall()
    root = Path(config.workspace_root).resolve()
    for row in rows:
        hours = config.retention.completed_job_hours if row["status"] == "completed" else config.retention.failed_job_hours
        age_from = int(row["completed_at"] or row["updated_at"] or now)
        if now - age_from < hours * 3600:
            skipped += 1; continue
        if row["status"] == "completed" and row["delivery_status"] != "sent":
            skipped += 1; continue
        try:
            target = safe_path(root, f"{int(row['chat_id'])}/{row['id']}")
            if target.exists():
                if target.is_symlink():
                    raise WorkspaceError("cleanup_symlink")
                shutil.rmtree(target)
            removed += 1
        except (OSError, WorkspaceError):
            failures += 1
    ingest = root.parent / "office_ingest"
    if ingest.is_dir():
        cutoff = now - config.pending_attachment_ttl_minutes * 60
        for item in ingest.iterdir():
            try:
                if item.is_symlink() or item.stat().st_mtime > cutoff:
                    continue
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
            except OSError:
                failures += 1
    return {"removed": removed, "skipped": skipped, "failures": failures}
