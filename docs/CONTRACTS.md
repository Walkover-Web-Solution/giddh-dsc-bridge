---
covers: ["dsc-bridge/extension/**", "dsc-bridge/native-host/giddh_dsc_host.py", "dsc-bridge/native-host/pkcs11_signer.py", "dsc-bridge/INTEGRATION.md", "packaging/extension-id.txt"]
verified_at: c591bc2
verified_on: 2026-11-17
---
# Contracts & Data Schemas

This repo has no HTTP server and no shared type files. Its contracts are message shapes on four hops:

```
Giddh page ──window.GiddhBridge──► bridge-inject.js ──postMessage──► content-script.js
  ──chrome.runtime.sendMessage──► background.js ──native messaging (stdio)──► giddh_dsc_host.py
  ──subprocess stdin/stdout JSON──► worker (same binary, --pkcs11-worker) ──PKCS#11──► token
```

The contract with the Giddh web app and backend is [`dsc-bridge/INTEGRATION.md`](../dsc-bridge/INTEGRATION.md). It stays the authoritative document for Giddh's side. This file summarises it, records where the code disagrees with it, and owns everything below the page. Where they differ, **the code wins**: the page receives what is described here.

## Authoritative Type Definitions

The shapes are plain dicts and JS objects, so the source of truth is the code that builds them.

| Domain Entity | Primary TS Type / Interface | Source File | Description |
|---|---|---|---|
| Certificate | `CertInfo.to_dict` | `dsc-bridge/native-host/pkcs11_signer.py:CertInfo` | One signing certificate from a token, returned by `getCertificate` |
| Module row | dict from `_worker_probe` | `dsc-bridge/native-host/pkcs11_signer.py:_worker_probe` | One PKCS#11 library with its load state, slots and tokens |
| Reader state | dict from `pcsc_reader_states` | `dsc-bridge/native-host/pkcs11_signer.py:pcsc_reader_states` | What PC/SC reports for each smartcard reader |
| Typed error | `Pkcs11Error(message, code)` | `dsc-bridge/native-host/pkcs11_signer.py:Pkcs11Error` | Every host failure carries a machine `code` |
| Bridge config | JSON file | `dsc-bridge/native-host/pkcs11_signer.py:config_path` | Attached and pinned modules (see Persistence) |

## Giddh integration contract (summary of INTEGRATION.md)

Deferred PAdES: the server prepares a hash, the token signs it, and the server embeds the signature. The bridge only reads certificates and signs a hash.

| Step | Caller → callee | Contract |
|---|---|---|
| 1 | page → bridge | `getCertificate()` returns signing certificates with their CA `chain` |
| 2 | page → Giddh server | `POST /<access_token>/dsc/prepare` `{certificate, chain, signature_image?}` → `{nonce, hash_b64, md_algorithm, cert, name_match_score}` <!-- verify:ignore --> |
| 3 | page → bridge | `signHash(hash_b64, "SHA256", certId, pin)` returns `{signature}` |
| 4 | page → Giddh server | `POST /<access_token>/dsc/finish` `{nonce, signature, chain?}` → `{success, pades_level, verified}` <!-- verify:ignore --> |

The server endpoints, the PAdES engine and the orchestrator `giddh/static/js/dsc-signing.js` (`window.GiddhDSC.signFlow`) live in the Giddh app, not here. <!-- verify:ignore -->

### Where INTEGRATION.md disagrees with the code (c591bc2)

| # | INTEGRATION.md says | Code does | Evidence |
|---|---|---|---|
| 1 | `window.GiddhBridge` is "injected on every page" | Injected only on pages the manifest matches: `https://*.giddh.com`, `http(s)://localhost`, `http(s)://127.0.0.1` and `file:///*` | `dsc-bridge/extension/manifest.json` |
| 2 | `const { success, certificates } = await getCertificate()` suggests failures resolve with `success: false` | A failure **rejects** with an `Error` whose `.code` holds the code. Only success resolves, with the host's full reply (`success: true` included) | `dsc-bridge/extension/bridge-inject.js`, `dsc-bridge/extension/content-script.js` |
| 3 | `getCertificate()` takes no argument | Takes an optional `driver` path that limits the read to one detected module | `dsc-bridge/extension/bridge-inject.js` |
| 4 | Certificate fields stop at `chain` | Each certificate also has `driverPath`. `certId` is opaque: `<base64url driver path>.<hex CKA_ID>`, not plain hex | `dsc-bridge/native-host/pkcs11_signer.py:_encode_cert_id` |
| 5 | `listModules()` returns `{success, modules, readers, preferred, strict}` | Also returns `configPath`, `detected` and `attached`. `preferred` is a path or `null`. A row that fails to probe carries `error` | `dsc-bridge/native-host/pkcs11_signer.py:IsolatedSigner` (`list_modules`) |
| 6 | Attaching or pinning a module "happens only in the desktop companion app" | The companion app has had no module UI since `fe15172`. `attach_module` and `set_preferred_module` have no callers, so the config file can only be edited by hand | `dsc-bridge/native-host/giddh_dsc_status.py` |
| 7 | "Message protocol and error codes are documented in README.md" | README documents neither. They are documented in this file | `README.md` |
| 8 | A page "can never make the native host dlopen an arbitrary library" | **False for `signHash`.** The driver path inside a page-supplied `certId` is decoded and loaded if the file exists. It is not checked against the detected set (see the warning below) | `dsc-bridge/native-host/pkcs11_signer.py:_decode_cert_id` |
| 9 | `TOKEN_BUSY_OR_ABSENT` means "another app holds the card" | It also means an unresponsive card ("re-insert it") | `dsc-bridge/native-host/pkcs11_signer.py:_classify_card_failure` |
| 10 | Page timeouts are 30 s for certificates and 60 s for signing (comment in `background.js`) | The background gives up at 55 s. The host's certificate budget is 45 s and its signing budget is 180 s. A 30 s page timeout can fire before the host answers, and a sign that takes over 55 s (a PIN pad, or several drivers) is cut off by the background | `dsc-bridge/extension/background.js:NATIVE_OP_TIMEOUT_MS`, `dsc-bridge/native-host/pkcs11_signer.py:_SIGN_BUDGET` |
| 11 | Checklist: "`selftest.py` passes all 5 stages" | **Cannot pass:** 5 stages by design (3 with `--no-sign`), but the script exits 2 at import at `c591bc2` (Q-012) | `dsc-bridge/native-host/selftest.py` |

> [!WARNING]
> **A crafted `certId` makes the host load any library on disk.** `IsolatedSigner.sign_hash` passes `_decode_cert_id(certId)` straight to `_run_on_token(only_driver=…)`. The worker then `dlopen`s that path, which runs the library's initialisers, before any PIN check reaches the token. Any allowed origin can do this, including any `localhost` port and any local file:// page, provided it sends a non-empty PIN and a valid 32-byte hash. `list_certificates` validates `only_driver` against `self.driver_paths`. `sign_hash` must do the same before it routes. Until it does, do not describe the web API as unable to load arbitrary libraries.

## Browser client API: `window.GiddhBridge`

Defined in `dsc-bridge/extension/bridge-inject.js`. Every method returns a Promise. On success the Promise resolves with the host reply object. On failure it rejects with `Error(message)` and sets `err.code`.

| Member | Args | Resolves with | Notes |
|---|---|---|---|
| `isAvailable()` | none | `true` (synchronous) | Use `!!window.GiddhBridge` to detect the bridge |
| `version` | none | `"1.0.0"` | Set by hand. Keep it equal to `manifest.json` `version`. Lets a page detect a stale injected copy |
| `getCertificate(driver?)` | optional driver path from `listModules` | `{success, certificates: Cert[]}` | No PIN on most tokens. Without `driver`, reads every detected token and merges the results |
| `signHash(hashB64, algorithm, certId, pin)` | algorithm `SHA256`, `SHA384` or `SHA512` (default `SHA256`) | `{success, signature}` | `pin` is required (`PIN_REQUIRED` if empty). `certId` must be echoed back unchanged from `getCertificate` |
| `diagnose()` | none | `{success, diagnostics}` | Support report (see Native host commands) |
| `listModules()` | none | `{success, configPath, preferred, strict, detected, attached, readers, modules}` | Read only. Probes every module, so it is slow |

The page can learn the bridge is ready from the `giddh-dsc-bridge-ready` window event, which `bridge-inject.js` dispatches once it has run.

```json
// Cert (CertInfo.to_dict); only non-CA certificates are returned
{
  "certId": "L0xpYnJhcnkv...ZHlsaWI.0a1b2c",   // opaque: b64url(driver path) + "." + hex CKA_ID
  "certB64": "MIIF...",                        // DER, base64
  "subjectCn": "string|null",                  // null if the subject has no CN
  "issuerCn": "string|null",
  "serial": "string|null",                     // hex
  "notBefore": "string|null", "notAfter": "string|null",
  "isCa": false,
  "chain": ["<b64 DER CA>"],                   // CA certs read off the same token; may be []
  "driverPath": "/path/to/module"              // which module the cert was read from
}
```

## Page ↔ content script (`window.postMessage`)

| Direction | Shape | Checked by |
|---|---|---|
| page → CS | `{__giddhDsc: true, id, action, data: {hash?, algorithm?, certId?, pin?, driver?}}` | `content-script.js` accepts only `event.source === window` (the top document posting to itself; no iframes, because `all_frames: false`) |
| CS → page | `{__giddhDscResp: true, id, result}` or `{__giddhDscResp: true, id, error, code}` | `bridge-inject.js` accepts only `event.source === window` and an `id` it is still waiting on |

- Both sides post with `targetOrigin "*"`. Because the message goes to the same window, any script on the page, third-party scripts included, can read replies: certificates and signatures. A script can also post requests directly, since the content script forwards **any** `action` string (for example `ping`, which has no wrapper method).
- The content script builds the message from an allowlist of fields: `hash`, `algorithm`, `certId`, `pin`, `driver`. Errors it raises itself: `SW_UNAVAILABLE` (the service worker is gone) and `CONTEXT_INVALIDATED` (the extension was reloaded; the page must be refreshed).

## Content script ↔ background

- Request: `{type: "GIDDH_DSC", action, hash?, algorithm?, certId?, pin?, driver?}` through `chrome.runtime.sendMessage`. The response is the host reply, passed through unchanged.
- Origin gate: `dsc-bridge/extension/background.js:_isAllowedOrigin` reads `sender.origin`, falling back to the origin of `sender.url`. Any other origin gets `ORIGIN_NOT_ALLOWED`. The background copies the same field allowlist again and does no type or length checks.
- Messages from the extension's own pages (no `sender.tab`) are not special-cased. The popup never sends one; see the comment in `dsc-bridge/extension/popup.js`.
- Calls run one at a time (`_enqueueNative`). Each call gets a **fresh host process** (`connectNative` per call, `_nativeCall`), and the background stops waiting after `NATIVE_OP_TIMEOUT_MS` (55 s). See [DECISIONS](DECISIONS.md) for why.

## Background ↔ native host (Chrome native messaging)

| Item | Value | Source |
|---|---|---|
| Host name | `com.giddh.dsc.bridge` | `dsc-bridge/extension/background.js:NATIVE_HOST_NAME`, packaging scripts |
| Framing | 4-byte little-endian length + UTF-8 JSON, both directions. stdout carries framed replies only; logs go to stderr | `dsc-bridge/native-host/giddh_dsc_host.py:_read_message`, `_send_message` |
| Max inbound | The host drops a message of 0 bytes or over 10 MiB, then exits its loop | `dsc-bridge/native-host/giddh_dsc_host.py:_read_message` |
| Max outbound | Chrome caps host → browser messages at 1 MB. The host does not check this (a `listModules` reply is the largest) <!-- verify:ignore --> | Chrome platform limit |
| Host manifest | `{"name","description","path","type":"stdio","allowed_origins":["chrome-extension://<EXT_ID>/"]}` | `packaging/macos/build_macos.sh`, `packaging/linux/build_deb.sh`, `packaging/windows/installer.iss`, `dsc-bridge/native-host/giddh_dsc_status.py:install_native_host` |

Background-side errors: `NATIVE_START_FAILED`, `NATIVE_SEND_FAILED`, `NATIVE_DISCONNECTED` (the host crashed or Chrome refused it), `EMPTY_RESPONSE`, `NATIVE_TIMEOUT`.

## Native host commands

Dispatch is in `dsc-bridge/native-host/giddh_dsc_host.py:main`. Every reply is `{"success": true, ...}` or `{"success": false, "error": "<user-facing text>", "code": "<CODE>"}`.

| `action` | Input | Success reply | Typical codes |
|---|---|---|---|
| `ping` | none | `{pong: true, version: "1.1.0"}` (hardcoded; does not match `VERSION` 1.0.0) | none |
| `diagnose` | none | `{diagnostics: {platform, python_arch, python_version, isolated_workers, config_path, pcsc_readers, attached_modules, preferred_module, strict_module, candidates, ranked, active_driver, providers[], driver_path, driver_loads, arch_mismatch, slots, tokens, error}}` | `INTERNAL` |
| `listModules` | none | see the API table | `INTERNAL` |
| `getCertificate` | `driver?` (ignored unless it is in the detected set) | `{certificates: Cert[]}` | `NO_DRIVER`, `NO_CERTS`, `NO_TOKEN`, `TOKEN_BUSY_OR_ABSENT`, `TOKEN_UNSUPPORTED_BY_MODULE`, `PIN_REQUIRED`, `TIMEOUT` |
| `signHash` | `hash`, `algorithm`, `certId`, `pin` | `{signature}` | `BAD_REQUEST`, `PIN_REQUIRED`, `PIN_INCORRECT`, `PIN_LOCKED`, `NO_CERT_ID`, `NO_PRIVATE_KEY`, `BAD_HASH`, `BAD_ALGO`, `SIGN_FAILED`, plus the token codes |
| anything else | none | none | `BAD_ACTION` |

The CLI flag `--diagnose` prints one unframed JSON `diagnose` reply and exits. Only the macOS build smoke test calls it (`packaging/macos/build_macos.sh`). `--pkcs11-worker` (`WORKER_FLAG`) is internal.

**Worker protocol** (host → `worker_main`, one process per driver): stdin `{"op": "probe"|"diagnose_one"|"list_certs"|"sign", "driver", hash?, algorithm?, certId?, pin?}`, stdout last line `{"ok": true, ...}` or `{"ok": false, "code", "error", "readers"}`. The PIN goes over stdin, never argv. Timeouts: 25 s per probe or list (`_PROBE_TIMEOUT`), 120 s per sign (`_SIGN_TIMEOUT`). Total budgets: 45 s for a list (`_LIST_BUDGET`), 180 s for a sign (`_SIGN_BUDGET`). When PC/SC reports the card as locked, the pass is retried once (`_BUSY_RETRIES`).

### Error codes (complete list)

| Code | Raised by | Meaning / user fix |
|---|---|---|
| `ORIGIN_NOT_ALLOWED` | background | The page is not on the allowlist |
| `SW_UNAVAILABLE`, `CONTEXT_INVALIDATED` | content script | The extension restarted or was reloaded; refresh the page |
| `NATIVE_START_FAILED`, `NATIVE_SEND_FAILED`, `NATIVE_DISCONNECTED`, `EMPTY_RESPONSE`, `NATIVE_TIMEOUT` | background | The host is not installed, was refused (extension ID mismatch), crashed, or hung |
| `BAD_ACTION`, `BAD_REQUEST`, `INTERNAL` | host | A caller bug or an unexpected exception |
| `NO_DRIVER`, `NO_MODULE` | host / signer | No PKCS#11 library found, or `python-pkcs11` is missing from the build |
| `NO_TOKEN`, `NO_CERTS` | signer / host | No card inserted, or the card holds no signing certificate |
| `TOKEN_BUSY_OR_ABSENT` | `_classify_card_failure` | PC/SC reports the card locked by another app, or unresponsive |
| `TOKEN_UNSUPPORTED_BY_MODULE` | `_classify_card_failure` | The card is present and free, but no installed module can drive it |
| `DRIVER_LOAD_FAILED`, `ARCH_MISMATCH`, `DRIVER_CRASHED`, `DRIVER_TIMEOUT`, `WORKER_SPAWN_FAILED`, `WORKER_BAD_REPLY`, `SESSION_FAILED`, `TIMEOUT` | signer | Driver or process-level failures, mostly per driver. `TIMEOUT` means the whole budget ran out |
| `PIN_REQUIRED`, `PIN_INCORRECT`, `PIN_LOCKED` | signer / host | `PIN_LOCKED` is permanent until the user's DSC provider unblocks the token |
| `NO_CERT_ID`, `NO_PRIVATE_KEY`, `BAD_HASH`, `BAD_ALGO`, `SIGN_FAILED` | signer | Bad sign input, or a key that is missing or unusable |
| `PKCS11_ERROR` | `Pkcs11Error` default | Not raised explicitly |
| `SLOT_ENUM_FAILED` | none | Listed in `_run_on_token` but never raised |

The `error` strings are written for end users and name a fix. INTEGRATION.md tells pages to show them as they are, so changing one changes Giddh's UI.

## Signature output format

- `signature` is base64 of a **raw RSASSA-PKCS#1 v1.5** signature: `CKM_RSA_PKCS` over a DigestInfo that `_wrap_digest_info` builds from the supplied hash. It is not CMS and not PAdES. Giddh's server builds the CMS and embeds it as PAdES-B-B.
- Only RSA keys work. There is no ECDSA or PSS mechanism.
- The hash is pre-computed by the caller. Its length is checked: 32, 48 or 64 bytes for `SHA256`, `SHA384` or `SHA512` (`SHA-256` and similar spellings are also accepted).
- The bridge signs **whatever hash it is given**. Nothing ties the hash to a Giddh document. The PIN is the only user consent.

## Extension identity and origin allowlist

| Item | Value | Source |
|---|---|---|
| Extension ID | `pbnmboohmdoknhpflpmeocccojkkjgng` | `packaging/extension-id.txt` |
| Manifest `key` | The public key of the Chrome Web Store item, so an unpacked load gets the same ID. The store rejects `key`, so the published zip drops it (README) | `dsc-bridge/extension/manifest.json` |
| `permissions` | `nativeMessaging` only; no host_permissions | `manifest.json` |
| `content_scripts.matches` and `web_accessible_resources.matches` | `https://*.giddh.com/*`, `http://localhost/*`, `http://127.0.0.1/*`, `https://localhost/*`, `https://127.0.0.1/*`, `file:///*`; `run_at: document_idle`, `all_frames: false` | `manifest.json` |
| Background allowlist | `ALLOWED_HOSTS_EXACT` (`giddh.com`), `ALLOWED_HOST_SUFFIXES` (`.giddh.com`, HTTPS only), `ALLOW_LOCALHOST` (http or https, any port), `ALLOW_ALL_ORIGINS = false` | `dsc-bridge/extension/background.js:_isAllowedOrigin` |
| file:// | Allowed when `sender.url` starts with file://, because file pages have an opaque origin. Chrome also requires "Allow access to file URLs" on the extension. This lets **any** local HTML file the user opens use the token | `dsc-bridge/extension/background.js:_isAllowedOrigin` |

Adding a Giddh domain means editing both `manifest.json` match lists and the `background.js` constants together. Whether the store build keeps `file:///*` and `localhost` is an open question ([OPEN_QUESTIONS](OPEN_QUESTIONS.md)).

## PKCS#11 drivers (auto-detection)

Detection comes from `list_driver_candidates`: the static paths in `_DRIVER_CANDIDATES` that exist on disk, then the globs in `_glob_driver_candidates`, then (Windows only) `_win32_registry_candidates`. The full static path list is in `_DRIVER_CANDIDATES`; edit that list, never the host.

| Vendor family | macOS | Windows | Linux |
|---|---|---|---|
| WatchData / ProxKey (Capricorn) | `libwdpkcs_Proxkey.dylib`, `libwdpkcs11.dylib` | `wdpkcs11.dll`, `libwdpkcs_Proxkey.dll`, `WDPKCS.dll` (PROXKey CSP India V3.0) | `libwdpkcs11.so` |
| SafeNet / Gemalto / Thales eToken | `libeTPkcs11.dylib`, eToken.framework | `eTPkcs11.dll` | `libeTPkcs11.so` |
| Feitian ePass2003 / mToken (eMudhra, Capricorn) | `libcastle*.dylib`, `eps2003csp11.framework`, `libePass2003.dylib` | `eps2003csp11(v2).dll`, `ShuttleCsp11_3003.dll`, `SignatureP11.dll`, `castle.dll` | glob only: `libcastle*.so`, `*eps2003*` |
| Longmai mToken CryptoID | `libcryptoid_pkcs11.dylib` (also inside `CryptoIDATools.app`) | `CryptoIDA_pkcs11.dll`, `mtoken_gm3000.dll` | none |
| IDEMIA / Gemalto IDPrime | `libIDPrimePKCS11.dylib` | `idprimepkcs11.dll` | none |
| TrustKey / Aladdin / GClib / Moserbaer | `libtrustkey.dylib` | `trustkey.dll`, `gclib.dll`, `pkcs11.dll` | none |
| OpenSC (generic fallback) | `opensc-pkcs11.so` | `opensc-pkcs11.dll` | glob `*pkcs11*.so` |

Ranking (`rank_driver_candidates`): vendor names in `_VENDOR_DRIVER_HINTS` come first, then unknown libraries, then OpenSC. On macOS, a driver whose architecture matches the process comes first. `pkcs11-spy` is never chosen automatically. Order at run time: the pinned module, then the last driver that worked (dsc_last_driver.txt), then the ranked list. Strict mode tries only the pinned module. On macOS, a driver inside a `.app` is copied out before loading (`_stage_bundled_driver`).

## Authentication & Authorization Assumptions

- There is no user account and no token storage. Authorization is (1) Chrome's `allowed_origins` check, which accepts only the extension ID, (2) the origin allowlist, and (3) the token PIN, verified by the token's `C_Login`.
- The PIN travels page → content script → background → host → worker stdin. None of them stores or logs it. `bridge-inject.js` keeps it only inside the pending postMessage.

## Standard Error Envelope

```json
{ "success": false, "error": "Human-readable text with the fix", "code": "TOKEN_UNSUPPORTED_BY_MODULE" }
```

On the page this becomes a rejected `Error`: `err.message` holds `error` and `err.code` holds `code`.

## Persistence & Client Cache Contracts

| Store Name | Storage Key | Format | Owner / Invalidator |
|---|---|---|---|
| Bridge config | dsc-bridge.json under `config_path()` (macOS `~/Library/Application Support/Giddh/`, Windows `%APPDATA%\Giddh\`, Linux `~/.config/giddh/`) | JSON `{pkcs11_driver?, modules[], preferred_module, strict_module}`; unknown keys are kept | Read by `resolve_driver_paths`. The host rebuilds its signer when the file's mtime changes (`_SignerCache`). There is no writer in any shipped UI (`save_module_config`) |
| Last good driver | dsc_last_driver.txt in `_state_dir()` (same folder) | Plain path | `_save_last_driver` after every success. Never cleared |
| Staged drivers (macOS) | `~/Library/Application Support/Giddh/drivers/` | Byte copies of in-bundle dylibs | `_stage_bundled_driver`; re-copied when the source changes |
| Status-app debug log | `/tmp/giddh-canvas-debug.log` | Text, overwritten on every render | `dsc-bridge/native-host/giddh_dsc_status.py:StatusApp` (`_render_canvas`) |
| Extension | none | The extension uses no chrome.storage or localStorage | none |

## Analytics & Telemetry Event Contracts

None. The extension makes no network calls (no `fetch` and no XHR in `dsc-bridge/extension/`), and the host only writes logs to stderr. The table is empty on purpose; adding telemetry is a product decision ([VISION](VISION.md)).

## Required / Unavailable Capabilities

The code or docs assume these capabilities, but nothing implements them. Specify the contract here before building them; never fake them.

### Proposed host action: attach or pin a PKCS#11 module (desktop-only)
- **Purpose:** Error text (`_UNSUPPORTED_HINT`, `NO_DRIVER`, the strict-pin hint) and the test page tell users to "use Attach module… in the Giddh DSC Bridge app". That UI was removed in `fe15172`. The back end exists (`attach_module`, `detach_module`, `set_preferred_module`, `set_strict_module`), but nothing calls it.
- **Contract:** This must stay **out of** the web API. It belongs only in `giddh_dsc_status.py`, writing dsc-bridge.json:
  ```json
  { "modules": ["<abs path ending .dylib|.so|.dll>"], "preferred_module": "<path or empty>", "strict_module": false }
  ```
- **Blocking status:** Needs an owner decision: restore the UI, or change the error copy ([OPEN_QUESTIONS](OPEN_QUESTIONS.md)).

### Proposed check: validate the driver tag in `signHash`
- **Purpose:** Close finding 8. In `IsolatedSigner.sign_hash`, ignore a decoded driver path that is not in `self.driver_paths`, matching `list_certificates`.
- **Blocking status:** A code fix. Not done in this docs-only pass.

### Proposed page API: `GiddhBridge.ping()`
- **Purpose:** The host answers `ping` with a version, but the wrapper has no method, so pages can only compare the injected `version`. A `ping()` that resolves `{pong, version}` would let Giddh detect a missing or stale host before asking for a PIN. Fix the hardcoded `"1.1.0"` first.
- **Blocking status:** Proposal only.
