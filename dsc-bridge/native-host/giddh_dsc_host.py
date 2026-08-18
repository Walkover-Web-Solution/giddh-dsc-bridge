#!/usr/bin/env python3
"""
Giddh DSC Bridge — Native Messaging Host
==========================================
Chrome/Edge native messaging host that bridges the browser to a hardware
DSC token via PKCS#11.  Communicates with the extension over stdin/stdout
using the Chrome native messaging protocol (4-byte little-endian length
prefix + UTF-8 JSON).

Message protocol (extension → host):
  {"action": "ping"}
  {"action": "getCertificate"}
  {"action": "signHash", "hash": "<base64>", "algorithm": "SHA256",
   "certId": "<hex-id>", "pin": "<token-pin>"}

Response (host → extension):
  {"success": true, ...}
  {"success": false, "error": "...", "code": "..."}

Security:
  * The private key never leaves the token — C_Sign happens in hardware.
  * The PIN is received from the browser (local pipe, no network) and
    discarded immediately after use.
  * stderr is used for logging (Chrome captures it; never write to stdout
    except via the framing protocol).
"""
from __future__ import annotations

import json
import os
import struct
import sys
import traceback

# Allow importing pkcs11_signer whether running as script or frozen (PyInstaller).
_HERE = os.path.dirname(os.path.abspath(sys.argv[0] if not getattr(sys, "frozen", False) else sys.executable))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from pkcs11_signer import (
    Pkcs11Signer,
    Pkcs11Error,
    list_driver_candidates,
    macho_arches,
    python_arch,
)

_log = sys.stderr


def _log_msg(msg: str):
    print(f"[giddh-dsc-host] {msg}", file=_log, flush=True)


# ── Chrome native messaging I/O ──────────────────────────────────────────

def _read_message() -> dict | None:
    """Read one length-prefixed JSON message from stdin."""
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        return None
    msg_len = struct.unpack("<I", raw_len)[0]
    if msg_len == 0 or msg_len > 10 * 1024 * 1024:
        _log_msg(f"Invalid message length: {msg_len}")
        return None
    raw = sys.stdin.buffer.read(msg_len)
    if len(raw) < msg_len:
        return None
    return json.loads(raw.decode("utf-8"))


def _send_message(msg: dict):
    """Write one length-prefixed JSON message to stdout."""
    raw = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(raw)))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


# ── Request handlers ─────────────────────────────────────────────────────

def _handle_ping() -> dict:
    return {"success": True, "pong": True, "version": "1.1.0"}


# Driver selection/ranking now lives in pkcs11_signer (rank_driver_candidates)
# and is applied across ALL detected providers, so the host no longer picks a
# single driver up front.


def _handle_diagnose(signer) -> dict:
    """Return per-provider diagnostics so support can pinpoint token-read failures."""
    try:
        s = signer or Pkcs11Signer(list_driver_candidates())
        return {"success": True, "diagnostics": s.diagnose()}
    except Exception as e:
        _log_msg(f"diagnose error: {traceback.format_exc()}")
        return {"success": False, "error": str(e), "code": "INTERNAL"}


def _handle_get_certificate(signer: Pkcs11Signer) -> dict:
    try:
        certs = signer.list_certificates()
        if not certs:
            return {"success": False, "error": "No certificates found on the token. Ensure your DSC token is plugged in.", "code": "NO_CERTS"}
        return {
            "success": True,
            "certificates": [c.to_dict() for c in certs],
        }
    except Pkcs11Error as e:
        return {"success": False, "error": str(e), "code": e.code}
    except Exception as e:
        _log_msg(f"getCertificate error: {traceback.format_exc()}")
        return {"success": False, "error": f"Failed to read certificates: {e}", "code": "INTERNAL"}


def _handle_sign_hash(signer: Pkcs11Signer, data: dict) -> dict:
    hash_b64 = data.get("hash")
    algorithm = data.get("algorithm", "SHA256").upper()
    cert_id = data.get("certId", "")
    pin = data.get("pin", "")

    if not hash_b64:
        return {"success": False, "error": "Missing hash to sign.", "code": "BAD_REQUEST"}
    if not pin:
        return {"success": False, "error": "Token PIN is required for signing.", "code": "PIN_REQUIRED"}

    try:
        signature_b64 = signer.sign_hash(hash_b64, algorithm, cert_id, pin)
        return {"success": True, "signature": signature_b64}
    except Pkcs11Error as e:
        return {"success": False, "error": str(e), "code": e.code}
    except Exception as e:
        _log_msg(f"signHash error: {traceback.format_exc()}")
        return {"success": False, "error": f"Signing failed: {e}", "code": "INTERNAL"}


# ── Main loop ────────────────────────────────────────────────────────────

def main():
    _log_msg("Giddh DSC native host started")

    # Auto-detect or load PKCS#11 driver from config.
    driver_path = None
    config_path = _get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
                driver_path = cfg.get("pkcs11_driver")
        except Exception:
            pass

    # Build the list of providers to try. A machine can have SEVERAL PKCS#11
    # drivers installed at once (e.g. WatchData for a Capricorn token AND Feitian
    # for an mToken); we hand the signer ALL of them and let it pick, per
    # operation, whichever library actually has a token present.
    if driver_path:
        driver_paths = [driver_path]
        _log_msg(f"Using configured PKCS#11 driver: {driver_path}")
    else:
        driver_paths = list_driver_candidates()
        if driver_paths:
            _log_msg(
                f"Detected {len(driver_paths)} PKCS#11 driver candidate(s): "
                f"{[os.path.basename(p) for p in driver_paths]}"
            )
        else:
            _log_msg("No PKCS#11 driver found. Please install your DSC token's driver.")

    if sys.platform == "darwin":
        for p in driver_paths:
            arches = macho_arches(p)
            if arches and python_arch() not in arches:
                _log_msg(
                    f"WARNING: driver {os.path.basename(p)} arch {arches} != Python "
                    f"{python_arch()} — this provider will be skipped at load time."
                )

    signer = Pkcs11Signer(driver_paths) if driver_paths else None

    while True:
        msg = _read_message()
        if msg is None:
            _log_msg("stdin closed, exiting")
            break

        action = msg.get("action", "")
        _log_msg(f"Received action: {action}")

        try:
            if action == "ping":
                _send_message(_handle_ping())
            elif action == "diagnose":
                _send_message(_handle_diagnose(signer))
            elif action == "getCertificate":
                if not signer:
                    _send_message({"success": False, "error": "No PKCS#11 driver configured. Please install your DSC token driver and restart.", "code": "NO_DRIVER"})
                else:
                    _send_message(_handle_get_certificate(signer))
            elif action == "signHash":
                if not signer:
                    _send_message({"success": False, "error": "No PKCS#11 driver configured.", "code": "NO_DRIVER"})
                else:
                    _send_message(_handle_sign_hash(signer, msg))
            else:
                _send_message({"success": False, "error": f"Unknown action: {action}", "code": "BAD_ACTION"})
        except Exception as e:
            _log_msg(f"Unhandled error: {traceback.format_exc()}")
            _send_message({"success": False, "error": f"Internal error: {e}", "code": "INTERNAL"})


def _get_config_path() -> str:
    """Platform-specific config file path."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Giddh", "dsc-bridge.json")
    elif sys.platform.startswith("linux"):
        return os.path.join(home, ".config", "giddh", "dsc-bridge.json")
    elif sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", home), "Giddh", "dsc-bridge.json")
    return os.path.join(home, ".giddh", "dsc-bridge.json")


if __name__ == "__main__":
    main()
