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
    IsolatedSigner,
    Pkcs11Error,
    WORKER_FLAG,
    config_path,
    macho_arches,
    python_arch,
    resolve_driver_paths,
    worker_main,
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


# ── Signer lifecycle (config is re-read when it changes) ─────────────────
# The companion app can attach/pin a PKCS#11 module at any time. The host is a
# long-lived process (Chrome keeps the port open), so a config read done once at
# startup would leave the user's new choice inert until the browser restarts.
# Watching the config file's mtime keeps the two in sync at zero cost.

class _SignerCache:
    """Rebuilds the signer whenever the shared config file changes."""

    def __init__(self):
        self._signer = None
        self._stamp = None
        self._logged_arch = False

    @staticmethod
    def _config_stamp():
        try:
            st = os.stat(config_path())
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _build(self):
        paths, preferred, strict = resolve_driver_paths()
        if preferred:
            _log_msg(f"Pinned PKCS#11 module: {preferred}"
                     + (" (strict: no other module will be tried)" if strict else ""))
        if paths:
            _log_msg(f"Using {len(paths)} PKCS#11 module(s): "
                     f"{[os.path.basename(p) for p in paths]}")
        else:
            _log_msg("No PKCS#11 driver found. Please install your DSC token's driver.")

        if sys.platform == "darwin" and not self._logged_arch:
            for p in paths:
                arches = macho_arches(p)
                if arches and python_arch() not in arches:
                    _log_msg(
                        f"WARNING: driver {os.path.basename(p)} arch {arches} != Python "
                        f"{python_arch()} — this provider will be skipped at load time."
                    )
            self._logged_arch = True

        return IsolatedSigner(paths, preferred=preferred, strict=strict) if paths else None

    def get(self):
        """Current signer (None when no module is available), rebuilt on change."""
        stamp = self._config_stamp()
        if self._signer is None or stamp != self._stamp:
            self._stamp = stamp
            self._signer = self._build()
        return self._signer


# ── Request handlers ─────────────────────────────────────────────────────

def _handle_ping() -> dict:
    return {"success": True, "pong": True, "version": "1.1.0"}


# Driver selection/ranking now lives in pkcs11_signer (rank_driver_candidates)
# and is applied across ALL detected providers, so the host no longer picks a
# single driver up front.


def _handle_diagnose(signer) -> dict:
    """Return per-provider diagnostics so support can pinpoint token-read failures."""
    try:
        s = signer or IsolatedSigner.from_config()
        return {"success": True, "diagnostics": s.diagnose()}
    except Exception as e:
        _log_msg(f"diagnose error: {traceback.format_exc()}")
        return {"success": False, "error": str(e), "code": "INTERNAL"}


def _handle_list_modules(signer) -> dict:
    """Read-only PKCS#11 module inventory.

    Intentionally read-only: attaching or pinning a module is done ONLY in the
    desktop companion app. Letting a web page name an arbitrary shared library
    for the host to dlopen would be a code-execution vector.
    """
    try:
        s = signer or IsolatedSigner.from_config()
        return {"success": True, **s.list_modules()}
    except Exception as e:
        _log_msg(f"listModules error: {traceback.format_exc()}")
        return {"success": False, "error": str(e), "code": "INTERNAL"}


def _handle_get_certificate(signer: IsolatedSigner, driver: str = "") -> dict:
    try:
        certs = signer.list_certificates(only_driver=driver or None)
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
        detail = str(e) or type(e).__name__
        return {"success": False, "error": f"Failed to read certificates: {detail}", "code": "INTERNAL"}


def _handle_sign_hash(signer: IsolatedSigner, data: dict) -> dict:
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
        detail = str(e) or type(e).__name__
        return {"success": False, "error": f"Signing failed: {detail}", "code": "INTERNAL"}


# ── Main loop ────────────────────────────────────────────────────────────

def main():
    _log_msg("Giddh DSC native host started")

    # Providers come from the shared config: auto-detected drivers plus any
    # module the user attached in the companion app, with an optional pin. The
    # cache re-reads that config whenever it changes, so attaching a module takes
    # effect on the very next request instead of after a browser restart.
    signers = _SignerCache()

    while True:
        msg = _read_message()
        if msg is None:
            _log_msg("stdin closed, exiting")
            break

        action = msg.get("action", "")
        _log_msg(f"Received action: {action}")

        try:
            signer = signers.get()
            if action == "ping":
                _send_message(_handle_ping())
            elif action == "diagnose":
                _send_message(_handle_diagnose(signer))
            elif action == "listModules":
                _send_message(_handle_list_modules(signer))
            elif action == "getCertificate":
                if not signer:
                    _send_message({"success": False, "error": "No PKCS#11 module available. Install your DSC token driver, or attach the module in the Giddh DSC Bridge app.", "code": "NO_DRIVER"})
                else:
                    _send_message(_handle_get_certificate(signer, msg.get("driver", "")))
            elif action == "signHash":
                if not signer:
                    _send_message({"success": False, "error": "No PKCS#11 module available. Install your DSC token driver, or attach the module in the Giddh DSC Bridge app.", "code": "NO_DRIVER"})
                else:
                    _send_message(_handle_sign_hash(signer, msg))
            else:
                _send_message({"success": False, "error": f"Unknown action: {action}", "code": "BAD_ACTION"})
        except Exception as e:
            _log_msg(f"Unhandled error: {traceback.format_exc()}")
            _send_message({"success": False, "error": f"Internal error: {e}", "code": "INTERNAL"})


if __name__ == "__main__":
    # Single-driver worker mode. The broker re-launches this same binary with
    # WORKER_FLAG so that each PKCS#11 library is loaded in its own process and
    # released on exit. Must be checked BEFORE the native-messaging loop, and
    # must not write anything to stdout other than the worker's JSON reply.
    if WORKER_FLAG in sys.argv[1:]:
        sys.exit(worker_main())
    # CLI diagnose mode: the status/companion app ("Check token" button)
    # shells out with --diagnose and expects one plain JSON object on
    # stdout — it is not a browser, so it cannot speak the length-prefixed
    # native-messaging framing the stdin loop below uses. Without this
    # branch the process just blocks on `_read_message()` forever (stdin is
    # the app's own pipe, never closed), and the caller times out.
    if "--diagnose" in sys.argv[1:]:
        result = _handle_diagnose(None)
        print(json.dumps(result))
        sys.exit(0 if result.get("success") else 1)
    main()
