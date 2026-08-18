"""
DSC certificate parsing + Certificate Verifier helpers.
=======================================================
Pure, dependency-light helpers (no Flask/DB) so they can be unit-tested in
isolation. Used by the DSC signing service to:

  * extract legally-relevant metadata from a signer's X.509 certificate
    (subject CN, serial, issuer, validity) for the audit trail;
  * fuzzy-match the certificate subject name against the recipient's declared
    name (Leegality-style Certificate Verifier);
  * check the issuer against an allow-list of CCA-licensed CAs.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import List, Optional


@dataclass(frozen=True)
class CertInfo:
    subject_cn: Optional[str]
    serial: Optional[str]
    issuer: Optional[str]
    issuer_cn: Optional[str]
    not_before: Optional[datetime]
    not_after: Optional[datetime]
    public_key_algo: Optional[str]
    pem: str


def load_cert_der(cert_der: bytes):
    from cryptography import x509

    return x509.load_der_x509_certificate(cert_der)


def cert_der_from_any(cert_data) -> bytes:
    """Accept raw DER bytes, base64 str/bytes, or PEM and return DER bytes."""
    if isinstance(cert_data, str):
        cert_data = cert_data.strip()
        if "BEGIN CERTIFICATE" in cert_data:
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(cert_data.encode())
            from cryptography.hazmat.primitives import serialization

            return cert.public_bytes(serialization.Encoding.DER)
        # assume base64 DER
        return base64.b64decode(cert_data)
    if isinstance(cert_data, (bytes, bytearray)):
        b = bytes(cert_data)
        # PEM in bytes?
        if b.lstrip().startswith(b"-----BEGIN"):
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization

            cert = x509.load_pem_x509_certificate(b)
            return cert.public_bytes(serialization.Encoding.DER)
        return b
    raise ValueError("Unsupported certificate input type")


def _name_attr(name, oid) -> Optional[str]:
    try:
        vals = name.get_attributes_for_oid(oid)
        return vals[0].value if vals else None
    except Exception:
        return None


def extract_cert_info(cert_der: bytes) -> CertInfo:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization

    cert = load_cert_der(cert_der)
    subject_cn = _name_attr(cert.subject, NameOID.COMMON_NAME)
    issuer_cn = _name_attr(cert.issuer, NameOID.COMMON_NAME)
    issuer_o = _name_attr(cert.issuer, NameOID.ORGANIZATION_NAME)
    issuer = ", ".join([p for p in (issuer_cn, issuer_o) if p]) or None

    try:
        not_before = cert.not_valid_before_utc.replace(tzinfo=None)
        not_after = cert.not_valid_after_utc.replace(tzinfo=None)
    except AttributeError:  # older cryptography
        not_before = cert.not_valid_before
        not_after = cert.not_valid_after

    try:
        algo = cert.public_key().__class__.__name__.lower()
        if "rsa" in algo:
            algo = "rsa"
        elif "ec" in algo:
            algo = "ec"
    except Exception:
        algo = None

    pem = cert.public_bytes(serialization.Encoding.PEM).decode()

    return CertInfo(
        subject_cn=subject_cn,
        serial=format(cert.serial_number, "x"),
        issuer=issuer,
        issuer_cn=issuer_cn,
        not_before=not_before,
        not_after=not_after,
        public_key_algo=algo,
        pem=pem,
    )


def _normalise_name(name: str) -> str:
    name = (name or "").lower()
    # Drop common honorifics/suffixes and non-alpha noise.
    name = re.sub(r"\b(mr|mrs|ms|dr|shri|smt|kum)\b\.?", " ", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return " ".join(name.split())


def name_match_score(declared_name: str, cert_cn: str) -> float:
    """Return a 0..1 similarity score between the declared and certificate name.

    Token-aware: also credits the case where every declared token appears in
    the certificate CN (initials/reordering are common in DSCs).
    """
    a = _normalise_name(declared_name)
    b = _normalise_name(cert_cn)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()

    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if a_tokens:
        contained = len(a_tokens & b_tokens) / len(a_tokens)
    else:
        contained = 0.0

    return max(ratio, contained)


def is_expired(info: CertInfo, at: Optional[datetime] = None) -> bool:
    at = at or datetime.utcnow()
    if info.not_before and at < info.not_before:
        return True
    if info.not_after and at > info.not_after:
        return True
    return False


def issuer_allowed(info: CertInfo, allowed_issuers: List[str]) -> bool:
    """True if allow-list is empty (accept all) or issuer matches a substring."""
    if not allowed_issuers:
        return True
    hay = " ".join([p for p in (info.issuer, info.issuer_cn) if p]).lower()
    return any(s.lower() in hay for s in allowed_issuers)
