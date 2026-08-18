"""
Deferred (interrupted) PAdES signing for hardware DSC tokens.
=============================================================
Implements the 3-hop "sign the hash" pattern that lets a hardware crypto
token (accessed client-side via a bridge such as the Signer.Digital browser
extension) apply a legally-valid PAdES digital signature without the private
key ever leaving the token:

  1. prepare_deferred_signature()  -- SERVER
     Given the current PDF bytes and the signer's chosen X.509 certificate,
     reserve a signature placeholder, compute the CMS SignedAttributes and
     return the exact bytes/hash the token must sign, plus an opaque, JSON-
     serialisable state blob to carry between the two HTTP requests.

  2. (client)  -- the browser extension has the token sign `data_to_sign_hash`
     and returns the raw signature bytes + certificate chain.

  3. finish_deferred_signature()  -- SERVER
     Rebuild the CMS from the prepared SignedAttributes + the token signature
     and embed it into the reserved placeholder as an incremental PDF update.

Design notes / invariants
-------------------------
* The SignedAttributes DER produced in step 1 MUST be persisted verbatim and
  reloaded in step 3 (it pins signingTime + the message digest). We never
  regenerate it.
* This targets PAdES-B-B (no TSA) by default. The pipeline is TSA-ready:
  pass `tsa_url` to finish_deferred_signature() to upgrade to B-T once a
  CCA-licensed TSA is provisioned (config-gated by the caller).
* RSA (PKCS#1 v1.5) is the primary mechanism -- Indian DSC tokens are RSA.
  RSA-PSS is supported via `prefer_pss`.
* No private key material ever reaches the server. Only a cert + a signature.

This module is intentionally free of Flask/DB imports so it can be unit-tested
in isolation (see tests/test_pdf_deferred_sign.py).
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MD = "sha256"


@dataclass(frozen=True)
class PreparedSignature:
    """JSON-serialisable state carried between prepare and finish."""

    prepared_pdf: bytes            # PDF with the reserved signature placeholder
    signed_attrs_der: bytes        # CMS SignedAttributes (DER) -- pinned verbatim
    document_digest: bytes         # ByteRange digest of the prepared PDF
    reserved_region_start: int
    reserved_region_end: int
    md_algorithm: str
    data_to_sign: bytes            # the DER SignedAttributes the token signs over

    def to_json(self) -> dict:
        import base64

        return {
            "prepared_pdf": base64.b64encode(self.prepared_pdf).decode(),
            "signed_attrs_der": base64.b64encode(self.signed_attrs_der).decode(),
            "document_digest": self.document_digest.hex(),
            "reserved_region_start": self.reserved_region_start,
            "reserved_region_end": self.reserved_region_end,
            "md_algorithm": self.md_algorithm,
            "data_to_sign": base64.b64encode(self.data_to_sign).decode(),
        }

    @classmethod
    def from_json(cls, d: dict) -> "PreparedSignature":
        import base64

        return cls(
            prepared_pdf=base64.b64decode(d["prepared_pdf"]),
            signed_attrs_der=base64.b64decode(d["signed_attrs_der"]),
            document_digest=bytes.fromhex(d["document_digest"]),
            reserved_region_start=int(d["reserved_region_start"]),
            reserved_region_end=int(d["reserved_region_end"]),
            md_algorithm=d["md_algorithm"],
            data_to_sign=base64.b64decode(d["data_to_sign"]),
        )


def _build_cert_registry(signer_cert, chain_ders: Optional[List[bytes]]):
    from asn1crypto import x509 as asn1_x509
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    store = SimpleCertificateStore()
    certs = [signer_cert]
    for der in chain_ders or []:
        try:
            certs.append(asn1_x509.Certificate.load(der))
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Skipping unparyseable chain cert: %s", e)
    store.register_multiple(certs)
    return store


def _placeholder_len_for_cert(signer_cert) -> int:
    """RSA modulus byte length -> raw signature length used for size estimation."""
    try:
        pub = signer_cert.public_key
        algo = pub.algorithm
        if algo == "rsa":
            bit_size = pub["public_key"].parsed["modulus"].native.bit_length()
            return (bit_size + 7) // 8
        if algo == "ec":
            # DER-encoded ECDSA sig upper bound; 132 covers P-521.
            return 132
    except Exception:  # pragma: no cover - defensive
        pass
    return 256  # safe default for 2048-bit RSA


def _digest_of(data: bytes, md_algorithm: str) -> bytes:
    import hashlib

    h = hashlib.new(md_algorithm)
    h.update(data)
    return h.digest()


def prepare_deferred_signature(
    pdf_bytes: bytes,
    signer_cert_der: bytes,
    *,
    field_name: str,
    field_box=None,
    on_page: int = 0,
    appearance_pdf_path: Optional[str] = None,
    chain_ders: Optional[List[bytes]] = None,
    reason: Optional[str] = None,
    location: Optional[str] = None,
    contact_info: Optional[str] = None,
    md_algorithm: str = DEFAULT_MD,
    certify: bool = False,
    prefer_pss: bool = False,
) -> PreparedSignature:
    """Phase 1: reserve a signature placeholder and compute what the token signs.

    :param pdf_bytes: current canonical PDF (bytes appended incrementally).
    :param signer_cert_der: DER of the signer's X.509 certificate (from token).
    :param field_name: unique signature field name for this signer.
    :param field_box: (x1, y1, x2, y2) in PDF points for a visible appearance,
        or None/invisible.
    :param on_page: 0-indexed page the visible field is placed on.
    :param appearance_pdf_path: optional path to a one-page PDF used verbatim as
        the visible signature appearance (see :mod:`dsc_signing.core.appearance`).
        Only used when ``field_box`` is a visible (non-zero) box.
    :param chain_ders: optional intermediate/root certs (DER) for the CMS.
    :param certify: if True, apply a DocMDP certification signature that permits
        further signing (use for the FIRST signature on the document).
    :returns: PreparedSignature state blob.
    """
    from asn1crypto import x509 as asn1_x509
    from pyhanko.sign.fields import (
        SigFieldSpec,
        SigSeedSubFilter,
        append_signature_field,
    )
    from pyhanko.sign.signers.pdf_cms import ExternalSigner
    from pyhanko.sign.signers.pdf_signer import (
        PdfSignatureMetadata,
        PdfSigner,
    )
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    signer_cert = asn1_x509.Certificate.load(signer_cert_der)
    registry = _build_cert_registry(signer_cert, chain_ders)

    ext_signer = ExternalSigner(
        signing_cert=signer_cert,
        cert_registry=registry,
        signature_value=bytes(_placeholder_len_for_cert(signer_cert)),
        prefer_pss=prefer_pss,
    )

    inp = io.BytesIO(pdf_bytes)
    w = IncrementalPdfFileWriter(inp)

    box = tuple(field_box) if field_box else None
    visible = bool(box) and (box[2] - box[0] > 1) and (box[3] - box[1] > 1)
    append_signature_field(
        w,
        sig_field_spec=SigFieldSpec(
            sig_field_name=field_name, box=box, on_page=int(on_page or 0)
        ),
    )

    # Build a visible static appearance from the supplied one-page PDF.
    stamp_style = None
    if visible and appearance_pdf_path:
        try:
            from pyhanko.stamp import StaticStampStyle

            stamp_style = StaticStampStyle.from_pdf_file(
                appearance_pdf_path, border_width=0
            )
        except Exception as e:  # pragma: no cover - defensive; fall back invisible
            logger.warning("DSC visible appearance disabled (%s)", e)
            stamp_style = None

    meta_kwargs = dict(
        field_name=field_name,
        subfilter=SigSeedSubFilter.PADES,
        md_algorithm=md_algorithm,
        reason=reason,
        location=location,
        contact_info=contact_info,
        embed_validation_info=False,  # B-B; LTV added later
    )
    if certify:
        from pyhanko.sign.fields import MDPPerm

        meta_kwargs["certify"] = True
        meta_kwargs["docmdp_permissions"] = MDPPerm.FILL_FORMS

    meta = PdfSignatureMetadata(**meta_kwargs)
    pdf_signer = PdfSigner(meta, signer=ext_signer, stamp_style=stamp_style)

    async def _run():
        prep_digest, _tbs_doc, output = await pdf_signer.async_digest_doc_for_signing(
            w, existing_fields_only=True
        )
        signed_attrs = await ext_signer.signed_attrs(
            prep_digest.document_digest, md_algorithm, use_pades=True
        )
        return prep_digest, output, signed_attrs

    prep_digest, output, signed_attrs = asyncio.run(_run())
    signed_attrs_der = signed_attrs.dump()

    return PreparedSignature(
        prepared_pdf=output.getvalue(),
        signed_attrs_der=signed_attrs_der,
        document_digest=prep_digest.document_digest,
        reserved_region_start=prep_digest.reserved_region_start,
        reserved_region_end=prep_digest.reserved_region_end,
        md_algorithm=md_algorithm,
        data_to_sign=signed_attrs_der,
    )


def hash_to_sign(prepared: PreparedSignature) -> bytes:
    """The digest the token must sign (over the SignedAttributes DER).

    Signer.Digital's SignHash API expects this hash; the token wraps it in a
    DigestInfo and applies RSA PKCS#1 v1.5.
    """
    return _digest_of(prepared.data_to_sign, prepared.md_algorithm)


def finish_deferred_signature(
    prepared: PreparedSignature,
    signature_value: bytes,
    signer_cert_der: bytes,
    *,
    chain_ders: Optional[List[bytes]] = None,
    prefer_pss: bool = False,
    tsa_url: Optional[str] = None,
) -> bytes:
    """Phase 3: build the CMS from the token signature and embed it.

    :param prepared: state returned by prepare_deferred_signature().
    :param signature_value: raw signature bytes returned by the token.
    :param signer_cert_der: DER of the signer certificate (must match prepare).
    :param tsa_url: optional RFC 3161 TSA endpoint -> upgrades B-B to B-T.
    :returns: the final incrementally-signed PDF bytes.
    """
    from asn1crypto import cms as asn1_cms, x509 as asn1_x509
    from pyhanko.sign.signers.pdf_byterange import PreparedByteRangeDigest
    from pyhanko.sign.signers.pdf_cms import ExternalSigner

    signer_cert = asn1_x509.Certificate.load(signer_cert_der)
    registry = _build_cert_registry(signer_cert, chain_ders)

    ext_signer = ExternalSigner(
        signing_cert=signer_cert,
        cert_registry=registry,
        signature_value=signature_value,
        prefer_pss=prefer_pss,
    )

    signed_attrs = asn1_cms.CMSAttributes.load(prepared.signed_attrs_der)

    timestamper = None
    if tsa_url:
        from pyhanko.sign.timestamps import HTTPTimeStamper

        timestamper = HTTPTimeStamper(tsa_url)

    sig_cms = asyncio.run(
        ext_signer.async_sign_prescribed_attributes(
            prepared.md_algorithm,
            signed_attrs,
            timestamper=timestamper,
        )
    )

    prep_digest = PreparedByteRangeDigest(
        document_digest=prepared.document_digest,
        reserved_region_start=prepared.reserved_region_start,
        reserved_region_end=prepared.reserved_region_end,
    )

    output = io.BytesIO(prepared.prepared_pdf)
    prep_digest.fill_with_cms(output, sig_cms)
    return output.getvalue()
