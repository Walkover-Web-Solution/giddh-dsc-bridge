# Local/manual convenience wrapper: freezes the native host + status app,
# then builds the Inno Setup installer — with no code signing.
#
# CI (.github/workflows/build-packages.yml) does NOT call this script. It
# calls freeze.ps1, optionally signs the raw .exe files with SSL.com eSigner,
# then calls build_installer.ps1, then optionally signs the installer too.
# Use this script for a quick local unsigned build.
#
# Requirements: Python 3.9+, Inno Setup (ISCC.exe).
#
# Inputs (env):
#   VERSION        package version         (default: contents of ..\..\VERSION)
#   GIDDH_EXT_ID   published extension ID  (default: contents of ..\extension-id.txt)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

& (Join-Path $Here "freeze.ps1")
& (Join-Path $Here "build_installer.ps1")
