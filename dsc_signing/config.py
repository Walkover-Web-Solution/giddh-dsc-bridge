"""Configuration contract for the DSC signing service.

A plain dataclass so any host can populate it from its own settings source
(Flask config, env vars, a dict, etc.) without coupling to this library.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DscConfig:
    # Digest algorithm the token signs with. Indian DSCs are SHA-256/RSA.
    md_algorithm: str = "sha256"

    # RFC 3161 TSA endpoint. When set, finish upgrades PAdES-B-B -> B-T.
    tsa_url: Optional[str] = None

    # Certificate Verifier: fuzzy-match the cert CN against the declared signer.
    verify_signer_name: bool = True
    name_match_threshold: float = 0.6

    # Allow-list of acceptable issuer substrings (empty = accept any CA).
    allowed_issuers: List[str] = field(default_factory=list)

    # How long a prepared signing session stays valid (seconds).
    prepare_ttl_seconds: int = 600

    # Free-text location stamped into the signature (host branding).
    location: str = ""

    # Identifier of the client bridge used (recorded in provenance).
    bridge: str = "signer_digital"

    @classmethod
    def from_mapping(cls, m) -> "DscConfig":
        """Build from any mapping using the host's UPPER_SNAKE keys.

        Recognised keys (all optional):
          DSC_MD_ALGORITHM, DSC_TSA_URL, DSC_VERIFY_SIGNER_NAME,
          DSC_NAME_MATCH_THRESHOLD, DSC_ALLOWED_ISSUERS,
          DSC_PREPARE_TTL_SECONDS, DSC_LOCATION, DSC_BRIDGE
        """
        get = m.get
        return cls(
            md_algorithm=get("DSC_MD_ALGORITHM", "sha256"),
            tsa_url=get("DSC_TSA_URL") or None,
            verify_signer_name=bool(get("DSC_VERIFY_SIGNER_NAME", True)),
            name_match_threshold=float(get("DSC_NAME_MATCH_THRESHOLD", 0.6)),
            allowed_issuers=list(get("DSC_ALLOWED_ISSUERS", []) or []),
            prepare_ttl_seconds=int(get("DSC_PREPARE_TTL_SECONDS", 600)),
            location=get("DSC_LOCATION", "") or "",
            bridge=get("DSC_BRIDGE", "signer_digital") or "signer_digital",
        )
