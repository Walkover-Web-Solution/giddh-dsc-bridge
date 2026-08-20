# Giddh DSC Bridge — Server Integration Guide

This document explains how to wire the **DSC Bridge** (browser extension +
native host) into a web application to produce cryptographically-signed PDFs
(**deferred PAdES**). It complements `README.md` (which covers the bridge itself).

> This repo ships only the **client-side bridge** (extension + native host that
> reads the token and signs a hash). The **server-side PDF/PAdES embedding
> engine** is not included here — implement it in your own backend using the
> contract below, or reuse whatever PAdES library you prefer.

> **Golden rule:** the private key and PIN **never** reach the server. The server
> only ever computes a *hash to sign* and later receives the *signature*.

---

## 1. End-to-end flow

```
Browser (your page)          Bridge (extension+host)        Your server
─────────────────            ───────────────────────        ───────────
1. read certificate  ───────► getCertificate ──► token
   (base64 DER)      ◄─────── cert list

2. POST /dsc/prepare ─────────────────────────────────────► verify cert,
   { certificate }                                           reserve placeholder,
   { nonce, hash, md_algorithm, cert } ◄─────────────────── compute hash

3. signHash(hash,PIN) ─────► token C_Sign ──► signature
   (base64)          ◄───────

4. POST /dsc/finish ──────────────────────────────────────► build CMS, embed
   { nonce, signature }                                      incrementally,
   { success, pades_level, verified } ◄──────────────────── record provenance
```

Two server endpoints, two bridge calls. The `nonce` ties `prepare` to `finish`.

---

## 2. HTTP contract (server endpoints)

Your own backend must implement these two endpoints. Paths below are Giddh's —
adapt the prefix to your app.

### `POST /<access_token>/dsc/prepare`

Request:
```json
{
  "certificate": "<base64 DER of the signer's X.509 certificate>",
  "chain": ["<base64 DER intermediate>", "<base64 DER root>"],  // recommended
  "signature_image": "data:image/png;base64,...  (optional, for a visible appearance)"
}
```

> **Send `chain` at `prepare`, not just `finish`.** The server reserves the
> signature placeholder during `prepare`; it must be sized for the *full* CMS
> (leaf + intermediates + root). If the chain only arrives at `finish`, the
> reserved byte range overflows (`Final ByteRange payload larger than expected`).
> `getCertificate` (below) already returns the chain for you — forward it here.
> The same chain is persisted and reused at `finish`, so the two always match.

Response (200):
```json
{
  "success": true,
  "nonce": "<opaque string — pass back to finish>",
  "hash_b64": "<base64 SHA-256 digest the token must sign>",
  "md_algorithm": "sha256",
  "cert": { "subject_cn": "…", "issuer_cn": "…", "serial": "…", "not_after": "…" },
  "name_match_score": 0.92
}
```

Failure: HTTP 4xx/5xx with `{ "success": false, "error": "...", "code": "..." }`.
Typical codes: `VERIFICATION_FAILED` (400), `STALE` (409), `INTERNAL` (500).

### `POST /<access_token>/dsc/finish`

Request:
```json
{
  "nonce": "<from prepare>",
  "signature": "<base64 raw PKCS#1 v1.5 signature from the token>",
  "chain": ["<base64 intermediate cert>", "..."]   // optional
}
```

Response (200):
```json
{ "success": true, "pades_level": "PAdES-B-B", "verified": true }
```

On success the signed PDF has been embedded as an **incremental update** onto the
per-document working PDF, so previously-applied signatures stay valid.

---

## 3. Browser client API (`window.GiddhBridge`)

Injected on every page once the extension is installed. All methods return
Promises.

```js
// Detect
if (!window.GiddhBridge) { /* prompt to install the bridge */ }

// 1. Read certificates (usually no PIN)
const { success, certificates } = await window.GiddhBridge.getCertificate();
// Only end-entity SIGNING certificates are returned (CA certs are filtered out
// via basicConstraints, so the user never has to pick a CA by mistake).
// Each carries its issuing CA chain so the server can embed the full trust path:
// certificates: [{ certId, certB64, subjectCn, issuerCn, serial, notBefore,
//                  notAfter, isCa: false, chain: ["<b64 CA DER>", ...] }]

// 2. Sign a base64 hash — PIN is forwarded straight to hardware
const { success, signature } =
  await window.GiddhBridge.signHash(hashB64, "SHA256", certId, pin);

// 3. Diagnose install/driver issues (no token/PIN needed)
console.log(await window.GiddhBridge.diagnose());

// 4. List PKCS#11 modules — READ-ONLY (no token/PIN needed)
const { success, modules, readers, preferred, strict } =
  await window.GiddhBridge.listModules();
// modules: [{ driver_path, manufacturer_id, library_description,
//             library_version, cryptoki_version, driver_exists, driver_loads,
//             driver_arches, arch_mismatch, slots, slot_errors, tokens,
//             attached, preferred }]
// readers: [{ reader, card_present, exclusive_ok, locked, status }]  <- PC/SC
// Useful for a "which token did we find?" panel and for support tickets.
// Attaching or pinning a module is deliberately NOT exposed here — it happens
// only in the desktop companion app, so a page can never make the native host
// dlopen an arbitrary library.
```

**Read `readers` (PC/SC), not module error codes, to explain a failure.** A
module that cannot drive the inserted card reports `CKR_TOKEN_NOT_PRESENT` or
`CKR_TOKEN_NOT_RECOGNIZED` — indistinguishable from an empty reader — so module
errors alone will lead you to the wrong conclusion. The two cases to separate:

| PC/SC says | Meaning | Code surfaced |
| --- | --- | --- |
| `locked: true` | another app really holds the card | `TOKEN_BUSY_OR_ABSENT` |
| `card_present: true, locked: false`, yet no module found a token | card is fine; the right PKCS#11 module is missing | `TOKEN_UNSUPPORTED_BY_MODULE` |
| `card_present: false` | nothing inserted | `NO_TOKEN` |

Surface those messages as-is: each already names the reader and states the fix.

Message protocol and error `code`s are documented in `README.md`.

---

## 4. Orchestration reference (`signFlow`)

`giddh/static/js/dsc-signing.js` implements the full four-step flow behind a
single call and is safe to copy into another app (it only assumes the two HTTP
endpoints above):

```js
const result = await window.GiddhDSC.signFlow({
  accessToken,                 // used to build /dsc/prepare and /dsc/finish URLs
  signatureImage,              // optional data URL for a visible appearance
  onSelectCertificate: certs => showPicker(certs),   // only if >1 cert
  onCertSelected: info => showProgress('Preparing…'),
  onPinRequired:  info => promptPin(info),            // return the PIN string
});
// result: { success, cert, padesLevel, verified }
```

It also auto-detects the legacy **Signer.Digital** extension as a fallback, so a
page works with either bridge.

---

## 5. Server engine

This repo does **not** include a server-side PDF/PAdES engine. Your backend must
be able to (a) verify an X.509 cert, (b) return the SHA-256 hash of the PDF's
signed attributes, and (c) embed the resulting CMS as an incremental PAdES
update — matching the `/dsc/prepare` and `/dsc/finish` contract above.

---

## 6. Trust (why Adobe may say "validity unknown")

A cryptographically-valid signature still shows as *untrusted* in Adobe until the
signer's **CA is in the verifier's trust store**. Indian DSC CAs chain to CCA
India; add the CCA root (or enable AATL) in Adobe to get a green check. This is a
verifier-side trust decision, **not** a signing defect — `verified: true` from
`finish` confirms the embedded signature is internally valid.

---

## 7. Pre-handoff checklist

- [ ] `python3 native-host/selftest.py` passes all 5 stages on each target OS.
- [ ] `test-page/index.html` shows "bridge detected", lists certs, signs a hash.
- [ ] Extension loads cleanly (`chrome://extensions`, no errors); check `VERSION` file.
- [ ] Native-host manifest `allowed_origins` matches the loaded extension ID.
- [ ] Server `DSC_SIGNING_ENABLED=true`; `/dsc/prepare` + `/dsc/finish` reachable.
- [ ] A completed multi-signer pack opens in **Adobe Acrobat** with a signature
      panel listing every signer.
