# Compiles the Inno Setup installer from ALREADY-FROZEN executables
# (dist\giddh-dsc-host\giddh-dsc-host.exe, dist\giddh-dsc-status\giddh-dsc-status.exe).
# Run packaging\windows\freeze.ps1 first. Splitting freeze/installer lets CI
# sign the raw .exe files with SSL.com eSigner in between (see
# .github/workflows/build-packages.yml), then sign the resulting installer too.
#
# Inputs (env):
#   VERSION        package version         (default: contents of ..\..\VERSION)
#   GIDDH_EXT_ID   published extension ID  (default: contents of ..\extension-id.txt)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $Here "..\..")).Path
$DefaultVersion = if (Test-Path (Join-Path $Root "VERSION")) {
    (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
} else { "1.6.0" }
$Version = if ($env:VERSION) { $env:VERSION } else { $DefaultVersion }
$ExtId = if ($env:GIDDH_EXT_ID) { $env:GIDDH_EXT_ID } else {
    (Get-Content (Join-Path $Root "packaging\extension-id.txt") -Raw).Trim()
}

$HostExe = Join-Path $Root "dist\giddh-dsc-host\giddh-dsc-host.exe"
$StatusExe = Join-Path $Root "dist\giddh-dsc-status\giddh-dsc-status.exe"
if (-not (Test-Path $HostExe))   { throw "Missing $HostExe — run freeze.ps1 first." }
if (-not (Test-Path $StatusExe)) { throw "Missing $StatusExe — run freeze.ps1 first." }

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

Write-Host "==> Compiling installer (ext id: $ExtId, version: $Version)"
& $iscc "/DAppVersion=$Version" "/DExtId=$ExtId" (Join-Path $Here "installer.iss")

$InstallerPath = Join-Path $Root "dist\GiddhDSCBridge-Setup-$Version.exe"
if (-not (Test-Path $InstallerPath)) {
    throw "Inno Setup did not produce $InstallerPath"
}

Write-Host "==> Done: $InstallerPath"
if ($env:GITHUB_ENV) {
    "INSTALLER_PATH=$InstallerPath" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
}
