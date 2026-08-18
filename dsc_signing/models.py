"""Plain data contracts exchanged with the host application.

These carry no behaviour and no host/framework types — just the minimal
information the service needs (in) and produces (out).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .core.cert_verifier import CertInfo


@dataclass
class SignerContext:
    """Identifies who is signing which document, resolved by the host.

    ``doc_id`` and ``signer_id`` are opaque to the library — they are only used
    as storage keys and echoed back to the host's ports. The host is
    responsible for authenticating/authorising the signer BEFORE calling the
    service (e.g. validating an access token and that DSC is an allowed method).
    """

    doc_id: str
    signer_id: str
    signer_name: str
    signer_email: Optional[str] = None
    # Prefix used to build the unique in-PDF signature field name.
    field_name_prefix: str = "DSC"


@dataclass
class PrepareResult:
    """Returned to the client after a successful prepare."""

    nonce: str
    hash_hex: str
    hash_b64: str
    md_algorithm: str
    name_match_score: float
    cert_info: CertInfo

    def to_dict(self) -> dict:
        return {
            "success": True,
            "nonce": self.nonce,
            "hash": self.hash_hex,
            "hash_b64": self.hash_b64,
            "md_algorithm": self.md_algorithm,
            "name_match_score": round(self.name_match_score, 3),
            "cert": {
                "subject_cn": self.cert_info.subject_cn,
                "issuer": self.cert_info.issuer,
                "serial": self.cert_info.serial,
                "valid_from": self.cert_info.not_before.isoformat() if self.cert_info.not_before else None,
                "valid_to": self.cert_info.not_after.isoformat() if self.cert_info.not_after else None,
            },
        }


@dataclass
class SignatureResult:
    """Full provenance of an applied DSC signature (passed to ProvenanceSink)."""

    pdf_field_name: str
    cert_info: CertInfo
    md_algorithm: str
    pades_level: str
    has_timestamp: bool
    name_match_score: Optional[float]
    bridge: str
    signature_mechanism: Optional[str] = None


@dataclass
class FinishResult:
    """Returned to the client after a successful finish."""

    pades_level: str
    verified: bool
    cert_subject_cn: Optional[str]
    signature: SignatureResult

    def to_dict(self) -> dict:
        return {
            "success": True,
            "pades_level": self.pades_level,
            "verified": self.verified,
            "cert_subject_cn": self.cert_subject_cn,
            "message": "DSC signature applied. Complete signing to finalise your turn.",
        }
