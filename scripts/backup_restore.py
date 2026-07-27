#!/usr/bin/env python3
"""Backup and restore for Zero SQLite databases.

Usage:
    python scripts/backup_restore.py backup <db_path> <backup_path>
    python scripts/backup_restore.py restore <backup_path> <db_path>
    python scripts/backup_restore.py verify <db_path>

backup: copies the DB file + WAL + SHM to a timestamped backup path
restore: replaces the DB file with the backup, verifying integrity first
verify: opens the DB and checks schema integrity (PRAGMA integrity_check)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path


def backup_db(db_path: str, backup_path: str) -> str:
    """Copy the DB file (+ WAL/SHM) to backup location. Returns backup path."""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"Database not found: {src}")
    dst = Path(backup_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    # Also copy WAL and SHM if they exist (for write-ahead log consistency)
    for ext in ("-wal", "-shm"):
        sidecar = Path(f"{src}{ext}")
        if sidecar.exists():
            shutil.copy2(sidecar, Path(f"{dst}{ext}"))
    # Verify the backup is readable
    try:
        conn = sqlite3.connect(str(dst))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        if result[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result[0]}")
    except sqlite3.Error as exc:
        raise RuntimeError(f"Backup verification failed: {exc}")
    return str(dst)


def restore_db(backup_path: str, db_path: str) -> str:
    """Restore DB from backup. Verifies backup integrity first."""
    src = Path(backup_path)
    if not src.exists():
        raise FileNotFoundError(f"Backup not found: {src}")
    # Verify backup integrity before restoring
    conn = sqlite3.connect(str(src))
    result = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
    if result[0] != "ok":
        raise RuntimeError(f"Backup integrity check failed: {result[0]}")
    dst = Path(db_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # If target exists, create a .bak copy
    if dst.exists():
        shutil.copy2(dst, Path(f"{dst}.bak"))
    # Remove WAL/SHM so stale WAL journal doesn't override restored data
    for ext in ("-wal", "-shm"):
        sidecar = Path(f"{dst}{ext}")
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy2(src, dst)
    # Also restore WAL/SHM if they exist in backup
    for ext in ("-wal", "-shm"):
        sidecar = Path(f"{src}{ext}")
        if sidecar.exists():
            shutil.copy2(sidecar, Path(f"{dst}{ext}"))
    return str(dst)


def verify_db(db_path: str) -> dict:
    """Open DB and run integrity check. Returns result dict."""
    path = Path(db_path)
    if not path.exists():
        return {"ok": False, "error": f"Database not found: {path}"}
    try:
        conn = sqlite3.connect(str(path))
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        # Count rows in key tables
        tables = {}
        for table in ("office_jobs", "office_delivery_outbox",
                       "direct_memory", "semantic_memory", "settings"):
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                tables[table] = count[0]
            except sqlite3.Error:
                tables[table] = "missing"
        conn.close()
        return {"ok": integrity[0] == "ok", "integrity": integrity[0], "tables": tables}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backup_restore", description="Zero DB backup/restore")
    sub = parser.add_subparsers(dest="command", required=True)
    bk = sub.add_parser("backup", help="Backup database")
    bk.add_argument("db_path")
    bk.add_argument("backup_path")
    rs = sub.add_parser("restore", help="Restore database from backup")
    rs.add_argument("backup_path")
    rs.add_argument("db_path")
    vf = sub.add_parser("verify", help="Verify database integrity")
    vf.add_argument("db_path")
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            path = backup_db(args.db_path, args.backup_path)
            print(f"Backup created: {path}")
            return 0
        if args.command == "restore":
            path = restore_db(args.backup_path, args.db_path)
            print(f"Restored to: {path}")
            return 0
        if args.command == "verify":
            result = verify_db(args.db_path)
            print(result)
            return 0 if result.get("ok") else 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
