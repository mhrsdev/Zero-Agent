#!/usr/bin/env bash
# Zero one-line installer for Linux/macOS.
# Idempotent: safe to re-run; never prints secret values.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
say()  { printf "${GREEN}[install]${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}[install]${NC} %s\n" "$1"; }
die()  { printf "${RED}[install]${NC} %s\n" "$1" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# 1) OS / arch detection.
OS="$(uname -s)"; ARCH="$(uname -m)"
say "detected OS=$OS ARCH=$ARCH"
case "$OS" in Linux|Darwin) ;; *) die "unsupported OS: $OS (use install.ps1 on Windows)";; esac

# 2) Python >= 3.11.
PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then PY=python; fi
command -v "$PY" >/dev/null 2>&1 || die "python3 not found; install Python 3.11+ first"
PYVER="$($PY -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python >= 3.11 required, found $PYVER"
say "python $PYVER OK"

# 3) Virtual environment (reused if present).
if [ ! -x ".venv/bin/python" ]; then
  say "creating .venv"
  "$PY" -m venv .venv || die "venv creation failed"
fi
PIP=".venv/bin/python -m pip"
REQ="requirements.txt"
[ -f requirements.lock ] && REQ="requirements.lock"

# 4) Dependencies. A pre-existing venv may lack pip (e.g. created by uv):
# bootstrap it, fall back to uv, then fail with an actionable message.
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  say "venv has no pip; trying ensurepip"
  .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    say "pip unavailable; using uv against the existing venv"
    uv pip install -r "$REQ" --python .venv/bin/python || die "dependency installation via uv failed"
  else
    die ".venv exists without pip and neither ensurepip nor uv could provide one; delete .venv and re-run"
  fi
else
  say "installing dependencies from $REQ"
  $PIP install --upgrade pip >/dev/null
  $PIP install -r "$REQ" || die "dependency installation failed"
fi

# 5) Configuration bootstrap (never overwrites; never prints secrets).
# The legacy runtime resolves its YAML at <ZERO_HOME>/config/zero.yaml
# (see zero.runtime_config.runtime_config_path) -- match that exactly.
CONFIG_DIR="${ZERO_HOME:-$HOME/.zero}"
CONFIG_FILE="$CONFIG_DIR/config/zero.yaml"
mkdir -p "$(dirname "$CONFIG_FILE")"
if [ ! -f "$CONFIG_FILE" ] && [ -f config/zero.example.yaml ]; then
  cp config/zero.example.yaml "$CONFIG_FILE"
  say "created $CONFIG_FILE from example -- EDIT IT before first run"
elif [ -f "$CONFIG_FILE" ]; then
  say "existing config kept: $CONFIG_FILE"
fi

# 6) Database schema init (repeatable).
say "initialising database schema"
ZERO_HOME="$CONFIG_DIR" .venv/bin/python scripts/init_db.py \
  || warn "init_db reported a problem (may be already initialised)"

# 7) Health check.
say "running doctor"
ZERO_HOME="$CONFIG_DIR" .venv/bin/python scripts/doctor.py \
  || warn "doctor found issues -- fix them before starting the listener"

cat <<EOF

Next steps:
  1. edit $CONFIG_FILE (telegram api_id/api_hash, groups)
  2. start:   .venv/bin/python scripts/run_listener.py
  3. health:  .venv/bin/python scripts/doctor.py
  4. stop:    Ctrl-C   |  update: git pull && $PIP install -r $REQ
  5. emergency stop:  ZERO_AUTOMATION_DISABLED=true
EOF
say "done"