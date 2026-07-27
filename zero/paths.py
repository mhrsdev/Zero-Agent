"""Canonical roots for the repository checkout and the private runtime home.

Zero has two distinct roots that must never be conflated:

* the **repository root** - the checkout itself, holding bundled assets such as
  ``config/zero.example.yaml``, ``scripts/`` and the panel sources. It is
  discovered from this module's location, so it works from any checkout path.
* the **runtime home** - the private per-installation directory holding state,
  secrets, logs and the effective configuration. It defaults to ``~/.zero`` and
  is overridable with the ``ZERO_HOME`` environment variable, which is what
  container images and tests use.

Runtime paths must always be expanded and absolute. A bare ``"~/.zero/..."``
string is not a usable path: ``Path("~/.zero").is_absolute()`` is false on every
platform, and writing to it creates a literal ``~`` directory next to the
process working directory instead of the user's home.
"""
from __future__ import annotations

import os
from pathlib import Path

ZERO_HOME_ENV = "ZERO_HOME"
DEFAULT_ZERO_HOME = "~/.zero"


def zero_home(env: os._Environ[str] | dict[str, str] | None = None) -> Path:
    """Return the expanded, absolute private runtime home."""
    values = os.environ if env is None else env
    return Path(values.get(ZERO_HOME_ENV, DEFAULT_ZERO_HOME)).expanduser()


def zero_home_path(*parts: str | Path) -> Path:
    """Join ``parts`` under the expanded runtime home."""
    return zero_home().joinpath(*parts)


def repo_root() -> Path:
    """Return the repository checkout root."""
    return Path(__file__).resolve().parents[1]


def repo_path(*parts: str | Path) -> Path:
    """Join ``parts`` under the repository checkout root."""
    return repo_root().joinpath(*parts)


def expand(path: str | Path) -> Path:
    """Expand a user-supplied path, tolerating ``~`` prefixes."""
    return Path(path).expanduser()


def expand_user_prefix(value: str) -> str:
    """Expand a leading ``~`` while leaving the rest of the string untouched.

    Unlike :func:`expand`, this does not normalise separators, so a
    POSIX-authored deployment path such as ``/usr/local/lib/x`` survives
    verbatim when the config is read on a non-POSIX host.
    """
    if value.startswith("~"):
        return str(Path(value).expanduser())
    return value
