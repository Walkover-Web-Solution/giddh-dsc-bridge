# PROJECT.md — Giddh DSC Bridge (AI Agent / Contributor Guide)

Single-file reference for anyone (human or AI agent) picking up this repo cold.
Read this before making changes. It complements, but does not replace,
`README.md` (user/ops-facing) and `dsc-bridge/INTEGRATION.md` (server API
contract) — this file is the "how the pieces fit + where to touch what" map.

## 1. What this project is

A desktop bridge that lets a browser tab sign PDFs with a hardware **DSC token**
(India digital signature certificate, PKCS#11) **without the private key ever
leaving the device**. Three components ship together:

```
Chrome extension  ──►  native host (local Python program)  ──►  DSC token (USB)
   (in the page)          (com.giddh.dsc.bridge)
                                │
                                └─► status/companion app (visible window)
```

1. **Browser extension** (`dsc-bridge/extension/`) — MV3 extension injecting
   `window.GiddhBridge` into allowed pages; relays to the native host.
2. **Native messaging host** (`dsc-bridge/native-host/giddh_dsc_host.py`) —
   headless process Chrome spawns per call; talks PKCS#11 to the token.
3. **Status/companion app** (`dsc-bridge/native-host/giddh_dsc_status.py`) —
   the only thing the end user actually *sees*: a small Tkinter window that
   shows install status, lets the user check the token, and uninstall.

The native host + status app are frozen into a single **installer per OS**
(`.dmg` macOS, `.exe` Windows, `.deb` Linux) via PyInstaller + platform
packagers, so the end user needs **no Python installed**.

## 2. Repository map

```
dsc-bridge/
  extension/               MV3 Chrome extension
    manifest.json            fixed extension key/ID + host permissions + domain matches
    background.js            service worker: native messaging relay + ORIGIN allowlist
    content-script.js        bridges page <-> background via postMessage
    bridge-inject.js          injects window.GiddhBridge into the page context
  native-host/             Python source (not run directly by end users — frozen by PyInstaller)
    giddh_dsc_host.py         headless host: stdin/stdout native-messaging protocol
    giddh_dsc_status.py       visible companion app (Tkinter GUI)
    pkcs11_signer.py          multi-vendor PKCS#11 driver discovery + signing + isolation
    selftest.py                local diagnostic script (see INTEGRATION.md checklist)
    requirements.txt
  test-page/index.html      standalone page to exercise the bridge without the real app
  INTEGRATION.md            server-side API contract (prepare/finish, PAdES)

packaging/
  pyinstaller/
    giddh_dsc_host.spec       freezes the headless host (console=True, no Tk)
    giddh_dsc_status.spec     freezes the GUI app (windowed, bundles Tk/PIL, produces macOS .app)
  macos/build_macos.sh       builds .dmg (pkg + companion .app + postinstall script)
  windows/
    freeze.ps1                PyInstaller-freeze ONLY (host + status .exe)
    build_installer.ps1       Inno Setup compile ONLY (assumes freeze.ps1 already ran)
    build_windows.ps1         local convenience wrapper: freeze.ps1 + build_installer.ps1, unsigned
    installer.iss              Inno Setup script
  linux/build_deb.sh         builds .deb (postinst/prerm register native-messaging manifest)
  extension-id.txt           published extension ID, read by all three build scripts

.github/workflows/build-packages.yml   CI: builds all 3 installers on a `v*` tag;
                                        Windows leg includes SSL.com eSigner signing
                                        (inactive until secrets are added)
docs/WINDOWS_SIGNING.md      how Windows code signing is wired + how to turn it on

VERSION                      single source of truth for the app version (read by
                              specs, build scripts, and the status app at runtime)
icons/                       all app icon assets in one folder (see §7)
```

## 3. Runtime architecture (what talks to what)

- **Page → extension**: `bridge-inject.js` exposes `window.GiddhBridge.*`
  (getCertificate, signHash, diagnose, listModules), which `postMessage`s to
  `content-script.js`, which forwards to `background.js` via
  `chrome.runtime.sendMessage`.
- **Extension → host**: `background.js` opens a **fresh** `connectNative`
  port **per call** (not a shared long-lived port) — see the big comment
  block at the top of `background.js` for why (PKCS#11 driver process-level
  mutex contention on hot-swapped tokens). Only one native call runs at a
  time (`_enqueueNative` queue).
- **Host → token**: `pkcs11_signer.py` discovers PKCS#11 `.so`/`.dylib`/`.dll`
  candidates for common Indian DSC vendors, isolates each driver load in a
  **worker subprocess** (`IsolatedSigner`, `WORKER_FLAG`) so a crashing/wedged
  driver can't take down the host, and exposes `list_certificates()` /
  `sign_hash()` / `list_modules()` / `diagnose()`.
- **Status app**: reads the SAME config file the host reads
  (`~/.giddh_dsc_bridge.json` and `dsc-bridge.json` under the platform config
  dir) to let a user attach/pin a module; the host re-reads that file's mtime
  on every request so changes apply without a browser restart.

## 4. Security model — do not casually change these

- **Origin allowlist lives in TWO places that must stay in sync**:
  `dsc-bridge/extension/background.js` (`ALLOWED_HOST_SUFFIXES`,
  `ALLOWED_HOSTS_EXACT`, `ALLOW_LOCALHOST`, `ALLOW_ALL_ORIGINS`) AND
  `manifest.json` (`content_scripts[].matches`,
  `web_accessible_resources[].matches`). Chrome's own match-pattern check is
  the first gate; `background.js`'s `_isAllowedOrigin` is the second,
  defense-in-depth gate using `sender.origin` (which page JS cannot spoof).
  `ALLOW_ALL_ORIGINS` must be `false` in any build that leaves a developer's
  machine.
- **Module attach/pin is desktop-app-only.** `listModules()` on the web API
  is deliberately **read-only**. Only `giddh_dsc_status.py` (the local GUI)
  can write to the shared config that adds a new PKCS#11 library path. Do not
  add a web-facing "attach module" call — that would let any page make the
  host `dlopen` an arbitrary local library.
- **PIN and private key never leave the token/process boundary they must
  stay in.** The PIN travels page → background → native host → PKCS#11
  `C_Login` and is never persisted or logged. Do not add logging that prints
  a raw PIN or hash payload beyond what already exists.
- **Extension ID is fixed** via the `key` field in `manifest.json` (derived
  from the gitignored `giddh-extension-key.pem`). The native host manifest's
  `allowed_origins` (written by the installer from
  `packaging/extension-id.txt`) must match it exactly, or Chrome refuses the
  connection ("Access to the specified native messaging host is forbidden").

## 5. Versioning

`VERSION` (repo root, plain text, e.g. `1.7.0`) is the single source of truth.

- PyInstaller specs (`packaging/pyinstaller/*.spec`) read it directly for the
  macOS `.app` bundle version and Windows `version_info.txt`.
- `packaging/windows/freeze.ps1`, `build_macos.sh`, `build_deb.sh` all default
  to it (overridable via `VERSION=x.y.z` env for one-off builds).
- The status app (`giddh_dsc_status.py`) reads it at **runtime** from (in
  order) a build-injected `_buildinfo.py` next to the executable, then the
  `VERSION` file, then a hardcoded fallback — so the "Version x.y.z" text on
  the centered welcome screen always matches what was actually built.
- `dsc-bridge/extension/manifest.json` (`version`) and `bridge-inject.js`
  (`version:` in the injected object) are bumped **manually** and should be
  kept in step with `VERSION` on every release — they are separate because
  Chrome extension versions have their own format rules.

When bumping the version: edit `VERSION`, then `manifest.json` +
`bridge-inject.js` in the extension, then tag `vX.Y.Z` to trigger CI.

## 6. The status/companion app UI

`giddh_dsc_status.py` is a single Tkinter window (no stray "Widget
Demonstration" file-menu artifacts — those came from Tk's default menu on
macOS before a custom menubar was installed; the app now always installs its
own minimal menu). Layout, top to bottom:

1. **Hero**: app icon, name, `Version X.Y.Z`, one-line description — centered.
2. **Primary actions**: Check token (on-demand popup with live results),
   Uninstall…, Quit.

There is no PKCS#11 module manager or persistent status card in this app —
module listing, the token picker, and certificate reading/signing live in the
[test page](dsc-bridge/test-page/index.html) (`listModules()` /
`getCertificate(driver)`), which the extension's real callers also use. A
persistent status card was tried and removed: `_find_host_executable()`-based
detection is only reliable right after a fresh install, so it produced false
"not installed" errors on later reopens — an on-demand "Check token" button
that reports the live result via popup replaced it.

The window forces a light `ttk` palette (`_apply_palette`) regardless of OS
dark mode, because the bundled Tk 8.5 runtime on macOS renders label text
black-on-black in dark mode otherwise — this was the root cause of the
"blank window" screenshots. Do not remove `_apply_palette` or the
`NSRequiresAquaSystemAppearance` Info.plist key in `giddh_dsc_status.spec`
without re-testing dark mode.

## 7. Icons

All icon assets live in `icons/`:

| File                          | Used by                                     |
| ----------------------------- | ------------------------------------------- |
| `icons/app_source_1024.png`   | Master 1024×1024 render — regenerate the rest from this. |
| `icons/app.icns`              | macOS `.app` bundle (PyInstaller + Finder). |
| `icons/app.ico`               | Windows `.exe` (PyInstaller) + Inno Setup installer. |
| `icons/tray.png`              | 16×16 tray PNG fallback (Linux/Windows tray). |
| `icons/tray@2x.png`           | 32×32 tray PNG (HiDPI / macOS fallback). |
| `icons/tray-small.png`        | 16×16 compact tray PNG.                     |

When you replace the brand asset, drop a new 1024×1024 master into
`icons/app_source_1024.png` and regenerate the rest with Pillow (and
`iconutil` on macOS):

```python
from PIL import Image
import shutil, os
img = Image.open("icons/app_source_1024.png").convert("RGBA")
# tray PNGs
img.resize((16,16), Image.Resampling.LANCZOS).save("icons/tray-small.png")
img.resize((16,16), Image.Resampling.LANCZOS).save("icons/tray.png")
img.resize((32,32), Image.Resampling.LANCZOS).save("icons/tray@2x.png")
# .ico (multi-size)
img.save("icons/app.ico", format="ICO",
         sizes=[(s,s) for s in (16,32,48,256)])
# .icns via macOS iconutil:
#   mkdir iconset && for s in 16 32 64 128 256 512 1024; do
#     img.resize((s,s)).save("iconset/icon_${s}x${s}.png")
#     img.resize((s*2,s*2)).save("iconset/icon_${s}x${s}@2x.png")
#   done
#   iconutil -c icns iconset -o icons/app.icns
```

Consumers: `packaging/pyinstaller/*.spec` (`icon=` param + bundled `datas`),
`packaging/windows/installer.iss` (`SetupIconFile`), and `giddh_dsc_status.py`
(`get_icon_path()` renders the hero logo at runtime).

## 8. Windows code signing (SSL.com eSigner) — architecture only, inactive

See `docs/WINDOWS_SIGNING.md` for full details. Summary: the Windows CI leg
is split into `freeze.ps1` (PyInstaller only) → **[sign raw exes if
credentials exist]** → `build_installer.ps1` (Inno Setup only) → **[sign
installer if credentials exist]**, using the
`SSLcom/esigner-codesign@v1.3.2` action, mirroring the pattern used by
Giddh's main Electron app. It is gated on four secrets (`ES_USERNAME`,
`ES_PASSWORD`, `ES_CREDENTIAL_ID`, `ES_TOTP_SECRET`) being present; without
them the workflow logs "building UNSIGNED" and still succeeds. **Do not
hardcode credentials or remove the gate** — add the secrets in
GitHub when ready and signing turns on with no further code changes.

## 9. Build & test commands

```bash
# Local unsigned build (OS you're currently on only — PyInstaller can't cross-compile):
./packaging/macos/build_macos.sh        # -> dist/GiddhDSCBridge-<ver>.dmg
./packaging/windows/build_windows.ps1   # -> dist/GiddhDSCBridge-Setup-<ver>.exe (needs Inno Setup)
./packaging/linux/build_deb.sh          # -> dist/giddh-dsc-bridge_<ver>_<arch>.deb

# All three via CI:
git tag v1.7.0 && git push origin v1.7.0   # or run build-packages.yml manually

# Native host self-test (no browser needed):
python3 dsc-bridge/native-host/selftest.py

# Quick syntax/compile check after editing Python:
python3 -m py_compile dsc-bridge/native-host/giddh_dsc_status.py
python3 -m py_compile dsc-bridge/native-host/giddh_dsc_host.py

# Manually launch the status app during UI work:
python3 dsc-bridge/native-host/giddh_dsc_status.py
```

## 10. Conventions for future changes

- Keep the native host **headless** (no Tk import) — it must stay usable as
  a `console=True` PyInstaller build; all UI work belongs in
  `giddh_dsc_status.py`.
- Any new web-facing API on `window.GiddhBridge` must be **read-only** or
  require an explicit user action (PIN entry) in the page — never let a page
  silently mutate host-side config.
- Any new PKCS#11 vendor driver path goes in `pkcs11_signer.py`'s driver
  candidate list, not into `giddh_dsc_host.py`.
- Keep `background.js`'s allowlist and `manifest.json`'s `matches` arrays in
  sync — grep for `giddh.com` before changing either.
- When adding packaging changes, prefer editing the platform script
  (`build_macos.sh` / `freeze.ps1` + `build_installer.ps1` / `build_deb.sh`)
  over duplicating logic in `.github/workflows/build-packages.yml`; CI should
  stay a thin orchestrator that calls those scripts.
