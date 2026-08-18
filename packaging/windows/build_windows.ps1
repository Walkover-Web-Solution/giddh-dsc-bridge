# Build the Windows installer: freezes the native host with PyInstaller, then
# compiles the Inno Setup script into a single .exe that registers the
# native-messaging host per-user.
#
# Requirements (Windows CI runner): Python 3.9+, and Inno Setup (ISCC.exe).
# The CI workflow installs Inno Setup via Chocolatey.
#
# Inputs (env):
#   VERSION        package version         (default: 1.3.0)
#   GIDDH_EXT_ID   published extension ID  (default: contents of ..\extension-id.txt)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $Here "..\..")).Path
$Version = if ($env:VERSION) { $env:VERSION } else { "1.3.0" }
$ExtId = if ($env:GIDDH_EXT_ID) { $env:GIDDH_EXT_ID } else {
    (Get-Content (Join-Path $Root "packaging\extension-id.txt") -Raw).Trim()
}

# Resolve a REAL Python 3. On Windows the bare `python` command is often the
# Microsoft Store "App execution alias" stub, which prints an install message
# and silently does nothing (PyInstaller then produces no output). The `py`
# launcher points at the actual interpreter, so prefer it.
$PyExe = $null; $PyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $PyExe = "py"; $PyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $PyExe = "python" }
else { throw "Python 3 not found. Install python.org 3.x, or ensure 'py'/'python' is on PATH." }

Write-Host "==> Freezing native host with PyInstaller (using: $PyExe $PyArgs)"
Push-Location $Root
& $PyExe @PyArgs -m PyInstaller --clean --noconfirm packaging\pyinstaller\giddh_dsc_host.spec
Pop-Location

if (-not (Test-Path (Join-Path $Root "dist\giddh-dsc-host\giddh-dsc-host.exe"))) {
    throw "PyInstaller did not produce dist\giddh-dsc-host\giddh-dsc-host.exe"
}

# Bake the version in, then freeze the visible status/companion app (windowed).
$BuildInfo = Join-Path $Root "dsc-bridge\native-host\_buildinfo.py"
Set-Content -Path $BuildInfo -Value "VERSION = `"$Version`"" -Encoding UTF8
try {
    Write-Host "==> Freezing status app with PyInstaller"
    Push-Location $Root
    $env:VERSION = $Version
    & $PyExe @PyArgs -m PyInstaller --clean --noconfirm packaging\pyinstaller\giddh_dsc_status.spec
    Pop-Location
    if (-not (Test-Path (Join-Path $Root "dist\giddh-dsc-status\giddh-dsc-status.exe"))) {
        throw "PyInstaller did not produce dist\giddh-dsc-status\giddh-dsc-status.exe"
    }
} finally {
    Remove-Item -Path $BuildInfo -ErrorAction SilentlyContinue
}

# Locate the Inno Setup compiler.
$iscc = $null
foreach ($c in @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)) { if (Test-Path $c) { $iscc = $c; break } }
if (-not $iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) { throw "Inno Setup (ISCC.exe) not found. Install it (choco install innosetup)." }

Write-Host "==> Compiling installer (ext id: $ExtId)"
& $iscc "/DAppVersion=$Version" "/DExtId=$ExtId" (Join-Path $Here "installer.iss")

Write-Host "==> Done: $Root\dist\GiddhDSCBridge-Setup-$Version.exe"
