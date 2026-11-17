# Giddh DSC Bridge

Lets staff at Giddh's customer businesses sign documents with their own hardware DSC token (an Indian digital signature certificate, via PKCS#11) from the Giddh web app, without the private key leaving the token. It has three parts:
- an MV3 Chrome extension (`dsc-bridge/extension/`) that exposes `window.GiddhBridge` on allowed Giddh pages;
- a Python native-messaging host (`dsc-bridge/native-host/giddh_dsc_host.py` + `pkcs11_signer.py`) that drives the token;
- a Tkinter companion app (`giddh_dsc_status.py`).

The host and app are frozen with PyInstaller into a `.dmg`/`.pkg`, `.exe` or `.deb` by GitHub Actions.

## Commands

Verified on 2026-11-17 in a scratch copy. [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) has the details.

| Task | Command |
|---|---|
| Install deps | `python3 -m venv .venv && .venv/bin/pip install -r dsc-bridge/native-host/requirements.txt` |
| Compile check | `python3 -m py_compile dsc-bridge/native-host/*.py` |
| Extension JS check | `node --check dsc-bridge/extension/<file>.js` (each file) |
| Manifest JSON | `python3 -c "import json;json.load(open('dsc-bridge/extension/manifest.json'))"` |
| Host diagnose (no PIN) | `python3 dsc-bridge/native-host/giddh_dsc_host.py --diagnose` |
| Manual end-to-end | Load `dsc-bridge/extension/` unpacked, point a dev native-host manifest at the source, and open `dsc-bridge/test-page/index.html` (setup in DEVELOPMENT) |
| Self-test | **Broken:** `selftest.py` exits 2 at import ([Q-012](docs/OPEN_QUESTIONS.md)) |
| Installers | CI only (`.github/workflows/build-packages.yml`). Push a `test-*` branch to build without publishing. |

## Reading Order Before You Change Code

1. **[docs/VISION.md](docs/VISION.md)**: purpose, the security principles and non-goals. Check every request against them.
2. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: components, process model, trust boundaries, install locations.
3. Then read only what the task touches:
   - [docs/FLOWS.md](docs/FLOWS.md): install, detect, list, sign, failures and uninstall.
   - [docs/CONTRACTS.md](docs/CONTRACTS.md): the `GiddhBridge` API, message protocol, error codes, extension ID and allowlist, drivers. The contract with the Giddh web app and backend stays in `dsc-bridge/INTEGRATION.md`.
   - [docs/DESIGN.md](docs/DESIGN.md): popup, companion app, test page, icons, user-facing copy.
   - [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): the dev loop, checks, CI and secrets by name, versioning, release.
   - [docs/DECISIONS.md](docs/DECISIONS.md): why things are the way they are (signing, macOS packaging, the store ID).
   - [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md): **open security issues first**, then unknowns.

## Project Rules & Boundaries

1. **Treat this as security software.** Any change to origins, the message protocol, driver loading or PIN handling needs its own PR, a stated reason and the owner's approval ([VISION.md](docs/VISION.md#core-principles--design-philosophy)).
2. **Never let a page choose which library the host loads.** Driver paths come only from detection or the local companion app. The `certId` path in `sign_hash` breaks this today; that's a bug to fix, not a pattern to copy ([OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md#security-issues-found-while-documenting-not-fixed)).
3. **Keep the origin allowlist in sync in both places:** `matches` in `dsc-bridge/extension/manifest.json` and the lists in `background.js` (`_isAllowedOrigin`). `ALLOW_ALL_ORIGINS` must stay `false` in anything that ships.
4. **Never log, store or echo a PIN,** and never add a code path that exports key material.
5. **The extension ID is fixed** (`pbnmboohmdoknhpflpmeocccojkkjgng`, from the manifest `key`). Change `packaging/extension-id.txt`, the manifest key or `vars.GIDDH_EXT_ID` only together; otherwise Chrome refuses the host.
6. **Use one native port per call, and run one native call at a time** (`background.js`). This was a deliberate fix for driver mutex contention; don't bring back a long-lived port ([DECISIONS.md](docs/DECISIONS.md)).
7. **Load drivers only in the isolated worker** (`IsolatedSigner`). Never load a vendor PKCS#11 library in the host process.
8. **Web-facing calls are read-only apart from signing.** Never add a web API that attaches modules or writes the config.
9. **The backend builds the PDF signature** (PAdES prepare/finish, `INTEGRATION.md`). The bridge only signs a hash. A new need from the web app is a contract change in `INTEGRATION.md` first.
10. **Bump all version strings together:** `VERSION`, `manifest.json`, `bridge-inject.js`, and the hard-coded `ping` / `version_info.txt` values ([DEVELOPMENT.md](docs/DEVELOPMENT.md#versioning)).
11. **Don't run installers, signing, notarisation or keychain commands on a developer machine to "check" something.** Use CI on a `test-*` branch. Installing the macOS `.pkg`, or running the status app from source, deletes user-level native-host manifests.
12. **Copy is for non-technical business staff:** plain words, and say what to do next. Don't point users at UI that no longer exists ([DESIGN.md](docs/DESIGN.md)).
13. **Never commit certificates, `.pem` keys or credentials.** Name GitHub secrets and variables only.

## Where Things Are

- `dsc-bridge/extension/`: `manifest.json`, `background.js` (relay + allowlist), `content-script.js`, `bridge-inject.js` (`window.GiddhBridge`), the popup.
- `dsc-bridge/native-host/`: `giddh_dsc_host.py` (stdio host), `pkcs11_signer.py` (drivers, isolation, signing), `giddh_dsc_status.py` (companion app), `selftest.py`, `requirements.txt`.
- `dsc-bridge/test-page/index.html`: manual test page. `dsc-bridge/INTEGRATION.md`: the web app / backend contract.
- `packaging/`: PyInstaller specs, macOS, Windows (Inno Setup, eSigner) and Linux build scripts, and `extension-id.txt`.
- `.github/workflows/build-packages.yml`: CI builds, signing, S3 upload, releases.
- `icons/`: master icon and generated sizes. `VERSION`: the app version.

## PR & Release Flow

- **The owner's description (2026-11-17):** a PR to `main` gives CI test builds, a `v*` tag publishes the release installers, and the extension is uploaded to the Chrome Web Store by hand.
- **What CI actually does** ([Q-011](docs/OPEN_QUESTIONS.md)):

> [!WARNING]
> A PR builds nothing (there is no `pull_request` trigger). **A push or merge to `main` is treated as `prod`:** it uploads to S3 `prod` and promotes the Windows installer to `latest`. Only `test-*` branches upload to `test`. Until Q-011 is settled, treat merging to `main` as a production publish. <!-- verify:ignore -->

- **Before a PR:** run the compile and `node --check` checks, `--diagnose`, and the test page with a token if you have one. Say which OS and token you tested.
- Agents never commit, push, merge, tag or deploy without the owner saying so for that action.

## Evaluating a Request

Follow [docs/VISION.md](docs/VISION.md#how-future-agents-must-evaluate-requests):
- Name the OS, token vendor and Giddh flow involved.
- Decline wider origins, web-side module management or key export, and explain why.
- Put new web-app needs into `INTEGRATION.md` as a contract first.
- Update `VISION.md` when the owner changes direction.

## Verification & Knowledge Maintenance

- **Say what you couldn't verify.** Without a DSC token, certificate listing and signing are unverified. Without CI, installers are unverified.
- **Preserve:** the extension ID, the allowlist, the error codes, the `GiddhBridge` API shape, install locations and the one-port-per-call model, unless the change is about them.
- **Update the owning doc in the same change,** and bump its `verified_at`: <!-- verify:ignore -->

  | What changed | Doc to update |
  |---|---|
  | Protocol, codes | `CONTRACTS.md` (and `INTEGRATION.md` if the web app is affected) |
  | Components | `ARCHITECTURE.md` |
  | Journeys | `FLOWS.md` |
  | UI, copy | `DESIGN.md` |
  | Build, CI, version | `DEVELOPMENT.md` |
  | Reversed choices | `DECISIONS.md` |
  | Unknowns | `OPEN_QUESTIONS.md` |

- **Superseded:** `PROJECT.md` is replaced by this file and `docs/`. `README.md` stays user/ops-facing, and `docs/WINDOWS_SIGNING.md` stays a runbook (partly stale; see Q-013).
