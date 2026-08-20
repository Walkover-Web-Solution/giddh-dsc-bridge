# Freezes the Windows native host and the visible status/companion app with
# PyInstaller. Split out from build_windows.ps1 so CI can sign the raw .exe
# files (SSL.com eSigner) *between* this step and the installer build — the
# same pattern used for Electron app+installer signing.
#
# Inputs (env):
#   VERSION   package version (default: contents of ..\..\VERSION)
#
# Outputs (stdout + $GITHUB_ENV when running in GitHub Actions):
#   HOST_EXE_PATH   = dist\giddh-dsc-host\giddh-dsc-host.exe
#   STATUS_EXE_PATH = dist\giddh-dsc-status\giddh-dsc-status.exe
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $Here "..\..")).Path
$DefaultVersion = if (Test-Path (Join-Path $Root "VERSION")) {
    (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
} else { "1.6.0" }
$Version = if ($env:VERSION) { $env:VERSION } else { $DefaultVersion }

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

$HostExe = Join-Path $Root "dist\giddh-dsc-host\giddh-dsc-host.exe"
if (-not (Test-Path $HostExe)) {
    throw "PyInstaller did not produce $HostExe"
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
} finally {
    Remove-Item -Path $BuildInfo -ErrorAction SilentlyContinue
}

$StatusExe = Join-Path $Root "dist\giddh-dsc-status\giddh-dsc-status.exe"
if (-not (Test-Path $StatusExe)) {
    throw "PyInstaller did not produce $StatusExe"
}

Write-Host "==> Frozen executables:"
Write-Host "  Host:   $HostExe"
Write-Host "  Status: $StatusExe"

if ($env:GITHUB_ENV) {
    "HOST_EXE_PATH=$HostExe"     | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
    "STATUS_EXE_PATH=$StatusExe" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
    "PACKAGE_VERSION=$Version"   | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
}
