"""Ports (interfaces) a host application implements to use the DSC service.

The library defines WHAT it needs; the host decides HOW. Giddh implements
these against its storage adapter, SQLAlchemy models and audit log; another
product would implement them against its own infrastructure.

Also included are trivial in-memory implementations (``InMemoryStateStore``,
``InMemoryWorkingPdfStore``, ``NullAuditSink``) useful for tests and for
hosts that only need ephemeral behaviour.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional, Tuple

from .models import SignerContext, SignatureResult


class WorkingPdfStore(ABC):
    """Storage for the per-document "working PDF" that signatures append to."""

    @abstractmethod
    def get_working_pdf(self, doc_id: str) -> bytes:
        """Return current working-PDF bytes, building+persisting a base if none."""

    @abstractmethod
    def save_working_pdf(self, doc_id: str, pdf_bytes: bytes) -> None:
        """Persist the incrementally-signed working PDF."""


class StateStore(ABC):
    """Storage for the opaque prepared-signature state, keyed by (doc_id, nonce)."""

    @abstractmethod
    def save(self, doc_id: str, nonce: str, state: dict) -> None: ...

    @abstractmethod
    def load(self, doc_id: str, nonce: str) -> Optional[dict]: ...

    @abstractmethod
    def delete(self, doc_id: str, nonce: str) -> None: ...


class ProvenanceSink(ABC):
    """Records provenance of an applied signature (DB row, field marking, etc.).

    Called by ``finish`` AFTER the working PDF is persisted. Any host-side
    transaction is committed by the host after the service returns.
    """

    @abstractmethod
    def record(self, ctx: SignerContext, result: SignatureResult) -> None: ...


class AuditSink(ABC):
    """Optional audit trail hook."""

    @abstractmethod
    def audit(self, ctx: SignerContext, action: str, details: dict) -> None: ...


# ── Trivial in-memory implementations (tests / ephemeral hosts) ───────────────

class InMemoryStateStore(StateStore):
    def __init__(self):
        self._d: Dict[Tuple[str, str], dict] = {}

    def save(self, doc_id, nonce, state):
        self._d[(str(doc_id), nonce)] = state

    def load(self, doc_id, nonce):
        return self._d.get((str(doc_id), nonce))

    def delete(self, doc_id, nonce):
        self._d.pop((str(doc_id), nonce), None)


class InMemoryWorkingPdfStore(WorkingPdfStore):
    """Keeps working PDFs in a dict; builds the base via a supplied callable."""

    def __init__(self, base_builder: Callable[[str], bytes]):
        self._pdfs: Dict[str, bytes] = {}
        self._base_builder = base_builder

    def get_working_pdf(self, doc_id):
        doc_id = str(doc_id)
        if doc_id not in self._pdfs:
            self._pdfs[doc_id] = self._base_builder(doc_id)
        return self._pdfs[doc_id]

    def save_working_pdf(self, doc_id, pdf_bytes):
        self._pdfs[str(doc_id)] = pdf_bytes


class NullAuditSink(AuditSink):
    def audit(self, ctx, action, details):
        return None
