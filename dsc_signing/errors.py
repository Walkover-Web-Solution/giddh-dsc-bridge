"""Typed errors raised by the DSC signing service.

Each error carries a machine-readable ``code`` and a suggested ``http_status``
so a host's transport layer can map them to responses without string-matching.
The library never imports any web framework; these are plain exceptions.
"""
from __future__ import annotations

from typing import Optional


class DscError(Exception):
    """Base class for all DSC signing errors."""

    code: str = "dsc_error"
    http_status: int = 400

    def __init__(self, message: str, *, code: Optional[str] = None,
                 http_status: Optional[int] = None, extra: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.extra = extra or {}

    def to_dict(self) -> dict:
        d = {"success": False, "error": self.message, "code": self.code}
        d.update(self.extra)
        return d


class NotAllowedError(DscError):
    """DSC signing is disabled or not permitted for this signer/field."""

    code = "dsc_not_allowed"
    http_status = 400


class CertificateError(DscError):
    """The supplied certificate is missing, malformed, or unparseable."""

    code = "dsc_bad_certificate"
    http_status = 400


class VerificationError(DscError):
    """Certificate failed a verifier check (expiry, issuer, name match)."""

    code = "dsc_verification_failed"
    http_status = 400


class SessionError(DscError):
    """The prepared signing session is missing, expired, or mismatched."""

    code = "dsc_invalid_session"
    http_status = 400


class StaleDocumentError(DscError):
    """The working PDF advanced between prepare and finish; client must retry."""

    code = "dsc_stale_document"
    http_status = 409

    def __init__(self, message: str = "The document changed during signing. Please retry."):
        super().__init__(message, extra={"stale": True})


class SigningError(DscError):
    """An unexpected failure while preparing or embedding the signature."""

    code = "dsc_signing_failed"
    http_status = 500
