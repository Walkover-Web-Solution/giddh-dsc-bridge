# Giddh DSC Bridge

Client-side signing of Giddh documents with a hardware **DSC token** (PKCS#11).
The browser talks to a small local program that drives the token; the private
key never leaves the device.

```
Chrome extension  ──►  native host (local program)  ──►  DSC token
   (in the page)         (com.giddh.dsc.bridge)           (USB)
```

## Project layout

```
dsc-bridge/
  extension/     Chrome extension (loads in the browser)
  native-host/   the local signer that talks to the token (Python source)
    giddh_dsc_host.py     headless native-messaging host (browser talks to this)
    giddh_dsc_status.py   visible companion app (status, token check, uninstall)
    pkcs11_signer.py      multi-vendor PKCS#11 driver resolution + signing
  test-page/     a standalone page to test token read + sign
packaging/       builds the end-user installers (.dmg / .exe / .deb)
  pyinstaller/   PyInstaller specs for the host + status app
  windows/       Inno Setup installer + signing-ready build scripts
dist/            build output
docs/            WINDOWS_SIGNING.md and other reference docs
icons/            all app icon assets in one folder (see `PROJECT.md` §7)
VERSION          single source of truth for the app version
PROJECT.md       one-file project guide for AI agents / new contributors
giddh-extension-key.pem   SECRET signing key → the fixed extension ID (gitignored)
INTEGRATION.md   API contract for the Giddh web app + backend
```

## How the extension and installer connect

The extension has a **fixed ID** baked in via the `key` field in
`extension/manifest.json`, so it is the **same on every machine**:

```
klmgadogecbimgjkepdljfljajphemfl
```

The installer authorizes exactly that ID in the native host's `allowed_origins`
(read from `packaging/extension-id.txt`). Because both sides are fixed, **one
installer build works on any computer** — no per-machine ID copying.

- The ID is derived from `giddh-extension-key.pem` (the private signing key).
  **Keep that file safe and never commit it** (it is gitignored). You only need
  it to package a signed `.crx`; the public half already lives in the manifest.
- To rotate the ID: regenerate the key, put the new public key in the manifest
  `key`, and write the new ID into `packaging/extension-id.txt`.
- If a build's ID ever mismatches the loaded extension, Chrome shows
  *"Access to the specified native messaging host is forbidden."*

## Install & test — for the Giddh team (prebuilt bridge)

You receive two things (shared separately):

1. **`GiddhDSCBridge-Setup-1.7.0.exe`** — the Windows installer.
2. **`dsc-bridge/extension/`** — the browser extension folder.

### Windows (no admin required)

1. **Run the installer** `GiddhDSCBridge-Setup-1.7.0.exe`. It is **unsigned**, so
   Windows SmartScreen may warn → click **More info → Run anyway**.
2. **Load the extension:** `chrome://extensions` → enable **Developer mode**
   (top-right) → **Load unpacked** → select the `dsc-bridge/extension/` folder.
   The ID must read `klmgadogecbimgjkepdljfljajphemfl`.
3. **Plug in the DSC token.** Its **vendor driver** must already be installed —
   it normally ships with the token. The bridge **auto-detects** the token across
   common vendors (WatchData/ProxKey/Capricorn, SafeNet/eToken, Feitian
   ePass2003, Longmai mToken, IDEMIA/IDPrime) — it probes each installed driver
   and uses the first with a token present.
4. **Test:** open `dsc-bridge/test-page/index.html`, or a page on `giddh.com`
   / `localhost`, read the certificate, and sign. Only your **signing**
   certificate is listed (CA certs are filtered out); its issuing chain is sent
   to the server so Adobe can build the full trust path.

### macOS

1. **Open** `GiddhDSCBridge-1.7.0.dmg`, then run the `.pkg` inside. It is
   **unsigned**, so Gatekeeper blocks a normal double-click → **right-click the
   `.pkg` → Open → Open** to run it anyway.
2. **Load the extension** and **plug in the token** — same as Windows steps 2–3
   (the token's macOS vendor driver must be installed).
3. **Test** the same way (step 4 above).

Nothing else is needed on that machine — **no Python, no build tools**.

### Companion app (visible status + uninstall)

The native host is a **headless** background helper (no window), so the installer
also ships a small visible companion app, **Giddh DSC Bridge**:

- **macOS:** `/Applications/Giddh DSC Bridge.app` (Spotlight/Launchpad).
- **Windows:** Start-menu shortcut **Giddh DSC Bridge** (optional desktop icon).
- **Linux:** app-menu entry **Giddh DSC Bridge** (`.desktop`).

It opens to a centered welcome screen showing the app name, installed
version, and a short description, followed by a **Check token** button (reports
live DSC token detection in a popup) and an **Uninstall…** button.

### PKCS#11 modules and tokens (advanced)

Modules are detected automatically, so most users never touch this. When
several tokens are plugged in at once, or auto-detection is not enough, use
the **List Module** / **Token** picker / **Read Certificate** controls on the
[test page](dsc-bridge/test-page/index.html) — it lists every detected PKCS#11
module and lets you pick exactly which plugged-in token to read certificates
from and sign with.

Each entry shows the module's manufacturer, library and Cryptoki version, and
its live token state. A module that cannot drive your card reports "token not
present" or "token not recognised" even when the card is inserted and
completely free, so module error codes are **not** evidence about the card.

The web-facing API exposes a read-only `listModules()` and a `driver` argument
on `getCertificate()`/signing so a web page can pick a token but can never make
the host load an arbitrary library outside the detected set.

**Troubleshooting**

- *"Access to the native messaging host is forbidden"* → the loaded extension ID
  doesn't match the installer's. Confirm it is `klmgadogecbimgjkepdljfljajphemfl`.
- *"No PKCS#11 module available"* → install the token's 64-bit vendor driver,
  then click **Check token** in the companion app or **Run Diagnose** on the
  test page.
- *"A card is inserted … but no PKCS#11 module could open it"* (code
  `TOKEN_UNSUPPORTED_BY_MODULE`) → the card is present and **not** locked; the
  PKCS#11 library for that token is missing or is the wrong one. Install your DSC
  vendor's PKCS#11 library and attach it. If the token already works in Adobe
  Acrobat, open Acrobat's *Digital ID and Trusted Certificate Settings → PKCS#11
  Modules and Tokens* and attach the **exact same library path** here. Beware:
  some vendor tools (e.g. HyperPKI/EnterSafe Manager) drive the token over raw
  USB/IOKit instead of PKCS#11, so "it works in the vendor's own tool" does not
  prove a usable PKCS#11 module exists on the machine.
- *"holds a card that could not be opened … holding it exclusively"* (code
  `TOKEN_BUSY_OR_ABSENT`) → raised **only** when PC/SC actually reports a sharing
  violation, i.e. another process really does hold the card. Quit Adobe Acrobat
  (including its background *Adobe Acrobat Helper*), vendor token managers and
  other browsers, re-insert the token, and retry.
- *macOS: driver found but token not recognised* → some vendor drivers ship a
  dylib **inside** an app bundle that macOS Library Validation blocks from being
  loaded by another process. The host works around this by copying the driver to
  `~/Library/Application Support/Giddh/drivers/` and loading the copy; no action
  needed.
- *Installer won't open / silent* → SmartScreen (unsigned build): **More info →
  Run anyway**. Windows signing is wired up but inactive until credentials are
  added — see `docs/WINDOWS_SIGNING.md`.

## Domain restriction — locked for production

The bridge only responds to `https://*.giddh.com`, `https://*.erpdocs.com`, and
`localhost`/`127.0.0.1` (for local dev). This is controlled by ONE flag in
`extension/background.js`:

```js
const ALLOW_ALL_ORIGINS = false; // set true ONLY for local development/testing
```

`manifest.json`'s `content_scripts` / `web_accessible_resources` `matches` list
the same domains — keep both in sync when adding a new Giddh domain.

## Test on any machine

Same steps on your Mac or a fresh target (no Python needed on the target — the
installer bundles it; the machine only needs its **DSC token vendor driver**):

1. `chrome://extensions` → Developer mode → **Load unpacked** → `dsc-bridge/extension/`.
   (For a `file://` test page, also open **Details → Allow access to file URLs**.)
2. Build + install the OS installer:
   ```bash
   VERSION=1.7.0 ./packaging/macos/build_macos.sh
   open dist/GiddhDSCBridge-1.7.0.dmg      # run the .pkg (right-click → Open; it is unsigned)
   ```
3. Open `dsc-bridge/test-page/index.html` (or a page on an allowed domain,
   see "Domain restriction" above), plug in the token, read certs and sign.

> The native host **self-heals** stale token locks on start, so swapping the DSC
> token no longer requires manually deleting driver lock files.

## Build the installers

You can only build the installer for the OS you are on (PyInstaller cannot
cross-compile):

| OS      | Command                                   | Output                          |
|---------|-------------------------------------------|---------------------------------|
| macOS   | `./packaging/macos/build_macos.sh`        | `dist/GiddhDSCBridge-<ver>.dmg` |
| Windows | `./packaging/windows/build_windows.ps1`   | `dist/GiddhDSCBridge-Setup-<ver>.exe` (needs Inno Setup) |
| Linux   | `./packaging/linux/build_deb.sh`          | `dist/giddh-dsc-bridge_<ver>_<arch>.deb` |

**To get all three at once**, use GitHub Actions: `.github/workflows/build-packages.yml`
builds mac + Windows + Linux on a version tag:

```bash
git tag v1.7.0 && git push origin v1.7.0   # or run it manually from the Actions tab
```

### Where each installer puts things

| OS      | Artifact                         | Host installs to               | Companion app                         | Registers native host in            |
|---------|----------------------------------|--------------------------------|---------------------------------------|-------------------------------------|
| macOS   | `.dmg` (holds a `.pkg`)          | `/usr/local/giddh-dsc-bridge`  | `/Applications/Giddh DSC Bridge.app`  | `/Library/.../NativeMessagingHosts` (system)   |
| Windows | `GiddhDSCBridge-Setup-*.exe`     | `%LOCALAPPDATA%\Giddh DSC Bridge` | Start-menu **Giddh DSC Bridge**    | `HKCU\...\NativeMessagingHosts` (per-user) |
| Linux   | `.deb`                           | `/opt/giddh-dsc-bridge`        | app-menu **Giddh DSC Bridge** (`.desktop`) | `/etc/opt/.../native-messaging-hosts` (system) |

> macOS pkg components are built **non-relocatable** (`BundleIsRelocatable=false`)
> so the app always installs to `/Applications` instead of being diverted to a
> stray copy by Spotlight-based relocation.

## Notes

- The `.exe` **cannot** be built or tested on a Mac — use a Windows machine or CI.
- **Windows** installer + binaries: code signing (SSL.com eSigner) is wired up
  in CI but inactive until credentials are added — see `docs/WINDOWS_SIGNING.md`.
  Until then, macOS Gatekeeper / Windows SmartScreen will warn on the unsigned
  build. **macOS** notarization (`codesign` + `productsign` + `notarytool`) is
  not yet wired up.
- **Ship together:** the OS installer **and** the `dsc-bridge/extension/` folder.
  Build each installer on its own OS; the `.exe` cannot be built on a Mac.

## Production checklist

- [ ] `ALLOW_ALL_ORIGINS = false` in `background.js` (default) and `manifest.json`
      `matches` point at real domains (default: `giddh.com`, `erpdocs.com`).
- [ ] `packaging/extension-id.txt` matches the loaded extension's ID.
- [ ] Installer built + installed on each target OS; token driver present (64-bit).
- [ ] `test-page/index.html` (or the real app) reads certs and signs on each target.
- [ ] `giddh-extension-key.pem` backed up securely and NOT committed.
- [ ] Windows signing secrets (`ES_USERNAME`, `ES_PASSWORD`, `ES_CREDENTIAL_ID`,
      `ES_TOTP_SECRET`) added once ready — see `docs/WINDOWS_SIGNING.md`.

## AI agents / new contributors

Start with [`PROJECT.md`](PROJECT.md) — a single-file guide covering
architecture, conventions, build commands, and where things live.
