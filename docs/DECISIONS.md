---
covers: ["*"]
verified_at: c591bc2
verified_on: 2026-11-17
---
# Architectural & Design Decisions (ADRs)

Recovered from the 31 commits (2026-08-18 `da5db75` to 2026-09-03 `c591bc2`), their bodies,
`PROJECT.md` and `README.md`. No PRs or issues are in the repo. Where a commit gives no reason, the
"why" is labelled *inferred*. All commits are authored "Giddh DSC Bridge CI"; several from 2026-09-02
on are co-authored by Cursor. Five commits carry no rationale at all ("trigger: CI/CD with fixes",
"fix: ci/cd build issue" ×2, "refactor ui", "trigger: test macOS…").

## Decision Index

| Date | Decision Record | Status | Area Affected | Key Rationale |
|---|---|---|---|---|
| 2026-09-01 / 09-03 | [ADR-001: Use the Chrome Web Store extension ID everywhere](#adr-001-use-the-chrome-web-store-extension-id-everywhere) | Accepted | Extension / installers | One installer build must match the extension on every machine |
| 2026-08-20 → 08-24 | [ADR-002: Lock the origin allowlist to Giddh, localhost and file](#adr-002-lock-the-origin-allowlist-to-giddh-localhost-and-file) | Accepted | Extension security | Only Giddh pages may drive the token |
| ≤ 2026-08-18 / 08-20 | [ADR-003: Fresh host per call, one isolated worker per driver](#adr-003-fresh-host-per-call-one-isolated-worker-per-driver) | Accepted | Extension / host | Vendor PKCS#11 drivers wedge and crash |
| 2026-08-21 | [ADR-004: Aggregate all tokens; let the page pick one by driver](#adr-004-aggregate-all-tokens-let-the-page-pick-one-by-driver) | Accepted | Host / web API | Several tokens plugged in at once |
| 2026-08-31 | [ADR-005: Raw-hash signing on the test page](#adr-005-raw-hash-signing-on-the-test-page) | Accepted | Test page | Test server-prepared hashes directly |
| 2026-08-21 → 09-01 | [ADR-006: Shrink the status app; move module tools to the test page](#adr-006-shrink-the-status-app-move-module-tools-to-the-test-page) | Accepted | Status app | Disk-based checks gave false alarms |
| 2026-09-01 → 09-03 | [ADR-007: macOS ships a signed, notarized .pkg inside a DMG](#adr-007-macos-ships-a-signed-notarized-pkg-inside-a-dmg) | Accepted (supersedes 3 shapes) | macOS packaging | Gatekeeper; Apple validates nested code |
| 2026-09-03 | [ADR-008: The host runs from the system path, never a user-level copy](#adr-008-the-host-runs-from-the-system-path-never-a-user-level-copy) | Accepted | macOS install | Copies broke the code signature |
| 2026-08-21 | [ADR-009: Fail macOS builds that would ship a broken host](#adr-009-fail-macos-builds-that-would-ship-a-broken-host) | Accepted | Packaging | "Native host has exited" shipped |
| 2026-08-20 / 08-21 | [ADR-010: CI builds on main and test-*, uploads to S3](#adr-010-ci-builds-on-main-and-test--uploads-to-s3) | Accepted (conflicts with owner flow) | CI / release | Builds never ran on push |
| 2026-08-20 | [ADR-011: Windows signing with SSL.com eSigner between freeze and installer](#adr-011-windows-signing-with-sslcom-esigner-between-freeze-and-installer) | Accepted | Windows packaging | Match the Giddh Electron app |
| 2026-08-20 → 09-01 | [ADR-012: VERSION file as the source of truth; reset to 1.0.0](#adr-012-version-file-as-the-source-of-truth-reset-to-100) | Accepted (partly drifted) | Versioning | One number per release |
| 2026-08-20 / 08-21 | [ADR-013: Remove legacy prototypes](#adr-013-remove-legacy-prototypes) | Accepted | Repo | Dead code |

---

## ADR-001: Use the Chrome Web Store extension ID everywhere

### Status
Accepted. Supersedes the self-generated dev ID.

### Context & Problem Statement
The host manifest's `allowed_origins` must name the exact extension ID, or Chrome refuses with
"Access to the specified native messaging host is forbidden". Originally the ID came from the
manifest `key`, the public half of a local `giddh-extension-key.pem`, giving `klmgadogecbimgjkepdljfljajphemfl`.

### Decision & Implementation
- `d59ef5e` (2026-09-01): the store-assigned ID `pbnmboohmdoknhpflpmeocccojkkjgng` goes into
  `packaging/extension-id.txt` and the default `ExtId` in `packaging/windows/installer.iss`. The published zip
  carries no `key` (the store rejects it).
- `c591bc2` (2026-09-03): the unpacked manifest `key` was swapped for the store public key, so **Load
  unpacked** gets the store ID too (checked: hashing the key gives `pbnm…`). The ID is now one
  identifier everywhere. See [CONTRACTS.md](CONTRACTS.md).

### Supporting Evidence & Past Incidents
- `d59ef5e` "chore: migrate to Chrome Web Store extension ID"; `c591bc2` "Align unpacked extension ID with the Chrome Web Store ID".

### Consequences & Trade-offs
- **Positive:** one installer works with both the store and the dev copy.
- **Negative / Constraints:** CI can override the ID with the repo variable `GIDDH_EXT_ID`. If it is set to anything else, every installer breaks. `giddh-extension-key.pem` is now only a legacy key (`.gitignore`).

### Superseded / Rejected Alternatives
1. **Self-generated dev ID from the `.pem`** (initial to `d59ef5e`): superseded because the store assigns its own ID.
2. **"Build an installer that whitelists both IDs"** (README advice from `d59ef5e` to `c591bc2`): dropped once the dev key matched the store.

---

## ADR-002: Lock the origin allowlist to Giddh, localhost and file

### Status
Accepted.

### Context & Problem Statement
Any page that reaches `window.GiddhBridge` can ask the user's token to sign, so the set of pages is the security boundary.

### Decision & Implementation
- `41ca3a5` (2026-08-20): `ALLOW_ALL_ORIGINS` defaults to `false`; matches locked to `*.giddh.com`, `*.erpdocs.com`, localhost.
- `d023856` (2026-08-21): `file://` allowed in the manifest and in `background.js` so the standalone test page works. <!-- verify:ignore -->
- `23d5218` (2026-08-24): `erpdocs.com` removed; security notes added (the popup never messages the host; only listed keys are forwarded).
- Two gates must agree: the `matches` in `dsc-bridge/extension/manifest.json`, and `_isAllowedOrigin` in `dsc-bridge/extension/background.js`.

### Consequences & Trade-offs
- **Positive:** a single flag for development; defence in depth using `sender.origin`.
- **Negative / Constraints:** any local `file://` page (and any `localhost` app) can drive the token in the shipped extension. That is accepted for testing; see [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). <!-- verify:ignore -->

### Superseded / Rejected Alternatives
1. **Allow all origins:** development only.
2. **erpdocs.com:** removed without a stated reason.

---

## ADR-003: Fresh host per call, one isolated worker per driver

### Status
Accepted. In place at `da5db75`; the isolated worker arrived in `41ca3a5`/`561a743`.

### Context & Problem Statement
`C_Initialize` binds to the process and to the token present at that moment. A long-lived host goes
stale when tokens are hot-swapped, and a wedged host cannot be killed from the extension (comment at
the top of `dsc-bridge/extension/background.js`).

### Decision & Implementation
`background.js` opens a new `connectNative` port per request and runs one at a time (`_enqueueNative`).
`dsc-bridge/native-host/pkcs11_signer.py:IsolatedSigner` loads each driver in a subprocess started with `WORKER_FLAG`.
`connectNative` rather than `sendNativeMessage` keeps the MV3 worker alive for the reply.

### Consequences & Trade-offs
- **Positive:** a crashing driver cannot take the host down; every call gets a clean token state.
- **Negative / Constraints:** every call pays process start-up plus a token read; calls are serialised.

### Superseded / Rejected Alternatives
1. **Shared long-lived port:** rejected for the stale state and hangs described above.

---

## ADR-004: Aggregate all tokens; let the page pick one by driver

### Status
Accepted.

### Context & Problem Statement
With several tokens plugged in, `list_certificates()` returned whichever driver answered first (`4c1d263`).

### Decision & Implementation
- `4c1d263`: probe every candidate driver, merge their certificates, and tag each `certId` with its source driver so `sign_hash` routes straight to it. Untagged IDs still probe every driver.
- `e8f3c31`: optional `driver` argument on `getCertificate`, threaded page → content script → background → host. **It is validated against the host's own detected driver paths**, so a page cannot make the host `dlopen` an arbitrary library.
- `f947f06`: the picker labels tokens by certificate owner (`CertInfo.driverPath`), not vendor and model.

### Consequences & Trade-offs
- **Positive:** behaves like Adobe Acrobat's digital-ID list.
- **Negative / Constraints:** listing touches every driver, so it is slower.

---

## ADR-005: Raw-hash signing on the test page

### Status
Accepted (`1e3796b`, 2026-08-31).

### Decision & Implementation
A checkbox on `dsc-bridge/test-page/index.html` sends a pasted base64 SHA-256 as-is instead of hashing the text. The host API was unchanged: `signHash` always signs a caller-supplied hash.

### Consequences & Trade-offs
- **Positive:** a hash prepared by the server (`/dsc/prepare`, see `dsc-bridge/INTEGRATION.md`) can be signed by hand.
- **Negative / Constraints:** this makes explicit what was always true: an allowed page can get any hash signed once the user enters the PIN. The user cannot see what the hash is.

---

## ADR-006: Shrink the status app; move module tools to the test page

### Status
Accepted.

### Context & Problem Statement
The status app's disk-path checks were only right just after a fresh install; later they showed a permanent red "not installed" (`f947f06`).

### Decision & Implementation
- `f947f06` (2026-08-21): the home-screen status card was removed; "Check token" ran a live diagnose.
- `fe15172` (2026-08-21): the PKCS#11 module manager (attach/detach/set-default/rescan) was removed. The test page's `listModules()` and `getCertificate(driver)` replace it.
- `11810e8` (2026-09-01): "remove native host integration from status app" removed `run_host_diagnose`, `is_host_installed` and related code. **No Check token or Uninstall button remains in `dsc-bridge/native-host/giddh_dsc_status.py` at `c591bc2`** (only About and Quit), although its warning text and `README.md`/`PROJECT.md` still mention them. <!-- verify:ignore -->

### Consequences & Trade-offs
- **Positive:** no false alarms.
- **Negative / Constraints:** the host still reads the shared config for attached modules, but nothing in the repo writes it any more. Users have no in-app uninstall.

### Superseded / Rejected Alternatives
1. **Persistent status card:** removed as unreliable.
2. **In-app module manager:** the only UI allowed to add a library path (the "desktop-only attach" rule in `PROJECT.md` §4). Now gone; see [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

---

## ADR-007: macOS ships a signed, notarized .pkg inside a DMG

### Status
Accepted. The final shape after five changes in three days.

### Context & Problem Statement
Unsigned installers make users right-click → Open. Apple notarizes only fully signed content,
including nested code and an inner `.pkg`, and the Developer ID **Installer** certificate was not
available at first.

### Decision & Implementation
Order in `packaging/macos/build_macos.sh`, then the workflow:
1. Sign every nested `.so`/`.dylib`/`Python` in `_internal` (`sign_nested_binaries`), with hardened runtime and timestamp.
2. Sign the host and the `.app` with `packaging/macos/entitlements.plist` (`com.apple.security.cs.disable-library-validation`, so vendor drivers signed by other teams can load: `c591bc2`).
3. `pkgbuild` with every bundle non-relocatable, then `productsign` with Developer ID Installer.
4. `notarytool submit --wait`, then `stapler staple` the `.pkg`.
5. Wrap in a DMG; CI signs, notarizes and staples the DMG, then runs `spctl --assess`.
Without an Installer identity the `.pkg` and DMG ship unsigned, with a warning.

### Supporting Evidence & Past Incidents
| Date | Commit | Shape |
|---|---|---|
| 2026-08-18 | `da5db75` | Unsigned `.pkg` in DMG |
| 2026-09-01 | `11810e8` | Sign `.app` inside the DMG, notarize the DMG |
| 2026-09-01 | `3bad943` | DMG holds only the `.app`; on first launch it copies the host into `~/Library/Application Support` "to avoid an installer package and therefore a Developer ID Installer certificate" |
| 2026-09-01 | `d362103` | Signed `.pkg` inside DMG |
| 2026-09-02 | `96935c7` | `productsign` + notarize `.pkg`; non-relocatable bundles |
| 2026-09-02 | `6ac1b67` | Apple rejected unsigned or timestamp-less nested libs: deep-sign |
| 2026-09-02 | `e19f8f9` | No Installer cert, so ship the signed `.app` as a fallback |
| 2026-09-02 | `dc026cc` | Back to the `.pkg`; the `.app` looked for the bundled host in the wrong folder |
| 2026-09-03 | `c591bc2` | Entitlements; stop the user-level copy (ADR-008) |

### Consequences & Trade-offs
- **Positive:** with both certificates, the install passes Gatekeeper.
- **Negative / Constraints:** library validation is off for the host and the app, which is needed for vendor drivers. `packaging/macos/DMG_README.txt` (shipped as "READ ME FIRST.txt") still describes the `3bad943` drag-the-app flow.

### Superseded / Rejected Alternatives
1. **App-only DMG with self-install** (`3bad943`, `e19f8f9`): it broke the signature (ADR-008) and the host lookup.
2. **Unsigned `.pkg`:** still the fallback without an Installer cert.

---

## ADR-008: The host runs from the system path, never a user-level copy

### Status
Accepted (`c591bc2`, 2026-09-03).

### Context & Problem Statement
The status app copied the signed host into `~/Library/Application Support/Giddh DSC Bridge`. That
invalidated the `Python` framework signature, and Chrome's user-level manifest took priority over the
working system one, giving "Native host has exited".

### Decision & Implementation
`packaging/macos/scripts/postinstall` registers system manifests under `/Library/...`, then deletes
the old user-level copies and manifests for **every** user. `dsc-bridge/native-host/giddh_dsc_status.py:install_native_host`
does nothing when `/usr/local/giddh-dsc-bridge/giddh-dsc-host` exists. Otherwise it points the manifests at
the host inside the `.app` and never copies it.

### Consequences & Trade-offs
- **Negative / Constraints:** installing the `.pkg`, or running the status app, removes a developer's user-level dev manifest ([DEVELOPMENT.md](DEVELOPMENT.md)).

---

## ADR-009: Fail macOS builds that would ship a broken host

### Status
Accepted.

### Decision & Implementation
- `7987453` (2026-08-21): `sync`, then check that `_internal/Python` resolves, because PyInstaller's COLLECT can return early.
- `f7f00a0` (2026-08-21): run the staged host with `--diagnose` as a smoke test; fix a `find` precedence bug.
- `d023856` (2026-08-21): `packaging/macos/scripts/preinstall` wipes the previous install, so old `Python.framework` versions cannot leave a dangling symlink.
- `96935c7`: `BundleIsRelocatable=false`, so Spotlight cannot divert the `.app` to a stray copy.

---

## ADR-010: CI builds on main and test-*, uploads to S3

### Status
Accepted in code; **conflicts with the owner's flow** (confirmed by the owner, 2026-11-17: a PR to `main` builds test installers and a `v*` tag publishes release installers).

### Decision & Implementation
- `41ca3a5` (2026-08-20): the `upload_to_s3` job writes `s3://$S3_BUCKET/<env>/<os>/<version>/`; on `prod` it also writes `latest/`. Env is `test` for `test-*` refs, else `prod`. <!-- verify:ignore -->
- `f95ba6f` (2026-08-21): push triggers on `main` and `test-*` were added because "pushes to main never started a build".
- `af32708`: download paths fixed to the artifact names `giddh-dsc-bridge-<os>`.

### Consequences & Trade-offs
- **Negative / Constraints:** every push to `main` overwrites the prod `latest` installers, and PRs build nothing. See [DEVELOPMENT.md](DEVELOPMENT.md#pr-workflow--deployment-pipeline).

---

## ADR-011: Windows signing with SSL.com eSigner between freeze and installer

### Status
Accepted. Whether it is active is unknown.

### Decision & Implementation
`41ca3a5`: split into `packaging/windows/freeze.ps1` → sign both `.exe` → `packaging/windows/build_installer.ps1` → sign the installer, with `SSLcom/esigner-codesign@v1.3.2`, "mirroring Giddh's main Electron app". The four `ES_*` secrets gate it. Fixes: `7c94b03` (probe `python`/`python3`/`py` for the interpreter that imports PyInstaller, so `py -3` stops picking 3.14) and `a23f5a6` (create `artifacts/` first). `f492206` removed the `Get-AuthenticodeSignature` checks and the "building UNSIGNED" log. Runbook: [WINDOWS_SIGNING.md](WINDOWS_SIGNING.md), which is stale on both points. <!-- verify:ignore -->

---

## ADR-012: VERSION file as the source of truth; reset to 1.0.0

### Status
Accepted; partly drifted.

### Decision & Implementation
`41ca3a5` made `VERSION` the single source for specs, scripts and the status app; the extension's
`manifest.json` and `bridge-inject.js` are bumped by hand. `d023856` bumped to 1.7.0. `1e3796b` and
`d59ef5e` reset to 1.0.0 for the store release. `992332f` ("refactor ui") changed the hard-coded
`_handle_ping` and `packaging/windows/version_info.txt` to 1.1.0, so they now disagree with `VERSION` ([DEVELOPMENT.md](DEVELOPMENT.md#versioning)).

---

## ADR-013: Remove legacy prototypes

### Status
Accepted. `561a743` removed `dsc_signing/` (a legacy signing package) and `dsc-bridge/test-server/` <!-- verify:ignore -->
(a Flask prototype); `9922f05` removed `dsc-bridge/extension.zip` (an old unpacked build with no key material). Do not resurrect them; the server side lives in the Giddh backend (`dsc-bridge/INTEGRATION.md`). <!-- verify:ignore -->

---

## Lessons Learned & Incident Log

- **Incident / Bug:** "Native host has exited" on macOS, three separate times.
  - **Root Cause:** the Python shared library was not written yet (`7987453`); stale framework debris from an earlier install (`d023856`); a user-level host copy that broke the signature (`c591bc2`).
  - **Prevention Rule:** keep the smoke test, `preinstall` and the postinstall scrub. Never copy a signed PyInstaller bundle.
- **Incident / Bug:** the status app always said "not installed".
  - **Root Cause:** it looked for `giddh_dsc_host` (underscore) but the build produces `giddh-dsc-host`, and it searched the wrong directories (`f7f00a0`). Later the bundled host was looked up in `Contents/MacOS` instead of `Contents/Frameworks` (`dc026cc`).
  - **Prevention Rule:** take names and paths from the PyInstaller specs; check a built bundle, not the source tree.
- **Incident / Bug:** notarization rejected.
  - **Root Cause:** unsigned nested `.so`/`.dylib` files (`6ac1b67`); an unsigned inner `.pkg` (`e19f8f9`).
  - **Prevention Rule:** sign inside-out. A DMG wrapping an unsigned `.pkg` cannot be notarized.
- **Incident / Bug:** "blank window" in macOS dark mode.
  - **Root Cause:** Tk 8.5 drew black text on black (`41ca3a5`).
  - **Prevention Rule:** keep `_apply_palette` and `NSRequiresAquaSystemAppearance` ([DESIGN.md](DESIGN.md)).
- **Incident / Bug:** `selftest.py` cannot run.
  - **Root Cause:** it imports `_select_driver`, which no commit of `giddh_dsc_host.py` has.
  - **Prevention Rule:** run it after changing the signer's public functions.
- **Pattern:** nine CI commits in 48 hours were titled "trigger"/"fix: ci/cd build issue" and carried no rationale. Push to a `test-*` branch and write down why in the commit body.
