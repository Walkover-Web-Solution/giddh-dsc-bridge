"""
dsc_signing — portable DSC (Digital Signature Certificate) token signing.
=========================================================================
A self-contained, framework-agnostic library implementing the deferred
PAdES ("sign the hash") flow used with hardware crypto tokens via a
client-side bridge (e.g. the Signer.Digital browser extension).

Design goal: ZERO hard dependencies on any host application (no Flask, no
SQLAlchemy, no host-specific imports). A host integrates by:

  1. implementing the small set of ports in ``dsc_signing.ports`` (storage,
     provenance, audit) — or reusing provided in-memory ports for tests;
  2. constructing a :class:`dsc_signing.service.DscSigningService` with a
     :class:`dsc_signing.config.DscConfig` and those ports;
  3. calling ``service.prepare(...)`` / ``service.finish(...)`` from its own
     transport layer (an HTTP endpoint, a queue worker, a CLI, etc).

This makes the exact same signing engine reusable across products: each host
supplies its own adapters while the crypto + protocol live here.
"""
from __future__ import annotations

from .config import DscConfig
from .errors import (
    DscError,
    CertificateError,
    VerificationError,
    SessionError,
    StaleDocumentError,
    SigningError,
    NotAllowedError,
)
from .models import SignerContext, PrepareResult, FinishResult, SignatureResult
from .service import DscSigningService

__all__ = [
    "DscConfig",
    "DscError",
    "CertificateError",
    "VerificationError",
    "SessionError",
    "StaleDocumentError",
    "SigningError",
    "NotAllowedError",
    "SignerContext",
    "PrepareResult",
    "FinishResult",
    "SignatureResult",
    "DscSigningService",
]

__version__ = "0.1.0"
