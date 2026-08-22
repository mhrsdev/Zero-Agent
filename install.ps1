# Zero one-line installer for Windows (PowerShell 5.1+).
# Idempotent: safe to re-run; never prints secret values.
$ErrorActionPreference = "Stop"

function Say($m)  { Write-Host "[install] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[install] $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "[install] $m" -ForegroundColor Red; exit 1 }

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoDir

# 1) OS / arch detection.
$Arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
Say "detected OS=Windows ARCH=$Arch"

# 2) Python >= 3.11.
$Py = $null
foreach ($candidate in @("python", "py")) {
  if (Get-Command $candidate -ErrorAction SilentlyContinue) { $Py = $candidate; break }
}
if (-not $Py) { Die "python not found; install Python 3.11+ from python.org first" }
$ver = & $Py -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"
& $Py -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)"
if ($LASTEXITCODE -ne 0) { Die "Python >= 3.11 required, found $ver" }
Say "python $ver OK"

# 3) Virtual environment (reused if present).
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Say "creating .venv"
  & $Py -m venv .venv
  if ($LASTEXITCODE -ne 0) { Die "venv creation failed" }
}
$VenvPy = ".venv\Scripts\python.exe"

# 4) Dependencies (locked versions when available).
# A pre-existing venv may lack pip (e.g. created by uv): bootstrap it, fall
# back to uv, and only then give up with an actionable message.
# NOTE: probes run through cmd so pip's stderr never reaches PowerShell --
# under $ErrorActionPreference="Stop", PS 5.1 turns native stderr into a
# terminating NativeCommandError.
function Test-VenvPip {
  cmd /c "`"$VenvPy`" -m pip --version >nul 2>nul"
  return ($LASTEXITCODE -eq 0)
}

if (-not (Test-VenvPip)) {
  Say "venv has no pip; trying ensurepip"
  cmd /c "`"$VenvPy`" -m ensurepip --upgrade >nul 2>nul"
}
$Uv = Get-Command uv -ErrorAction SilentlyContinue
if ((-not (Test-VenvPip)) -and $Uv) {
  Say "pip unavailable; using uv against the existing venv"
  & $Uv pip install -r $(if (Test-Path "requirements.lock") { "requirements.lock" } else { "requirements.txt" }) --python $VenvPy
  if ($LASTEXITCODE -ne 0) { Die "dependency installation via uv failed" }
} elseif (-not (Test-VenvPip)) {
  Die ".venv exists without pip and neither ensurepip nor uv could provide one; delete .venv and re-run"
} else {
  $Req = if (Test-Path "requirements.lock") { "requirements.lock" } else { "requirements.txt" }
  Say "installing dependencies from $Req"
  & $VenvPy -m pip install --upgrade pip | Out-Null
  & $VenvPy -m pip install -r $Req
  if ($LASTEXITCODE -ne 0) { Die "dependency installation failed" }
}

# 5) Configuration bootstrap (never overwrites; never prints secrets).
# The legacy runtime resolves its YAML at <ZERO_HOME>/config/zero.yaml
# (see zero.runtime_config.runtime_config_path) -- match that exactly.
$ZeroHome = if ($env:ZERO_HOME) { $env:ZERO_HOME } else { Join-Path $env:USERPROFILE ".zero" }
New-Item -ItemType Directory -Force -Path (Join-Path $ZeroHome "config") | Out-Null
$Cfg = Join-Path $ZeroHome "config\zero.yaml"
if ((-not (Test-Path $Cfg)) -and (Test-Path "config\zero.example.yaml")) {
  Copy-Item "config\zero.example.yaml" $Cfg
  Say "created $Cfg from example -- EDIT IT before first run"
} elseif (Test-Path $Cfg) {
  Say "existing config kept: $Cfg"
}

# 6) Database schema init (repeatable).
Say "initialising database schema"
$env:ZERO_HOME = $ZeroHome
& $VenvPy scripts\init_db.py
if ($LASTEXITCODE -ne 0) { Warn "init_db reported a problem (may be already initialised)" }

# 7) Health check.
Say "running doctor"
& $VenvPy scripts\doctor.py
if ($LASTEXITCODE -ne 0) { Warn "doctor found issues -- fix them before starting the listener" }

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. edit $Cfg (telegram api_id/api_hash, groups)"
Write-Host "  2. start:   .venv\Scripts\python.exe scripts\run_listener.py"
Write-Host "  3. health:  .venv\Scripts\python.exe scripts\doctor.py"
Write-Host "  4. stop: Ctrl-C  |  update: git pull; .venv\Scripts\python.exe -m pip install -r $Req"
Write-Host "  5. emergency stop:  setx ZERO_AUTOMATION_DISABLED true"
Say "done"