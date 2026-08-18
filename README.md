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
dist/            build output
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

1. **`GiddhDSCBridge-Setup-1.3.0.exe`** — the Windows installer.
2. **`dsc-bridge/extension/`** — the browser extension folder.

### Windows (no admin required)

1. **Run the installer** `GiddhDSCBridge-Setup-1.3.0.exe`. It is **unsigned**, so
   Windows SmartScreen may warn → click **More info → Run anyway**.
2. **Load the extension:** `chrome://extensions` → enable **Developer mode**
   (top-right) → **Load unpacked** → select the `dsc-bridge/extension/` folder.
   The ID must read `klmgadogecbimgjkepdljfljajphemfl`.
3. **Plug in the DSC token.** Its **vendor driver** must already be installed —
   it normally ships with the token. The bridge **auto-detects** the token across
   common vendors (WatchData/ProxKey/Capricorn, SafeNet/eToken, Feitian
   ePass2003, Longmai mToken, IDEMIA/IDPrime) — it probes each installed driver
   and uses the first with a token present.
4. **Test:** open `dsc-bridge/test-page/index.html` (or any page — this is a
   testing build), read the certificate, and sign. Only your **signing**
   certificate is listed (CA certs are filtered out); its issuing chain is sent
   to the server so Adobe can build the full trust path.

### macOS

1. **Open** `GiddhDSCBridge-1.3.0.dmg`, then run the `.pkg` inside. It is
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

It shows the installed version, whether the host is installed and registered
with the browser, a **Check token** button (detects the inserted token and its
signing certificate), and an **Uninstall** button that cleanly removes the bridge
(prompts for admin where required, then closes itself).

**Troubleshooting**

- *"Access to the native messaging host is forbidden"* → the loaded extension ID
  doesn't match the installer's. Confirm it is `klmgadogecbimgjkepdljfljajphemfl`.
- *"No PKCS#11 driver found"* → install the token's 64-bit vendor driver, then
  reopen the companion app and click **Check token**.
- *macOS: driver found but token not recognised* → some vendor drivers ship a
  dylib **inside** an app bundle that macOS Library Validation blocks from being
  loaded by another process. The host works around this by copying the driver to
  `~/Library/Application Support/Giddh/drivers/` and loading the copy; no action
  needed.
- *Installer won't open / silent* → SmartScreen (unsigned build): **More info →
  Run anyway**. Code-signing is the production TODO that removes this.

> **Note (test build):** this build accepts **any** page origin so it can be
> tested anywhere. Before a production release, lock the domains — see below.

## Domain restriction — OPEN for testing, LOCK before production

So the test page runs on any machine, the bridge currently works on **any page**
(any domain, `localhost`, `file://`). This is controlled by ONE flag in
`extension/background.js`:

```js
const ALLOW_ALL_ORIGINS = true; // TESTING ONLY — set false for production
```

**Before production, lock it down (2 steps):**
1. Set `ALLOW_ALL_ORIGINS = false` in `background.js`.
2. In `manifest.json`, change both `"matches": ["<all_urls>"]` back to the real
   domains (e.g. `"https://*.giddh.com/*"`). The allowlist is already prepared
   in `background.js` just below the flag.

## Test on any machine

Same steps on your Mac or a fresh target (no Python needed on the target — the
installer bundles it; the machine only needs its **DSC token vendor driver**):

1. `chrome://extensions` → Developer mode → **Load unpacked** → `dsc-bridge/extension/`.
   (For a `file://` test page, also open **Details → Allow access to file URLs**.)
2. Build + install the OS installer:
   ```bash
   VERSION=1.3.0 ./packaging/macos/build_macos.sh
   open dist/GiddhDSCBridge-1.3.0.dmg      # run the .pkg (right-click → Open; it is unsigned)
   ```
3. Open `dsc-bridge/test-page/index.html` (or any page while in testing mode),
   plug in the token, read certs and sign.

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
git tag v1.3.0 && git push origin v1.3.0   # or run it manually from the Actions tab
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
- Installers are **unsigned** → macOS Gatekeeper / Windows SmartScreen will warn.
  For public distribution (production TODO): **macOS** — `codesign` the binary +
  `productsign` the pkg with a Developer ID, then `notarytool` to notarize;
  **Windows** — Authenticode-sign `giddh-dsc-host.exe` and the setup `.exe`.
- **Ship together:** the OS installer **and** the `dsc-bridge/extension/` folder.
  Build each installer on its own OS; the `.exe` cannot be built on a Mac.

## Production checklist

- [ ] `ALLOW_ALL_ORIGINS = false` and `manifest.json` `matches` restored to real domains.
- [ ] `packaging/extension-id.txt` matches the loaded extension's ID.
- [ ] Installer built + installed on each target OS; token driver present (64-bit).
- [ ] `test-page/index.html` (or the real app) reads certs and signs on each target.
- [ ] `giddh-extension-key.pem` backed up securely and NOT committed.
