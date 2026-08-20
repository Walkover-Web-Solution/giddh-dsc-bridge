# Windows Code Signing (SSL.com eSigner)

The Windows build (`.github/workflows/build-packages.yml`) is wired to sign
every Windows binary with [SSL.com eSigner](https://www.ssl.com/esigner/)
using the [`SSLcom/esigner-codesign@v1.3.2`](https://github.com/SSLcom/esigner-codesign)
GitHub Action — the same action used by the main Giddh Electron app.

**Signing is currently INACTIVE.** The workflow checks whether the required
secrets exist; if not, it logs `building UNSIGNED` and produces a working
but unsigned installer. No workflow changes are needed to turn signing on —
just add the four secrets below.

## What gets signed, and in what order

1. `dist/giddh-dsc-host/giddh-dsc-host.exe` — the native-messaging host.
2. `dist/giddh-dsc-status/giddh-dsc-status.exe` — the status/companion app.
3. The signed exes are copied back into place, **then** the Inno Setup
   installer is compiled around them (so the installer ships already-signed
   binaries).
4. `dist/GiddhDSCBridge-Setup-<version>.exe` — the installer itself is signed
   as a final step.

Each signing step is followed by `Get-AuthenticodeSignature` verification;
the job fails loudly if a signature comes back anything other than `Valid`.

## Enabling signing

Add these repository secrets (Settings → Secrets and variables → Actions →
Secrets), obtained from your SSL.com eSigner account:

| Secret              | Description                                   |
|---------------------|------------------------------------------------|
| `ES_USERNAME`       | SSL.com account username                      |
| `ES_PASSWORD`       | SSL.com account password                       |
| `ES_CREDENTIAL_ID`  | eSigner code-signing credential ID             |
| `ES_TOTP_SECRET`    | TOTP secret for the eSigner credential         |

Once all four secrets exist, the very next workflow run automatically signs
host exe → status exe → installer with no further changes.

## Local scripts

The Windows build is split into two scripts specifically so CI can inject
signing between them:

- `packaging/windows/freeze.ps1` — PyInstaller-freezes the host and status
  apps only. Exposes `HOST_EXE_PATH` / `STATUS_EXE_PATH` via `$GITHUB_ENV`.
- `packaging/windows/build_installer.ps1` — compiles the Inno Setup
  installer from the (possibly already-signed) frozen exes. Exposes
  `INSTALLER_PATH`.
- `packaging/windows/build_windows.ps1` — convenience wrapper that runs both
  in sequence with no signing, for local/manual unsigned builds.

## macOS and Linux

Not covered by this document. macOS builds are ad-hoc signed only
(`packaging/macos/build_macos.sh`); Apple notarization and Linux package
signing are out of scope until requested.
