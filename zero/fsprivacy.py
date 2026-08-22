"""Cross-platform private-storage primitives.

Zero stores credentials, sessions, databases, and logs that must be readable
only by the owning account.  POSIX expresses this with owner-only mode bits
(``0o600`` files / ``0o700`` directories).  Windows has no usable
``os.fchmod`` and ``st_mode`` carries no access information there, so the
same guarantee is implemented with NTFS ACLs:

* :func:`restrict_private_path` removes inherited ACLs (``icacls
  /inheritance:r``) and grants access only to the current user and SYSTEM.
* :func:`path_is_private` reads the DACL through ``advapi32`` and reports
  whether any account other than the owner, SYSTEM, or Administrators holds
  an access-allowed ACE.

Every helper fails closed: when privacy cannot be applied or verified the
caller receives an error instead of silently continuing with a world-readable
secret.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

__all__ = [
    "IS_WINDOWS",
    "restrict_private_path",
    "path_is_private",
    "ensure_private_path",
    "posix_mode",
]

IS_WINDOWS = os.name == "nt"

# Well-known SIDs that may hold access on a private object without making it
# group/world readable.  Administrators are the Windows equivalent of root,
# which POSIX mode bits never excluded either.
_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"
_CREATOR_OWNER_SID = "S-1-3-0"


def _windows_identity() -> str:
    identity = os.environ.get("USERNAME")
    if not identity:
        raise PermissionError("cannot determine the Windows user for private storage")
    return identity


def _apply_windows_acl(path: Path, *, directory: bool) -> None:
    permission = "(OI)(CI)F" if directory else "F"
    command = [
        "icacls",
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"{_windows_identity()}:{permission}",
        "/grant:r",
        f"SYSTEM:{permission}",
        "/c",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False, text=True)
    except OSError as exc:
        raise PermissionError(f"Windows ACL hardening is unavailable for {path}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else f"icacls exited with {result.returncode}"
        raise PermissionError(f"Windows ACL hardening failed for {path}: {reason}")


def restrict_private_path(path: str | Path, *, directory: bool = False) -> None:
    """Apply owner-only permissions to an existing path.

    Raises :class:`PermissionError` when the platform cannot enforce privacy.
    """
    target = Path(path)
    if IS_WINDOWS:
        _apply_windows_acl(target, directory=directory)
        return
    mode = 0o700 if directory else 0o600
    os.chmod(target, mode)
    if stat.S_IMODE(target.stat().st_mode) & 0o077:
        raise PermissionError(f"private storage could not be secured: {target}")


def posix_mode(path: str | Path) -> int | None:
    """Return the POSIX permission bits of *path*, or ``None`` off-POSIX."""
    if IS_WINDOWS:
        return None
    return stat.S_IMODE(Path(path).stat().st_mode)


def path_is_private(path: str | Path) -> bool:
    """Report whether *path* is free of group/world access.

    On POSIX this checks the classic mode bits.  On Windows it inspects the
    DACL: every access-allowed ACE must belong to the owner, SYSTEM,
    Administrators, or CREATOR OWNER, and no inherited grant from another
    account may remain.  Unverifiable objects (missing file, unsupported
    filesystem) are reported as not private so callers fail closed.
    """
    target = Path(path)
    try:
        if not target.exists():
            return False
    except OSError:
        return False
    if not IS_WINDOWS:
        try:
            return not (stat.S_IMODE(target.stat().st_mode) & 0o077)
        except OSError:
            return False
    return _windows_dacl_is_private(target)


def ensure_private_path(path: str | Path, label: str) -> None:
    """Raise :class:`PermissionError` unless *path* is private."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"{label} not found: {target}")
    if not path_is_private(target):
        if IS_WINDOWS:
            raise PermissionError(
                f"{label} is accessible to other accounts; remove inherited or "
                f"extra ACL entries (icacls {target} /inheritance:r): {target}"
            )
        raise PermissionError(f"{label} permissions must not expose group/world bits: {target}")


# --- Windows DACL inspection -------------------------------------------------

def _windows_dacl_is_private(target: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x00000001
    DACL_SECURITY_INFORMATION = 0x00000004
    ACCESS_ALLOWED_ACE_TYPE = 0x00
    AclSizeInformation = 2

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    class ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    class ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    def sid_to_string(sid: ctypes.c_void_p) -> str | None:
        string = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(string)):
            return None
        try:
            return string.value
        finally:
            kernel32.LocalFree(string)

    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(target),
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if result != 0 or not dacl:
        # No DACL (unsupported filesystem or insufficient information):
        # fail closed rather than assume privacy.
        return False
    try:
        owner_sid = sid_to_string(owner) if owner else None
        info = ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl, ctypes.byref(info), ctypes.sizeof(info), AclSizeInformation
        ):
            return False
        for index in range(info.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                return False
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(ACCESS_ALLOWED_ACE)).contents
            if ace.Header.AceType != ACCESS_ALLOWED_ACE_TYPE:
                continue  # deny/audit ACEs only narrow access
            trustee = sid_to_string(ctypes.c_void_p(ctypes.addressof(ace) + ACCESS_ALLOWED_ACE.SidStart.offset))
            if trustee is None:
                return False
            if trustee in {
                owner_sid,
                _SYSTEM_SID,
                _ADMINISTRATORS_SID,
                _CREATOR_OWNER_SID,
            }:
                continue
            return False
        return True
    finally:
        kernel32.LocalFree(security_descriptor)