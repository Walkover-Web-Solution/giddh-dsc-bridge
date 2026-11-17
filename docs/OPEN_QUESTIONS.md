---
covers: ["*"]
verified_at: c591bc2
verified_on: 2026-11-17
---
# Open Questions & Known Ambiguities

Questions the repository does not answer, plus the security issues and defects found while writing this doc set. When one is resolved, update the doc that owns the answer and move it to **Resolved** with the date and source.

## Security Issues Found While Documenting (not fixed)

Init mode is docs only, so none of these was changed. The first one has a fix open in [PR #2](https://github.com/Walkover-Web-Solution/giddh-dsc-bridge/pull/2); ship it before anything else.

> [!WARNING]
> **Driver selection when signing.** At `c591bc2`, `IsolatedSigner.sign_hash` did not check the driver it routes to against the host's known drivers, as `list_certificates` does. The fix is in [PR #2](https://github.com/Walkover-Web-Solution/giddh-dsc-bridge/pull/2). Fuller details will be added here once that fix has shipped to users.

- **Shipped builds allow `file://` and `http://localhost` on any port** (`dsc-bridge/extension/background.js:_isAllowedOrigin`, `dsc-bridge/extension/manifest.json`). Any local page or local dev server can drive the token. <!-- verify:ignore -->
- **The bridge signs any hash an allowed page sends.** The only consent step is a PIN prompt that the page draws itself. The PIN and the results cross `window.postMessage(…, "*")` in the page's main world, so every script on an allowed page can see certificates and signatures.
- **The status app writes `/tmp/giddh-canvas-debug.log` on every render** (`dsc-bridge/native-host/giddh_dsc_status.py`).

## Active Questions Table

| ID | Topic / Question | Affected Code | Conflicting Evidence / Current Status | What Would Resolve It |
|---|---|---|---|---|
| Q-001 | Should production builds allow `file://` and `localhost`? | `dsc-bridge/extension/background.js`, `dsc-bridge/extension/manifest.json` | Useful for the test page, but it widens the trusted surface on every user's machine | Owner decision; possibly a separate dev build | <!-- verify:ignore -->
| Q-002 | Should `signHash` be tied to a server-issued nonce or prepared hash instead of signing any hash? | `dsc-bridge/extension/bridge-inject.js`, `dsc-bridge/INTEGRATION.md` | Any script on an allowed page can request a signature once it has the PIN | Owner + backend design decision |
| Q-003 | Should the companion app get "Check token", "Uninstall" and "Attach module" back, or should all copy stop mentioning them? | `dsc-bridge/native-host/giddh_dsc_status.py`, `pkcs11_signer.py` | Removed in `11810e8` and `fe15172`, but the `NO_DRIVER` error, the app's own warning, `README.md`, `PROJECT.md` and `INTEGRATION.md` still tell users to click them | Owner decision; then update the code or the copy |
| Q-004 | Keep or delete the module-config writers (`attach_module`, `set_preferred_module`)? | `dsc-bridge/native-host/pkcs11_signer.py` | No callers; the config file can only be edited by hand | Owner decision |
| Q-005 | What is the real version? | `VERSION`, `dsc-bridge/extension/manifest.json`, `dsc-bridge/extension/bridge-inject.js`, `dsc-bridge/native-host/giddh_dsc_host.py` | `VERSION`, the manifest and the inject script say 1.0.0; `ping` returns a hard-coded 1.1.0; `README.md` names 1.1.0 installers; history mentions 1.7.0 | Owner sets the version; `ping` should read `VERSION` |
| Q-006 | Should the timeouts be aligned? | `dsc-bridge/extension/background.js`, `pkcs11_signer.py` | Page: 30 s (certs) / 60 s (sign). Background: 55 s. Host: 45 s (list) / 180 s (sign). A slow sign reports `NATIVE_TIMEOUT` while the worker may still hold the token | Owner decision; one budget end to end |
| Q-007 | How should macOS users uninstall? | `packaging/macos/` | `DMG_README.txt` says to drag the `.app`, but a `.pkg` is shipped and leaves `/usr/local/giddh-dsc-bridge` and the system NativeMessagingHosts manifests behind | Owner decision; an uninstall script or app action |
| Q-008 | Which DSC token vendors are officially supported? | `pkcs11_signer.py` | The driver list is built from common Indian vendors; no support matrix exists | Owner / support list |
| Q-009 | Should `INTEGRATION.md`'s error shape be corrected? | `dsc-bridge/INTEGRATION.md`, `dsc-bridge/test-page/index.html` | It implies `{success:false}`, but failures reject the Promise with an Error carrying `.code`. The test page assumes the documented shape and hangs on "Reading…/Signing…" | Update the contract (and the Giddh web app if it copied the wrong shape) |
| Q-010 | Priorities and success measures (install success, signing success, supported vendors) | — | None written down | Owner input into [VISION.md](VISION.md) |
| Q-011 | Should a push to `main` publish to production? | `.github/workflows/build-packages.yml` | The owner described main as test builds and `v*` tags as releases. But the workflow treats every ref that isn't `test-*` as `prod`: a push to `main` uploads to S3 `prod` and promotes the Windows installer to `latest`. There's no `pull_request` trigger, so PRs build nothing | Owner decision; then change the workflow or the documented flow | <!-- verify:ignore -->
| Q-012 | Fix or drop `selftest.py`? | `dsc-bridge/native-host/selftest.py` | It imports `_select_driver`, which `giddh_dsc_host.py` has never had, so it exits 2. `INTEGRATION.md`'s checklist still requires it | Owner decision |
| Q-013 | Is Windows signing live? | `docs/WINDOWS_SIGNING.md`, `.github/workflows/build-packages.yml` | Signing runs only when the `ES_*` secrets are set; whether they are can't be seen from the repo, and the signature check that doc describes was removed in `f492206` | Owner confirms the secrets and updates the runbook |

## Known Technical Limitations & Verification Gaps

- **No DSC token was available.** Listing certificates, PIN entry and signing were not run. Flow and contract claims are read from the code.
- **No installer was built or run.** Builds per OS, code signing and notarisation were read from the scripts and CI ([DEVELOPMENT.md](DEVELOPMENT.md)).
- **Safe handling:** test with a real or test DSC token on each target OS, using `dsc-bridge/test-page/index.html`, before tagging a release.

## Resolved

| ID | Answer | Source |
|---|---|---|
| — | Users are staff at Giddh's customer businesses who sign documents with their own DSC token | Confirmed by the owner, 2026-11-17 |
| — | Intended ship flow: PR to `main` → CI test builds; `v*` tag → release; extension uploaded to the Chrome Web Store by hand. **CI doesn't match it yet (Q-011).** | Confirmed by the owner, 2026-11-17 |
