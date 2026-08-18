"""DscSigningService — framework-agnostic orchestration of the DSC flow.

Holds NO web/DB/host imports. It composes the pure crypto core with the
host-supplied ports (storage, provenance, audit) and a plain config object.

Transport layers (a Flask blueprint, a worker, a CLI) call ``prepare`` and
``finish`` and translate the returned dataclasses / raised ``DscError``s into
their own responses.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Callable, List, Optional

logger = logging.getLogger("dsc_signing.service")

from .config import DscConfig
from .core.cert_verifier import (
    cert_der_from_any,
    extract_cert_info,
    is_expired,
    issuer_allowed,
    name_match_score,
)
from .core.deferred_pades import (
    PreparedSignature,
    finish_deferred_signature,
    hash_to_sign,
    prepare_deferred_signature,
)
from .errors import (
    CertificateError,
    SessionError,
    SigningError,
    StaleDocumentError,
    VerificationError,
)
from .models import FinishResult, PrepareResult, SignatureResult, SignerContext
from .ports import AuditSink, ProvenanceSink, StateStore, WorkingPdfStore


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DscSigningService:
    def __init__(
        self,
        config: DscConfig,
        working_store: WorkingPdfStore,
        state_store: StateStore,
        provenance: ProvenanceSink,
        audit: Optional[AuditSink] = None,
        clock: Callable[[], datetime] = datetime.utcnow,
    ):
        self.config = config
        self.working_store = working_store
        self.state_store = state_store
        self.provenance = provenance
        self.audit = audit
        self._now = clock

    # ── Phase 1: prepare ─────────────────────────────────────────────────────
    def prepare(
        self,
        ctx: SignerContext,
        certificate,
        *,
        field_box=None,
        on_page: int = 0,
        handwritten_png: Optional[bytes] = None,
        chain: Optional[List[str]] = None,
    ) -> PrepareResult:
        """Reserve a deferred signature and return the hash the token must sign.

        When ``field_box`` (a 4-tuple of PDF points, bottom-left origin) is
        supplied, a visible signature appearance is rendered at that location:
        the signer's ``handwritten_png`` (optional) on the left and the
        certificate details on the right — matching the product's DocuSign+DSC
        hybrid look. Without a box the signature is cryptographic-only
        (invisible), preserving the previous behaviour.
        """
        if not certificate:
            raise CertificateError("Certificate is required")

        try:
            cert_der = cert_der_from_any(certificate)
            info = extract_cert_info(cert_der)
        except Exception as e:
            raise CertificateError(f"Invalid certificate: {e}")

        # Decode the CA chain NOW so the reserved signature placeholder is sized
        # for the full CMS (leaf + intermediates + root). If the chain is only
        # supplied at finish, the reserved ByteRange is too small and embedding
        # overflows. The same chain is stored and reused at finish.
        try:
            chain_ders = [base64.b64decode(c) for c in (chain or [])]
        except Exception as e:
            raise CertificateError(f"Invalid certificate chain: {e}")

        # Certificate Verifier -------------------------------------------------
        if is_expired(info):
            raise VerificationError("Certificate is expired or not yet valid.")

        if not issuer_allowed(info, self.config.allowed_issuers):
            raise VerificationError("Certificate issuer is not an accepted CA.")

        score = name_match_score(ctx.signer_name or "", info.subject_cn or "")
        if self.config.verify_signer_name and score < self.config.name_match_threshold:
            raise VerificationError(
                f"Certificate name '{info.subject_cn}' does not match the "
                f"expected signer '{ctx.signer_name}'.",
                extra={"name_match_score": round(score, 3)},
            )

        # Reserve the deferred signature --------------------------------------
        pdf_bytes = self.working_store.get_working_pdf(ctx.doc_id)
        base_hash = _sha256(pdf_bytes)

        nonce = secrets.token_urlsafe(24)
        pdf_field_name = f"{ctx.field_name_prefix}_{ctx.signer_id}_{secrets.token_hex(4)}"
        md = self.config.md_algorithm
        reason = f"Signed by {info.subject_cn or ctx.signer_name}"

        # Compose the visible appearance (optional) --------------------------
        appearance_pdf_path = None
        if field_box is not None:
            try:
                from .core.appearance import render_appearance_pdf

                x1, y1, x2, y2 = (float(v) for v in field_box)
                box_w, box_h = abs(x2 - x1), abs(y2 - y1)
                logger.info(
                    "DSC appearance: field_box=%s box=%.1fx%.1f pt on_page=%s "
                    "handwritten=%s cn=%r",
                    field_box, box_w, box_h, on_page,
                    (len(handwritten_png) if handwritten_png else 0), info.subject_cn,
                )
                if box_w > 1 and box_h > 1:
                    appearance_pdf_path = render_appearance_pdf(
                        box_w,
                        box_h,
                        subject_cn=info.subject_cn,
                        signed_time_str=self._now().strftime("%Y.%m.%d %H:%M:%S UTC"),
                        reason=reason,
                        location=self.config.location or None,
                        handwritten_png=handwritten_png,
                    )
                    logger.info("DSC appearance rendered -> %s", appearance_pdf_path)
                else:
                    logger.warning(
                        "DSC appearance skipped: degenerate box %.2fx%.2f", box_w, box_h
                    )
            except Exception:  # non-fatal: fall back to invisible signature
                logger.exception("DSC visible appearance failed; invisible fallback")
                appearance_pdf_path = None
        else:
            logger.info("DSC appearance: no field_box -> invisible cryptographic signature")

        try:
            prepared = prepare_deferred_signature(
                pdf_bytes,
                cert_der,
                field_name=pdf_field_name,
                field_box=field_box,
                on_page=on_page,
                appearance_pdf_path=appearance_pdf_path,
                reason=reason,
                location=self.config.location or None,
                contact_info=ctx.signer_email,
                md_algorithm=md,
                chain_ders=chain_ders or None,
            )
        except Exception as e:
            raise SigningError(f"Failed to prepare signature: {e}")
        finally:
            if appearance_pdf_path:
                try:
                    import os as _os

                    _os.remove(appearance_pdf_path)
                except Exception:
                    pass

        state = {
            "signer_id": ctx.signer_id,
            "doc_id": ctx.doc_id,
            "pdf_field_name": pdf_field_name,
            "base_hash": base_hash,
            "cert_der_b64": base64.b64encode(cert_der).decode(),
            "name_match_score": score,
            "md_algorithm": md,
            "created_at": self._now().isoformat(),
            "prepared": prepared.to_json(),
            # Persist the exact chain used to size the placeholder so finish
            # embeds the identical certs (any mismatch overflows the ByteRange).
            "chain_b64": list(chain or []),
        }
        self.state_store.save(ctx.doc_id, nonce, state)

        to_sign = hash_to_sign(prepared)
        return PrepareResult(
            nonce=nonce,
            hash_hex=to_sign.hex(),
            hash_b64=base64.b64encode(to_sign).decode(),
            md_algorithm=md,
            name_match_score=score,
            cert_info=info,
        )

    # ── Phase 3: finish ──────────────────────────────────────────────────────
    def finish(
        self,
        ctx: SignerContext,
        nonce: str,
        signature_b64: str,
        chain: Optional[List[str]] = None,
    ) -> FinishResult:
        if not nonce or not signature_b64:
            raise SessionError("nonce and signature are required")

        state = self.state_store.load(ctx.doc_id, nonce)
        if not state or str(state.get("signer_id")) != str(ctx.signer_id):
            raise SessionError("Invalid or expired signing session.")

        # TTL check -----------------------------------------------------------
        try:
            created = datetime.fromisoformat(state["created_at"])
            if (self._now() - created).total_seconds() > self.config.prepare_ttl_seconds:
                self.state_store.delete(ctx.doc_id, nonce)
                raise SessionError("Signing session expired. Please try again.")
        except SessionError:
            raise
        except Exception:
            pass

        # Stale-base (concurrency) check --------------------------------------
        current_pdf = self.working_store.get_working_pdf(ctx.doc_id)
        if _sha256(current_pdf) != state["base_hash"]:
            self.state_store.delete(ctx.doc_id, nonce)
            raise StaleDocumentError()

        try:
            signature_value = base64.b64decode(signature_b64)
            cert_der = base64.b64decode(state["cert_der_b64"])
            # Use the chain persisted at prepare (it sized the placeholder). Any
            # deviation here would overflow the reserved ByteRange, so the
            # client-supplied chain is only a fallback for legacy sessions.
            chain_src = state.get("chain_b64")
            if chain_src is None:
                chain_src = chain or []
            chain_ders = [base64.b64decode(c) for c in chain_src]
            prepared = PreparedSignature.from_json(state["prepared"])
        except Exception as e:
            raise SessionError(f"Malformed finish payload: {e}")

        tsa_url = self.config.tsa_url or None
        try:
            final_pdf = finish_deferred_signature(
                prepared, signature_value, cert_der,
                chain_ders=chain_ders or None, tsa_url=tsa_url,
            )
        except Exception as e:
            raise SigningError(f"Failed to embed signature: {e}")

        # Persist the incrementally-signed working PDF ------------------------
        self.working_store.save_working_pdf(ctx.doc_id, final_pdf)

        info = extract_cert_info(cert_der)
        pades_level = "B-T" if tsa_url else "B-B"
        sig_result = SignatureResult(
            pdf_field_name=state["pdf_field_name"],
            cert_info=info,
            md_algorithm=state.get("md_algorithm", "sha256"),
            pades_level=pades_level,
            has_timestamp=bool(tsa_url),
            name_match_score=state.get("name_match_score"),
            bridge=self.config.bridge,
            signature_mechanism=(
                "rsassa_pkcs1v15" if info.public_key_algo == "rsa" else info.public_key_algo
            ),
        )

        # Host-side provenance + field marking + metadata ---------------------
        self.provenance.record(ctx, sig_result)

        if self.audit is not None:
            self.audit.audit(ctx, "dsc_signed", {
                "cert_subject_cn": info.subject_cn,
                "cert_serial": info.serial,
                "cert_issuer": info.issuer,
                "pades_level": pades_level,
                "has_timestamp": bool(tsa_url),
                "name_match_score": state.get("name_match_score"),
                "pdf_field_name": state["pdf_field_name"],
            })

        self.state_store.delete(ctx.doc_id, nonce)

        return FinishResult(
            pades_level=pades_level,
            verified=True,
            cert_subject_cn=info.subject_cn,
            signature=sig_result,
        )
