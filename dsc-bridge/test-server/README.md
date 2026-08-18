# Standalone Full-Flow Test Server

Proves the **entire** DSC chain on any machine **without Giddh**:

```
browser page → extension (window.GiddhBridge) → native host → DSC token
      │                                                            │
      └─ POST /dsc/prepare (hash) ─ dsc_signing engine ─ POST /dsc/finish (embed CMS)
```

A successful run yields a **real PAdES-signed PDF** you can open in **Adobe
Acrobat** to confirm the signature panel.

## Prerequisites

1. Install the DSC bridge on this machine (extension + native host):
   - macOS: `../install/install_macos.sh`
   - Windows: `../install/install_windows.ps1`
   - Linux: `../install/install_linux.sh`
2. Load the Chrome extension from `../extension/` (or install the published one).
3. Plug in the DSC token and install its vendor PKCS#11 driver.

## Run

```bash
pip install -r requirements.txt
python app.py                 # http://127.0.0.1:5055
```

Open http://127.0.0.1:5055 and follow steps 1→3:
- **Bridge status** should turn green ("detected").
- **Read certificates** lists certs on the token.
- **Sign PDF** runs prepare → token sign → finish, then shows a download link.

Open the downloaded `dsc-signed-test.pdf` in Adobe Acrobat → the Signatures panel
must show a valid signature. (Browser PDF viewers do NOT render signature panels.)

## What this validates vs. not

- ✅ Extension ↔ native host wiring (contract 2 + host manifest name)
- ✅ Page global `window.GiddhBridge` (contract 1)
- ✅ Token PKCS#11 read + hardware sign
- ✅ Server `prepare`/`finish` crypto via the portable `dsc_signing` engine
- ✅ Visible signature appearance + PAdES seal (Adobe-verifiable)
- ⚠️ Uses in-memory ports and `verify_signer_name=False` — it does NOT test your
  DB/storage or name-matching policy. That is host-specific (see `INTEGRATION.md`).

## Testing a third-party signer tool (swap the extension)

This page is adapter-aware, so you can prove the swap works without any code:

1. Disable our extension at `chrome://extensions`.
2. Install a third-party tool (e.g. **Signer.Digital** from the Chrome Web Store).
3. Reload the page → the status line switches to **"Bridge detected: signer-digital"**.
4. Read certificates → works. Sign PDF → the tool runs its own sign path.

Note on Signer.Digital: it reads certificates for free but its **signing** step
requires a **licensed origin**. On `localhost` it will reject with a clear
notification ("License is not Active for site …") — that is expected and proves
error handling, not a bug. Our own bridge needs no license.

To wire a different tool, add one adapter object to the `ADAPTERS` array in
`index.html` (map `isAvailable` / `getCertificate` / `signHash`).

## Bundle note

This server imports the engine from the repo root (`dsc_signing/`). A partner
handoff bundle MUST include both `dsc-bridge/` and `dsc_signing/`. See
`../PACKAGING.md` and `../HANDOFF.md`.

## Troubleshooting

- **Bridge not detected** → extension not loaded, or content script blocked. Reload.
- **prepare fails** → check the server console; usually a missing engine dep
  (`pip install -r requirements.txt`) or an invalid certificate.
- **token sign fails** → wrong PIN, or the origin is unlicensed if you are using
  the legacy Signer.Digital extension (our own bridge needs no per-origin license).
- **Adobe shows no signature** → you opened an in-progress/regenerated copy;
  download again via the link after step 3 completes.
