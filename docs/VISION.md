---
covers: ["*"]
verified_at: c591bc2
verified_on: 2026-11-17
---
# Product Vision & Strategic Direction

Giddh DSC Bridge lets a Giddh user sign documents with their own hardware DSC token (an Indian digital signature certificate on a USB token, used through PKCS#11) straight from the Giddh web app. The private key never leaves the token. A Chrome extension carries the request from the Giddh page to a small local program, and that program asks the token to sign.

## Authoritative Sources & Status

| Source | What it gives |
|---|---|
| The owner's answers, 2026-11-17 | Users and the release flow: **approved** |
| `README.md`, `PROJECT.md` | Purpose, components, security model. `PROJECT.md` is superseded by this doc set; its still-true content moved here and into [ARCHITECTURE.md](ARCHITECTURE.md) |
| `dsc-bridge/INTEGRATION.md` | The contract with the Giddh web app and backend. It stays authoritative; [CONTRACTS.md](CONTRACTS.md) summarises it |
| Git history (31 commits, 2026-08-18 to 2026-09-03) | Fast hardening of packaging, signing and token handling ([DECISIONS.md](DECISIONS.md)) |

- **Approved direction:** what the owner confirmed (below).
- **Current implementation:** [ARCHITECTURE.md](ARCHITECTURE.md), [FLOWS.md](FLOWS.md) and [CONTRACTS.md](CONTRACTS.md).
- **Proposals:** there is no roadmap in the repo.
- **Unknowns:** [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

### Approved Direction

Confirmed by the owner, 2026-11-17:
- **Users are staff at Giddh's customer businesses.** They sign invoices and other documents with their own DSC token from the Giddh web app.
- **Release flow:** a PR to `main` makes CI build test installers; a `v*` tag publishes the release installers; the extension is uploaded to the Chrome Web Store by hand.

Stated in the repo (`README.md`, `PROJECT.md`):
- **Key custody:** signing happens on the user's machine and the private key never leaves the token.
- **No setup for end users:** one installer per OS (`.dmg`/`.pkg`, `.exe`, `.deb`) and nothing else to install. Python is not needed.
- **Fixed extension ID:** one Chrome Web Store extension ID, so a single installer build works on any computer.

## Target Audience & Ideal Customer Profile (ICP)

- **Primary persona:** a staff member at a Giddh customer business who holds a DSC token and signs documents in Giddh, such as invoices (confirmed by the owner, 2026-11-17). They are a business user, not technical, and they install the bridge once.
- **Secondary personas:**
  - Giddh support staff, who help users install and diagnose (the status app's "Check token", the test page). This is inferred.
  - Giddh engineers, who integrate the web app and backend through `dsc-bridge/INTEGRATION.md`.

## Problems Solved & Value Proposition

| Customer Problem | How We Solve It | Unique Value / Differentiator |
|---|---|---|
| Browsers can't reach a USB signing token | Extension plus a native messaging host plus PKCS#11 | Signing happens inside the Giddh page; the user doesn't export and re-upload files |
| Indian DSC tokens come from many vendors with different drivers | `pkcs11_signer.py` finds drivers for common vendors and runs each in an isolated worker | A crashing or stuck driver can't take the bridge down |
| Users can't install developer tools | Signed and notarised installers per OS | Nothing else to install |
| The private key must stay protected | Only a hash is signed on the token; the PIN goes straight to `C_Login` and is never stored | Key custody stays with the user |

## Core Principles & Design Philosophy

These come from `PROJECT.md` §4 ("Security model — do not casually change these") and the code.

1. **Least privilege for web pages.** Only allowlisted Giddh origins can reach the bridge, checked by the manifest match patterns *and* `background.js`. The web API is read-only apart from signing: a page must never be able to add a PKCS#11 library.
2. **The key and PIN stay local.** Never persist or log a PIN, and never add a code path that exports key material.
3. **Robust against drivers.** Open a fresh native connection for each call, and load each driver in an isolated subprocess. Vendor drivers misbehave, so the design assumes they will.
4. **One build fits everyone.** The fixed extension ID and `allowed_origins` mean no per-machine configuration.
5. **Plain language for business users.** The status app and its messages are written for non-technical staff ([DESIGN.md](DESIGN.md)).

## Strategic Priorities & Success Measures

> [!TODO]
> No priorities or measures are written down. Recent history focuses on macOS signing and notarisation, Windows signing, and token compatibility. An owner needs to set measures such as install success rate, signing success rate and supported token vendors (see [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)).

## Boundaries & Non-Goals

- **Not a key store and not a remote signing service.** It never holds or uploads keys and never signs on a server.
- **Not a general-purpose signer for any website.** Only Giddh origins are allowed. Widening the allowlist needs an owner decision.
- **No web-facing driver management.** Attaching or pinning a PKCS#11 module happens only in the local status app.
- **PDF assembly is not done here.** The backend prepares and finishes the PAdES signature (`INTEGRATION.md`); the bridge signs a hash.
- **Chrome only** (an MV3 extension) unless the owner widens it.

**Anti-patterns:**
- Setting `ALLOW_ALL_ORIGINS` to true in a shipped build.
- Changing the extension ID or `packaging/extension-id.txt` independently of each other.
- Logging PINs or hash payloads.
- Adding a long-lived native port: it was deliberately replaced by one port per call.

## How Future Agents Must Evaluate Requests

1. **Align with approved direction:** compare the request against the principles and boundaries above. Security-model changes need explicit owner approval.
2. **Improve unclear requests:** name the OS, token vendor and Giddh flow involved, and whether the change touches the extension, the host, the installer or the contract.
3. **Flag conflicts:** widening origins, web-side module management or key export conflict with the vision. Explain why and recommend declining.
4. **Explicit direction changes:** update this file in the same change as the code.
5. **No fabricated integrations:** a new web-app or backend need goes into `dsc-bridge/INTEGRATION.md` and [CONTRACTS.md](CONTRACTS.md) as a contract first. Never fake a token or a signature path.
