# dsc_signing

Portable, framework-agnostic **DSC (Digital Signature Certificate) token signing**
for PDFs — the deferred **PAdES** ("sign the hash") flow used with hardware
crypto tokens via a client-side bridge (e.g. the **Signer.Digital** browser
extension).

The private key never leaves the token. The browser only transports an opaque
hash and the resulting signature. The server prepares the signature, then
embeds the token's CMS signature as an **incremental** PDF update so earlier
signatures are never invalidated.

> This package has **zero** dependencies on any web framework, database, or host
> application. A host integrates by implementing a few small ports. Giddh is
> one such host (`giddh/blueprints/dsc.py`); another product can reuse the
> exact same engine.

## Install (runtime deps)

```
pyhanko  pyhanko-certvalidator  asn1crypto  cryptography
```

## Architecture

```
dsc_signing/
  core/
    deferred_pades.py   # pure crypto: prepare / hash_to_sign / finish
    cert_verifier.py    # X.509 parsing, name match, expiry, issuer allow-list
  config.py             # DscConfig (populate from your own settings)
  errors.py             # typed DscError subclasses (code + http_status)
  models.py             # SignerContext / PrepareResult / FinishResult / SignatureResult
  ports.py              # WorkingPdfStore / StateStore / ProvenanceSink / AuditSink (+ in-memory impls)
  service.py            # DscSigningService.prepare() / .finish()
```

Three-hop flow:

1. **prepare(ctx, certificate)** → reserves a placeholder, verifies the cert,
   returns `{nonce, hash, cert, name_match_score}`.
2. **(client)** the token signs `hash`, returns the raw signature.
3. **finish(ctx, nonce, signature)** → builds the CMS, embeds it incrementally,
   records provenance, returns `{pades_level, verified}`.

## Ports you implement

| Port | Responsibility |
|------|----------------|
| `WorkingPdfStore` | load/build + save the per-document working PDF |
| `StateStore` | persist the opaque prepared-state by `(doc_id, nonce)` |
| `ProvenanceSink` | record the applied signature (DB row, field marking…) |
| `AuditSink` *(optional)* | audit-trail hook |

In-memory implementations (`InMemoryStateStore`, `InMemoryWorkingPdfStore`,
`NullAuditSink`) are provided for tests and ephemeral hosts.

## Minimal usage

```python
from dsc_signing import DscConfig, DscSigningService, SignerContext
from dsc_signing.ports import InMemoryStateStore, InMemoryWorkingPdfStore, NullAuditSink

service = DscSigningService(
    DscConfig(location="MyApp", name_match_threshold=0.6),
    working_store=InMemoryWorkingPdfStore(build_base_pdf),  # build_base_pdf(doc_id)->bytes
    state_store=InMemoryStateStore(),
    provenance=my_provenance_sink,
    audit=NullAuditSink(),
)

ctx = SignerContext(doc_id="doc-1", signer_id="s-1", signer_name="Rajesh Kumar")
prep = service.prepare(ctx, certificate_b64)     # -> PrepareResult
# ... token signs prep.hash_hex, returns signature_b64 ...
result = service.finish(ctx, prep.nonce, signature_b64)   # -> FinishResult
```

## Error handling

All failures raise a `DscError` subclass carrying a machine `code` and a
suggested `http_status` (e.g. `VerificationError` → 400,
`StaleDocumentError` → 409, `SigningError` → 500). Your transport layer maps
these to responses via `err.to_dict()` / `err.http_status`.

## PAdES levels

Ships **PAdES-B-B** by default. Set `DscConfig.tsa_url` to an RFC 3161 TSA to
upgrade to **B-T** — no code change required.

## Tests

- `tests/test_pdf_deferred_sign.py` — crypto core
- `tests/test_dsc_verifier.py` — certificate verifier
- `tests/test_dsc_service_portable.py` — the service via in-memory ports (no host)
- `tests/test_dsc_endpoints.py` — Giddh Flask adapter layer

## Extracten as its own package later

This lives in-repo for now. To publish standalone: move the `dsc_signing/`
directory into its own repo, add a `pyproject.toml` with the runtime deps
above, and depend on it from the host. No host imports need changing beyond the
dependency source.
