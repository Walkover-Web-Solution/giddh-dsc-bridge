#!/usr/bin/env python3
"""
Giddh DSC Bridge — Standalone Self-Test
=========================================
Proves the ENTIRE token stack works on this machine, end-to-end, WITHOUT the
browser extension and WITHOUT Giddh. Run this first when validating a machine
or before handing the bridge to another team.

Stages (each printed PASS/FAIL):
  1. Driver detection  — the same auto-selection the native host uses.
  2. Driver load       — the PKCS#11 library initialises.
  3. List certificates — reads certs off the token (usually no PIN needed).
  4. Sign              — the token signs the SHA-256 of a test string (PIN).
  5. Verify            — the returned signature is checked against the cert's
                         public key with `cryptography` (proves the signature is
                         a valid RSASSA-PKCS1-v1_5 signature over the digest).

Usage:
    python3 selftest.py                 # interactive (prompts for PIN)
    python3 selftest.py --no-sign       # stop after listing certificates
    python3 selftest.py --pin 12345678  # non-interactive (avoid in shared shells)
    python3 selftest.py --driver /path/to/driver.dylib

Exit code is 0 only if every attempted stage passes.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Import the SAME modules the native host uses, so this test exercises the real
# code path (driver ranking, cert parsing, signing) rather than a parallel copy.
try:
    from pkcs11_signer import Pkcs11Signer, Pkcs11Error, list_driver_candidates
    from giddh_dsc_host import _select_driver
except Exception as e:  # pragma: no cover - import guard
    print(f"FATAL: cannot import bridge modules from {HERE}: {e}")
    print("       Run this from the native-host/ directory and install deps:")
    print("       pip install -r requirements.txt")
    sys.exit(2)


GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = DIM = RESET = ""


def ok(msg):
    print(f"{GREEN}  PASS{RESET} {msg}")


def fail(msg):
    print(f"{RED}  FAIL{RESET} {msg}")


def info(msg):
    print(f"{DIM}       {msg}{RESET}")


def stage(n, title):
    print(f"\n[{n}] {title}")


def main():
    ap = argparse.ArgumentParser(description="Giddh DSC Bridge self-test")
    ap.add_argument("--driver", help="Explicit PKCS#11 driver path (skip auto-detect)")
    ap.add_argument("--pin", help="Token PIN (omit to be prompted securely)")
    ap.add_argument("--no-sign", action="store_true", help="Stop after listing certificates")
    ap.add_argument("--text", default="Giddh DSC Bridge self-test", help="Text to sign")
    args = ap.parse_args()

    print("=" * 60)
    print(" Giddh DSC Bridge — Standalone Self-Test")
    print("=" * 60)

    # ── Stage 1: driver detection ────────────────────────────────────────
    stage(1, "Driver detection")
    driver = args.driver
    if driver:
        info(f"using explicit driver: {driver}")
    else:
        cands = list_driver_candidates()
        if not cands:
            fail("no PKCS#11 driver found on this system")
            info("install your token vendor's driver (WatchData/ProxKey, SafeNet")
            info("eToken, Feitian ePass2003, …) and re-run.")
            return 1
        driver = _select_driver(cands)
        ok(f"selected driver: {driver}")
        for c in cands:
            marker = " <-- selected" if c == driver else ""
            info(f"candidate: {c}{marker}")
    if not driver or not os.path.exists(driver):
        fail(f"driver path does not exist: {driver}")
        return 1

    signer = Pkcs11Signer(driver)

    # ── Stage 2: driver load + diagnostics ───────────────────────────────
    stage(2, "Driver load / diagnostics")
    diag = signer.diagnose()
    info("diagnostics: " + json.dumps({
        k: diag.get(k) for k in
        ("platform", "python_arch", "driver_loads", "arch_mismatch", "slots", "tokens", "error")
    }, default=str))
    if not diag.get("driver_loads"):
        fail("PKCS#11 driver failed to load — see diagnostics above")
        if diag.get("arch_mismatch"):
            info("architecture mismatch: rebuild/reinstall a matching-arch Python")
        return 1
    ok("driver loaded")
    if not diag.get("tokens"):
        fail("driver loaded but NO token present — plug in / re-seat the token")
        return 1
    ok(f"token(s) present: {diag.get('tokens')}")

    # ── Stage 3: list certificates ───────────────────────────────────────
    stage(3, "List certificates")
    try:
        certs = signer.list_certificates()
    except Pkcs11Error as e:
        fail(f"{e} (code={e.code})")
        return 1
    if not certs:
        fail("no certificates on the token")
        return 1
    ok(f"read {len(certs)} certificate(s)")
    for c in certs:
        info(f"- {c.subject_cn or '?'} | issuer={c.issuer_cn or '?'} | id={c.cert_id_hex}")

    signing_certs = [c for c in certs if c.subject_cn] or certs
    chosen = signing_certs[0]
    info(f"will sign with: {chosen.subject_cn or chosen.cert_id_hex}")

    if args.no_sign:
        print(f"\n{GREEN}All non-signing stages passed.{RESET} (--no-sign)")
        return 0

    # ── Stage 4: sign a test hash ────────────────────────────────────────
    stage(4, "Sign a test hash")
    message = args.text.encode("utf-8")
    digest = hashlib.sha256(message).digest()
    hash_b64 = base64.b64encode(digest).decode()
    pin = args.pin or getpass.getpass("       Token PIN: ")
    if not pin:
        fail("no PIN provided")
        return 1
    try:
        sig_b64 = signer.sign_hash(hash_b64, "SHA256", chosen.cert_id_hex, pin)
    except Pkcs11Error as e:
        fail(f"{e} (code={e.code})")
        return 1
    signature = base64.b64decode(sig_b64)
    ok(f"token returned a {len(signature)}-byte signature")

    # ── Stage 5: verify signature against the certificate public key ─────
    stage(5, "Verify signature (cryptography)")
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa, ec, utils

        cert = x509.load_der_x509_certificate(base64.b64decode(chosen.cert_b64))
        pub = cert.public_key()
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(signature, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        else:
            fail(f"unsupported key type: {type(pub).__name__}")
            return 1
        ok("signature is cryptographically VALID for the certificate's public key")
    except Exception as e:
        fail(f"signature verification FAILED: {e}")
        info("the token signed, but the signature does not match the certificate —")
        info("this usually means a cert/key ID mismatch on the token.")
        return 1

    print(f"\n{GREEN}{'=' * 60}{RESET}")
    print(f"{GREEN} ALL STAGES PASSED — the DSC token stack is working end-to-end.{RESET}")
    print(f"{GREEN}{'=' * 60}{RESET}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
