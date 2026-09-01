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
} else { "1.1.0" }
$Version = if ($env:VERSION) { $env:VERSION } else { $DefaultVersion }

# Resolve the Python interpreter that has PyInstaller. On the GitHub-hosted
# Windows runner, `actions/setup-python` installs a `python` (and `python3`)
# shim that points at the exact interpreter it managed — we MUST use it,
# because the bare `py -3` launcher resolves to whichever Python is newest
# on the runner (currently Python 3.14.7, which PyInstaller does NOT yet
# support). On a normal dev box, `py -3` is more reliable than `python`
# (which is sometimes the Microsoft Store "App execution alias" stub), so we
# fall back to that after the setup-python shim.
$PyExe = $null; $PyArgs = @()
foreach ($candidate in @("python", "python3", "py")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        # Sanity-check: import PyInstaller (a tiny startup-cost probe) so we
        # skip a `python` shim that points at a vanilla interpreter without
        # the build deps. The PyInstaller import is what actually matters.
        $probe = & $found.Source -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PyExe = $found.Source
            break
        }
    }
}
if (-not $PyExe) {
    throw "Python 3 with PyInstaller not found on PATH. Install PyInstaller in the interpreter that 'python', 'python3', or 'py' resolves to, then re-run."
}

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
