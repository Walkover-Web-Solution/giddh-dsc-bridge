"""
PKCS#11 signer - talks to hardware DSC tokens via the standard PKCS#11 API.

Uses python-pkcs11 (pip install python-pkcs11) which wraps the C PKCS#11
interface.  All Indian DSC tokens (WatchData, SafeNet/Gemalto, eMudhra,
TrustKey) ship a PKCS#11 shared library that this module loads.

Security:
  * The private key never leaves the token.  C_Sign is executed in hardware.
  * The PIN is passed to C_Login and never stored.
  * Sessions are short-lived: opened per operation, closed immediately after.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import pkcs11
    from pkcs11 import Session, Slot, Mechanism, Attribute, ObjectClass
    from pkcs11.exceptions import (
        PKCS11Error as _Pkcs11BaseError,
        NoSuchToken,
        PinIncorrect,
        PinLocked,
    )
except ImportError:
    pkcs11 = None


# -- Logging (stderr; Chrome captures it — stdout is reserved for framing) ----
def _log(msg: str) -> None:
    """Best-effort stage logging to stderr; never raises."""
    try:
        print(f"[giddh-dsc-host:pkcs11] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


class Pkcs11Error(Exception):
    """Typed error with a machine-readable code."""

    def __init__(self, message: str, code: str = "PKCS11_ERROR"):
        super().__init__(message)
        self.code = code


# -- Certificate info -------------------------------------------------------

@dataclass
class CertInfo:
    cert_id_hex: str
    cert_b64: str
    subject_cn: Optional[str]
    issuer_cn: Optional[str]
    serial_hex: Optional[str]
    not_before: Optional[str]
    not_after: Optional[str]
    is_ca: bool = False
    # DER (base64) of the issuer chain (intermediates + root) read off the same
    # token — attached to a signing cert so the CMS can embed the full path.
    chain_b64: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "certId": self.cert_id_hex,
            "certB64": self.cert_b64,
            "subjectCn": self.subject_cn,
            "issuerCn": self.issuer_cn,
            "serial": self.serial_hex,
            "notBefore": self.not_before,
            "notAfter": self.not_after,
            "isCa": self.is_ca,
            "chain": self.chain_b64,
        }


# -- Driver auto-detection --------------------------------------------------

_DRIVER_CANDIDATES = {
    "darwin": [
        # WatchData / ProxKey (very common for Indian DSC tokens)
        "/usr/local/lib/wdProxKeyUsbKeyTool/libwdpkcs_Proxkey.dylib",
        "/Library/WatchData/ProxKey/libwdpkcs_Proxkey.dylib",
        "/Library/WatchData/libwdpkcs11.dylib",
        "/usr/local/lib/libwdpkcs11.dylib",
        "/usr/lib/libwdpkcs11.dylib",
        # SafeNet / Gemalto / Thales eToken
        "/Library/SafeNet/libeTPkcs11.dylib",
        "/usr/local/lib/libeTPkcs11.dylib",
        "/usr/lib/libeTPkcs11.dylib",
        "/Library/Frameworks/eToken.framework/Versions/Current/libeTPkcs11.dylib",
        # Feitian ePass2003 / mToken (eMudhra, Capricorn, many resellers)
        "/usr/local/lib/libcastle.dylib",
        "/usr/local/lib/libcastle.1.0.0.dylib",
        "/Library/Frameworks/eps2003csp11.framework/eps2003csp11",
        "/usr/local/lib/libePass2003.dylib",
        # Longmai mToken CryptoID (CryptoIDA / CryptoID-A User Tool)
        "/Applications/CryptoIDATools.app/Contents/MacOS/libcryptoid_pkcs11.dylib",
        "/usr/local/lib/libcryptoid_pkcs11.dylib",
        "/usr/lib/libcryptoid_pkcs11.dylib",
        # TrustKey / IDEMIA / Aladdin
        "/usr/local/lib/libtrustkey.dylib",
        "/usr/local/lib/libIDPrimePKCS11.dylib",
        "/usr/lib/libIDPrimePKCS11.dylib",
        # OpenSC (generic fallback if installed)
        "/Library/OpenSC/lib/opensc-pkcs11.so",
        "/usr/local/lib/opensc-pkcs11.so",
        "/opt/homebrew/lib/opensc-pkcs11.so",
    ],
    "linux": [
        "/usr/lib/libeTPkcs11.so",
        "/usr/lib/libwdpkcs11.so",
        "/usr/lib/x86_64-linux-gnu/libeTPkcs11.so",
        "/usr/lib/x86_64-linux-gnu/libwdpkcs11.so",
        "/usr/lib/pkcs11/libeTPkcs11.so",
        "/usr/lib/pkcs11/libwdpkcs11.so",
        "/usr/local/lib/libeTPkcs11.so",
        "/usr/local/lib/libwdpkcs11.so",
        "/opt/safenet/libeTPkcs11.so",
        "/opt/watchdata/libwdpkcs11.so",
    ],
    "win32": [
        # SafeNet / Gemalto / Thales eToken
        "C:\\Windows\\System32\\eTPkcs11.dll",
        "C:\\Windows\\SysWOW64\\eTPkcs11.dll",
        # WatchData / ProxKey (Capricorn et al.)
        "C:\\Windows\\System32\\wdpkcs11.dll",
        "C:\\Windows\\SysWOW64\\wdpkcs11.dll",
        "C:\\Windows\\System32\\libwdpkcs_Proxkey.dll",
        "C:\\Windows\\SysWOW64\\libwdpkcs_Proxkey.dll",
        "C:\\Windows\\System32\\Watchdata\\PROXKey CSP India V3.0\\WDPKCS.dll",
        "C:\\Windows\\SysWOW64\\Watchdata\\PROXKey CSP India V3.0\\WDPKCS.dll",
        "C:\\Program Files\\WatchData\\PROXKey\\libwdpkcs_Proxkey.dll",
        "C:\\Program Files (x86)\\WatchData\\PROXKey\\libwdpkcs_Proxkey.dll",
        "C:\\Program Files\\Watchdata\\wdProxKeyUsbKeyTool\\libwdpkcs_Proxkey.dll",
        "C:\\Program Files (x86)\\Watchdata\\wdProxKeyUsbKeyTool\\libwdpkcs_Proxkey.dll",
        # Feitian ePass2003 / mToken (eMudhra, Capricorn, many resellers)
        "C:\\Windows\\System32\\eps2003csp11.dll",
        "C:\\Windows\\SysWOW64\\eps2003csp11.dll",
        "C:\\Windows\\System32\\eps2003csp11v2.dll",
        "C:\\Windows\\SysWOW64\\eps2003csp11v2.dll",
        "C:\\Windows\\System32\\ShuttleCsp11_3003.dll",
        "C:\\Windows\\SysWOW64\\ShuttleCsp11_3003.dll",
        "C:\\Windows\\System32\\SignatureP11.dll",
        "C:\\Windows\\SysWOW64\\SignatureP11.dll",
        "C:\\Windows\\System32\\castle.dll",
        "C:\\Windows\\SysWOW64\\castle.dll",
        "C:\\Program Files\\Feitian\\ePass2003\\eps2003csp11.dll",
        "C:\\Program Files (x86)\\Feitian\\ePass2003\\eps2003csp11.dll",
        "C:\\Program Files\\mToken\\SignatureP11.dll",
        "C:\\Program Files (x86)\\mToken\\SignatureP11.dll",
        # Longmai mToken CryptoID (CryptoIDA). NOTE: the real DLL name is
        # "CryptoIDA_pkcs11.dll"; System32 holds the 64-bit build, SysWOW64 the
        # 32-bit one. The vendor installer puts a copy under Program Files\CryptoID.
        "C:\\Windows\\System32\\CryptoIDA_pkcs11.dll",
        "C:\\Windows\\SysWOW64\\CryptoIDA_pkcs11.dll",
        "C:\\Program Files\\CryptoID\\CryptoIDA_pkcs11.dll",
        "C:\\Program Files (x86)\\CryptoID\\CryptoIDA_pkcs11.dll",
        "C:\\Windows\\System32\\mtoken_gm3000.dll",
        "C:\\Windows\\SysWOW64\\mtoken_gm3000.dll",
        # IDEMIA / Gemalto IDPrime
        "C:\\Windows\\System32\\idprimepkcs11.dll",
        "C:\\Windows\\SysWOW64\\idprimepkcs11.dll",
        "C:\\Windows\\System32\\eTPKCS11.dll",
        # Aladdin / TrustKey / Moserbaer / GClib (assorted Indian DSC middleware)
        "C:\\Windows\\System32\\trustkey.dll",
        "C:\\Windows\\SysWOW64\\trustkey.dll",
        "C:\\Windows\\System32\\gclib.dll",
        "C:\\Windows\\SysWOW64\\gclib.dll",
        "C:\\Windows\\System32\\pkcs11.dll",
        "C:\\Windows\\SysWOW64\\pkcs11.dll",
        # OpenSC (generic fallback if installed)
        "C:\\Program Files\\OpenSC Project\\OpenSC\\pkcs11\\opensc-pkcs11.dll",
        "C:\\Program Files (x86)\\OpenSC Project\\OpenSC\\pkcs11\\opensc-pkcs11.dll",
    ],
}


def _glob_driver_candidates() -> List[str]:
    """Discover likely PKCS#11 drivers by globbing common install dirs.

    Indian DSC vendors install to non-standard paths; a targeted glob catches
    versioned filenames (e.g. libwdpkcs_Proxkey.1.0.0.dylib) the static list
    misses.
    """
    import glob

    if sys.platform == "darwin":
        roots = [
            "/usr/local/lib", "/usr/lib", "/opt/homebrew/lib",
            "/Library/WatchData", "/Library/WatchData/*",
            "/Library/SafeNet", "/Library/OpenSC/lib",
            "/usr/local/lib/*",
            # Some vendors (e.g. Longmai CryptoIDA) ship the PKCS#11 lib INSIDE
            # their .app bundle rather than a system lib dir.
            "/Applications/*.app/Contents/MacOS",
        ]
        patterns = ["*pkcs11*.dylib", "*pkcs11*.so", "*wdpkcs*.dylib",
                    "libcastle*.dylib", "*eps2003*", "*eToken*.dylib",
                    "libcryptoid*.dylib"]
    elif sys.platform.startswith("linux"):
        roots = ["/usr/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib/pkcs11",
                 "/usr/local/lib", "/opt/*"]
        patterns = ["*pkcs11*.so", "*wdpkcs*.so", "libcastle*.so", "*eps2003*"]
    elif sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        prog = os.environ.get("ProgramFiles", "C:\\Program Files")
        prog86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        roots = [
            os.path.join(windir, "System32"),
            os.path.join(windir, "SysWOW64"),
            os.path.join(prog, "*"), os.path.join(prog, "*", "*"),
            os.path.join(prog86, "*"), os.path.join(prog86, "*", "*"),
        ]
        # Match known vendor DLL name stems only — never a blanket *.dll scan of
        # System32 (too slow and would surface unrelated crypto libraries).
        patterns = [
            "*wdpkcs*.dll", "*eTPkcs11*.dll", "eps2003*.dll", "*ShuttleCsp11*.dll",
            "SignatureP11*.dll", "castle*.dll", "*idprime*.dll", "*opensc-pkcs11*.dll",
            "trustkey*.dll", "gclib*.dll",
            # Longmai mToken CryptoID — match the real name (CryptoIDA_pkcs11.dll)
            # and any relocated/renamed variant so a wrong static path can't break
            # detection.
            "*cryptoid*.dll", "*mtoken*.dll", "*_gm3000*.dll",
        ]
    else:
        return []

    found = []
    for root in roots:
        for pat in patterns:
            for p in glob.glob(os.path.join(root, pat)):
                if os.path.isfile(p) and p not in found:
                    found.append(p)
    return found


def _win32_registry_candidates() -> List[str]:
    """Read PKCS#11 module paths that vendors register in the Windows registry.

    Many Indian DSC middlewares install to versioned, non-standard folders that
    neither the static list nor a bounded glob will catch, but they record the
    driver DLL path under a vendor registry key. Best-effort; never raises.
    """
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except Exception:
        return []

    found: List[str] = []
    # (hive, subkey, value-name). value-name None => scan all string values.
    probes = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Feitian\ePass2003", None),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Feitian\ePass2003", None),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Watchdata\Proxkey", None),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Watchdata\Proxkey", None),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\SafeNet\Authentication\SAC", None),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Gemalto\IDGo 800\PKCS11", None),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Gemalto\IDGo 800\PKCS11", None),
    ]
    for hive, subkey, _vname in probes:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                i = 0
                while True:
                    try:
                        _n, val, _t = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if (isinstance(val, str) and val.lower().endswith(".dll")
                            and os.path.isfile(val) and val not in found):
                        found.append(val)
        except OSError:
            continue
        except Exception:
            continue
    return found


def list_driver_candidates() -> List[str]:
    """Return PKCS#11 driver paths that exist on this system (static + globbed)."""
    platform = sys.platform
    static = [p for p in _DRIVER_CANDIDATES.get(platform, []) if os.path.exists(p)]
    for p in _glob_driver_candidates():
        if p not in static:
            static.append(p)
    for p in _win32_registry_candidates():
        if p not in static:
            static.append(p)
    return static


# -- Provider ranking -------------------------------------------------------
# Real vendor DSC drivers are preferred over generic OpenSC; the OpenSC
# "pkcs11-spy" shim is a debug wrapper and must NEVER be used as a token driver.
_VENDOR_DRIVER_HINTS = (
    "wdpkcs", "proxkey", "watchdata",            # WatchData / ProxKey (Capricorn et al.)
    "etpkcs", "etoken", "safenet", "aks",        # SafeNet / eToken
    "eps2003", "castle", "epass", "feitian",     # Feitian ePass2003 / mToken
    "shuttle", "mtoken", "signaturep11",          # Feitian mToken family
    "cryptoid", "longmai", "gm3000",              # Longmai mToken CryptoID
    "aladdin", "trustkey", "moserbaer", "gclib",
    "idprime", "idemia",
)
_NEVER_SELECT = ("pkcs11-spy",)


def _driver_rank(path: str) -> int:
    """Lower rank = more preferred. Vendor drivers beat OpenSC; spy is excluded."""
    name = os.path.basename(path).lower()
    if any(bad in name for bad in _NEVER_SELECT):
        return 99
    if any(h in name for h in _VENDOR_DRIVER_HINTS):
        return 0
    if "opensc" in name:
        return 5
    return 3


def rank_driver_candidates(candidates: List[str]) -> List[str]:
    """Order candidates for token discovery: vendor drivers first, OpenSC next,
    arch-matched before mismatched (macOS); the pkcs11-spy shim is dropped.
    """
    if not candidates:
        return []
    py = python_arch() if sys.platform == "darwin" else None

    def arch_ok(c: str) -> bool:
        if sys.platform != "darwin":
            return True
        arches = macho_arches(c)
        return (not arches) or (py in arches)

    usable = [c for c in candidates if _driver_rank(c) < 99]
    return sorted(
        usable,
        key=lambda c: (_driver_rank(c), 0 if arch_ok(c) else 1, candidates.index(c)),
    )


# -- Self-healing driver locks ----------------------------------------------
# Some Indian DSC drivers (notably WatchData / ProxKey's libwdpkcs) implement a
# cross-process mutex during C_GetSlotList by creating a directory named
# "lockfinddevice" (sometimes suffixed with a PID, e.g. "lockfinddevice_657")
# in the driver's install folder, then removing it when the call returns.
#
# If a host process is killed mid-call, or the token is yanked/swapped while a
# call is in flight, that lock directory is left behind. Every later call then
# spin-sleeps for seconds waiting on the orphaned lock -> the token appears
# "stuck" until the user manually runs `rmdir .../lockfinddevice*`.
#
# A legitimate lock lives for only a fraction of a second, so we treat any lock
# older than _LOCK_STALE_SECONDS as orphaned and remove it. A PID-suffixed lock
# whose owning process is already dead is removed immediately. This is
# best-effort and never raises: at worst the driver simply recreates the lock.

_LOCK_DIR_PREFIX = "lockfinddevice"
_LOCK_STALE_SECONDS = 3.0


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID currently exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def cleanup_stale_driver_locks(driver_path: str, max_age: float = _LOCK_STALE_SECONDS) -> List[str]:
    """Remove orphaned ``lockfinddevice*`` dirs left in the driver's folder.

    Only removes a lock when it is safe to do so:
      * its name encodes a PID that is no longer running, OR
      * it is older than ``max_age`` seconds (a real lock is sub-second).
    An actively-held lock from a concurrent operation is left untouched.
    Best-effort: all errors are swallowed. Returns the paths removed.
    """
    removed: List[str] = []
    try:
        folder = os.path.dirname(driver_path)
        if not folder or not os.path.isdir(folder):
            return removed
        now = time.time()
        for name in os.listdir(folder):
            if not name.startswith(_LOCK_DIR_PREFIX):
                continue
            path = os.path.join(folder, name)
            if not os.path.isdir(path):
                continue

            stale = False
            # PID-suffixed lock (e.g. "lockfinddevice_657") whose owner is dead.
            suffix = name[len(_LOCK_DIR_PREFIX):].lstrip("_")
            if suffix.isdigit() and not _pid_alive(int(suffix)):
                stale = True
            if not stale:
                try:
                    if (now - os.path.getmtime(path)) >= max_age:
                        stale = True
                except OSError:
                    continue
            if not stale:
                continue  # possibly an active lock — leave it alone

            try:
                os.rmdir(path)  # succeeds only if empty (the normal case)
                removed.append(path)
            except OSError:
                # Non-empty or odd perms — fall back to a guarded recursive rm.
                shutil.rmtree(path, ignore_errors=True)
                if not os.path.exists(path):
                    removed.append(path)
    except Exception:
        pass
    return removed


# -- Architecture detection (Apple Silicon vs Intel mismatch) ---------------

def python_arch() -> str:
    """Return the running interpreter's CPU architecture (e.g. 'arm64','x86_64')."""
    import platform as _plat

    return _plat.machine()


def macho_arches(path: str) -> List[str]:
    """Return the CPU architectures contained in a Mach-O dylib (macOS).

    Reads the Mach-O / fat-binary header directly (no external tools). Returns
    an empty list when the file cannot be parsed or is not Mach-O.
    """
    import struct as _struct

    _CPU = {0x01000007: "x86_64", 0x0100000C: "arm64",
            0x00000007: "i386", 0x0000000C: "arm"}
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            if len(head) < 4:
                return []
            magic = _struct.unpack(">I", head)[0]
            # FAT binary (big-endian): 0xCAFEBABE
            if magic in (0xCAFEBABE, 0xCAFEBABF):
                nfat = _struct.unpack(">I", f.read(4))[0]
                arches = []
                for _ in range(nfat):
                    cputype = _struct.unpack(">I", f.read(4))[0]
                    f.read(16 if magic == 0xCAFEBABE else 28)  # skip rest of arch entry
                    arches.append(_CPU.get(cputype, hex(cputype)))
                return arches
            # Thin Mach-O: 0xFEEDFACE (32) / 0xFEEDFACF (64), LE or BE
            f.seek(0)
            raw = f.read(8)
            for endian in ("<", ">"):
                m, cputype = _struct.unpack(endian + "II", raw)
                if m in (0xFEEDFACE, 0xFEEDFACF):
                    return [_CPU.get(cputype, hex(cputype))]
        return []
    except Exception:
        return []


# -- DigestInfo prefix for SHA-256 (RFC 3447 9.2) ---------------------------
# When using CKM_RSA_PKCS, the input must be a DER-encoded DigestInfo.
# For SHA-256 this is a fixed 19-byte prefix + 32-byte hash.

_SHA256_DIGESTINFO_PREFIX = bytes([
    0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86,
    0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01, 0x05,
    0x00, 0x04, 0x20,
])


def _wrap_digest_info(hash_bytes: bytes, algorithm: str) -> bytes:
    """Wrap a pre-computed hash in a DigestInfo DER structure for CKM_RSA_PKCS."""
    algo = algorithm.upper()
    if algo == "SHA256" or algo == "SHA-256":
        if len(hash_bytes) != 32:
            raise Pkcs11Error(f"SHA-256 hash must be 32 bytes, got {len(hash_bytes)}", "BAD_HASH")
        return _SHA256_DIGESTINFO_PREFIX + hash_bytes
    elif algo == "SHA384" or algo == "SHA-384":
        prefix = bytes([
            0x30, 0x41, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86,
            0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x02, 0x05,
            0x00, 0x04, 0x30,
        ])
        if len(hash_bytes) != 48:
            raise Pkcs11Error(f"SHA-384 hash must be 48 bytes, got {len(hash_bytes)}", "BAD_HASH")
        return prefix + hash_bytes
    elif algo == "SHA512" or algo == "SHA-512":
        prefix = bytes([
            0x30, 0x51, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86,
            0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x03, 0x05,
            0x00, 0x04, 0x40,
        ])
        if len(hash_bytes) != 64:
            raise Pkcs11Error(f"SHA-512 hash must be 64 bytes, got {len(hash_bytes)}", "BAD_HASH")
        return prefix + hash_bytes
    else:
        raise Pkcs11Error(f"Unsupported hash algorithm: {algorithm}", "BAD_ALGO")


# -- Certificate parsing helpers --------------------------------------------

def _parse_cert_der(cert_der: bytes) -> dict:
    """Extract human-readable metadata from a DER certificate."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    cert = x509.load_der_x509_certificate(cert_der)

    def _name_attr(name, oid):
        vals = name.get_attributes_for_oid(oid)
        return vals[0].value if vals else None

    subject_cn = _name_attr(cert.subject, NameOID.COMMON_NAME)
    issuer_cn = _name_attr(cert.issuer, NameOID.COMMON_NAME)

    try:
        not_before = cert.not_valid_before_utc.isoformat()
        not_after = cert.not_valid_after_utc.isoformat()
    except AttributeError:
        not_before = cert.not_valid_before.isoformat() if cert.not_valid_before else None
        not_after = cert.not_valid_after.isoformat() if cert.not_valid_after else None

    # CA vs end-entity: basicConstraints CA:TRUE marks an issuer (chain) cert.
    # Requires no PIN — read straight from the public certificate DER.
    is_ca = False
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        is_ca = bool(bc.ca)
    except x509.ExtensionNotFound:
        is_ca = False
    except Exception:
        is_ca = False

    return dict(subject_cn=subject_cn, issuer_cn=issuer_cn,
                serial_hex=format(cert.serial_number, "x"),
                not_before=not_before, not_after=not_after, is_ca=is_ca)


# -- Signer -----------------------------------------------------------------

class Pkcs11Signer:
    """Wraps PKCS#11 operations: list certs and sign hashes."""

    def __init__(self, driver_paths):
        """Accept a single driver path or a list of candidate drivers.

        When given several candidates we probe them at operation time and use
        the first library that actually reports a token present — so a machine
        with, say, the WatchData driver (Capricorn) AND the Feitian driver
        (mToken) installed works with whichever token is plugged in.
        """
        if pkcs11 is None:
            raise Pkcs11Error(
                "python-pkcs11 is not installed. Run: pip install python-pkcs11",
                "NO_MODULE",
            )
        if isinstance(driver_paths, str):
            driver_paths = [driver_paths]
        cleaned = [p for p in (driver_paths or []) if p]
        # Rank so vendor drivers are tried before OpenSC / arch-mismatched ones.
        self.driver_paths = rank_driver_candidates(cleaned) or cleaned
        self._lib_cache = {}       # path -> loaded pkcs11 lib
        self._active_lib = None    # library that last had a token present
        self._active_driver = None

    @property
    def driver_path(self):
        """Back-compat: the resolved active driver, else the first candidate."""
        return self._active_driver or (self.driver_paths[0] if self.driver_paths else None)

    def _arch_mismatch_hint_for(self, path) -> Optional[str]:
        """Actionable message if a driver's arch can't be loaded by this Python
        interpreter (the #1 macOS failure: x86_64-only DSC driver on arm64)."""
        if sys.platform != "darwin" or not path:
            return None
        drv = macho_arches(path)
        py = python_arch()
        if drv and py not in drv:
            return (
                f"Architecture mismatch: driver {os.path.basename(path)} is "
                f"{'/'.join(drv)} but the bridge runs under {py} Python. Indian DSC "
                f"drivers are often Intel-only. Run the host under a {drv[0]} Python "
                f"(e.g. `arch -x86_64 /usr/bin/python3`) or install an {py} build of "
                f"the token driver."
            )
        return None

    def _stage_bundled_driver(self, path):
        """Copy a driver that lives inside a macOS ``.app`` bundle to a plain
        cache dir and return the copy's path.

        macOS ties a bundled dylib's code-signature validity to its host app, so
        ``dlopen`` from another process is blocked ("code signature not valid for
        use in process: library load disallowed by system policy"). A byte copy
        OUTSIDE the bundle keeps the (valid) ad-hoc signature but drops the bundle
        binding, so it loads. Only used on macOS for in-bundle paths.
        """
        if sys.platform != "darwin" or ".app/" not in (path + "/"):
            return path
        cache_dir = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support",
            "Giddh", "drivers",
        )
        try:
            os.makedirs(cache_dir, exist_ok=True)
            dest = os.path.join(cache_dir, os.path.basename(path))
            # Re-copy if missing or the source changed (driver upgrade).
            if (not os.path.exists(dest)
                    or os.path.getmtime(path) > os.path.getmtime(dest)
                    or os.path.getsize(path) != os.path.getsize(dest)):
                shutil.copy2(path, dest)  # copy2 preserves the embedded signature
            _log(f"  staged bundled driver {os.path.basename(path)} -> {dest}")
            return dest
        except Exception as e:
            _log(f"  staging failed for {os.path.basename(path)}: {e}")
            return path

    def _load_lib(self, path):
        """Load (and cache) one PKCS#11 library. Raises Pkcs11Error on failure."""
        if path in self._lib_cache:
            return self._lib_cache[path]
        if not os.path.exists(path):
            raise Pkcs11Error(f"PKCS#11 driver not found: {path}", "NO_DRIVER")
        # A driver inside a .app bundle cannot be dlopen'd by another process on
        # macOS — load it from a staged copy outside the bundle instead.
        load_path = self._stage_bundled_driver(path)
        # Clear any orphaned driver lock left by a previous crash or token swap.
        cleanup_stale_driver_locks(load_path)
        try:
            lib = pkcs11.lib(load_path)
        except Exception as e:
            hint = self._arch_mismatch_hint_for(load_path)
            if hint:
                raise Pkcs11Error(hint, "ARCH_MISMATCH")
            raise Pkcs11Error(
                f"Failed to load PKCS#11 driver '{path}': {e}", "DRIVER_LOAD_FAILED"
            )
        self._lib_cache[path] = lib
        return lib

    def _resolve_token(self):
        """Find the first installed provider that has a token present.

        Emits stage-by-stage logs so support can distinguish:
          NO_DRIVER (nothing installed) / LIBRARY_LOAD_FAILED /
          LIBRARY_LOADED_NO_SLOTS / READER_PRESENT_NO_TOKEN / TOKEN_FOUND.
        Returns (lib, token). Raises Pkcs11Error on failure.
        """
        # Reuse a previously-resolved provider if its token is still present.
        if self._active_lib is not None:
            try:
                slots = self._active_lib.get_slots(token_present=True)
                if slots:
                    return self._active_lib, slots[0].get_token()
            except Exception:
                pass
            self._active_lib = self._active_driver = None

        if not self.driver_paths:
            raise Pkcs11Error(
                "No PKCS#11 driver found. Install your DSC token's driver and restart.",
                "NO_DRIVER",
            )

        _log(f"Resolving token across {len(self.driver_paths)} provider(s): "
             f"{[os.path.basename(p) for p in self.driver_paths]}")

        saw_reader = False
        errors = []
        for path in self.driver_paths:
            base = os.path.basename(path)
            try:
                lib = self._load_lib(path)
            except Pkcs11Error as e:
                _log(f"  [{base}] LIBRARY_LOAD_FAILED ({e.code}): {e}")
                errors.append(f"{base}:{e.code}")
                continue
            try:
                all_slots = lib.get_slots(token_present=False)
            except Exception as e:
                _log(f"  [{base}] SLOT_ENUM_FAILED: {e}")
                errors.append(f"{base}:SLOT_ENUM_FAILED")
                continue
            if not all_slots:
                _log(f"  [{base}] LIBRARY_LOADED_NO_SLOTS (driver exposes no readers)")
                continue
            try:
                tok_slots = lib.get_slots(token_present=True)
            except Exception as e:
                _log(f"  [{base}] SLOT_ENUM_FAILED (token_present): {e}")
                errors.append(f"{base}:SLOT_ENUM_FAILED")
                continue
            if not tok_slots:
                saw_reader = True
                _log(f"  [{base}] READER_PRESENT_NO_TOKEN "
                     f"({len(all_slots)} reader slot(s), no token inserted)")
                continue
            # Success — this provider owns the plugged-in token.
            self._active_lib = lib
            self._active_driver = path
            try:
                label = str(getattr(tok_slots[0].get_token(), "label", "")).strip()
            except Exception:
                label = ""
            _log(f"  [{base}] TOKEN_FOUND -> selected provider "
                 f"(label={label!r}, {len(tok_slots)} token slot(s))")
            return lib, tok_slots[0].get_token()

        # No provider had a token present.
        if saw_reader:
            raise Pkcs11Error(
                "A card reader was detected but no DSC token is inserted. "
                "Plug the token in fully and try again.",
                "NO_TOKEN",
            )
        raise Pkcs11Error(
            "No token found. Ensure your DSC token is plugged in and its driver "
            "is installed."
            + (f" (driver issues: {'; '.join(errors)})" if errors else ""),
            "NO_TOKEN",
        )

    def diagnose(self) -> dict:
        """Per-provider diagnostics so support can see WHICH driver has the token.

        Never raises — every field is best-effort so this always returns.
        """
        candidates = self.driver_paths or list_driver_candidates()
        info = {
            "platform": sys.platform,
            "python_arch": python_arch(),
            "python_version": sys.version.split()[0],
            "candidates": list_driver_candidates(),
            "ranked": candidates,
            "active_driver": self._active_driver,
            "providers": [],
            # Back-compat top-level summary (filled from the best provider below).
            "driver_path": None,
            "driver_loads": False,
            "arch_mismatch": None,
            "slots": [],
            "tokens": [],
            "error": None,
        }
        for path in candidates:
            entry = {
                "driver_path": path,
                "driver_exists": os.path.exists(path),
                "driver_arches": macho_arches(path) if sys.platform == "darwin" else [],
                "arch_mismatch": self._arch_mismatch_hint_for(path),
                "driver_loads": False,
                "slots": [],
                "tokens": [],
                "error": None,
            }
            try:
                lib = self._load_lib(path)
                entry["driver_loads"] = True
                slots = lib.get_slots(token_present=False)
                entry["slots"] = [str(getattr(s, "slot_description", s)) for s in slots]
                for s in slots:
                    try:
                        tok = s.get_token()
                        entry["tokens"].append(str(getattr(tok, "label", "")).strip())
                    except Exception:
                        pass
            except Pkcs11Error as e:
                entry["error"] = f"{e.code}: {e}"
            except Exception as e:
                entry["error"] = str(e)
            info["providers"].append(entry)

        # Choose a summary provider: one with a token > one that loads > first.
        best = next((p for p in info["providers"] if p["tokens"]), None) \
            or next((p for p in info["providers"] if p["driver_loads"]), None) \
            or (info["providers"][0] if info["providers"] else None)
        if best:
            info["driver_path"] = best["driver_path"]
            info["driver_loads"] = best["driver_loads"]
            info["slots"] = best["slots"]
            info["tokens"] = best["tokens"]
            info["arch_mismatch"] = best["arch_mismatch"]
        if not candidates:
            info["error"] = "No PKCS#11 driver found."
        return info

    def _get_token(self):
        """Return the token from the first provider that has one present."""
        _lib, token = self._resolve_token()
        return token

    def list_certificates(self) -> List[CertInfo]:
        """List all certificates on the token (no PIN needed on most drivers)."""
        token = self._get_token()
        try:
            session = token.open()
        except Exception as e:
            raise Pkcs11Error(f"Failed to open PKCS#11 session: {e}", "SESSION_FAILED")

        try:
            # Some tokens require login to read certs. Try without first.
            try:
                certs = list(session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}))
            except Exception:
                # Retry after login with empty PIN (some drivers allow this)
                try:
                    session.login("")
                    certs = list(session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}))
                except Exception:
                    _log("CERT_READ_REQUIRES_LOGIN: token needs a PIN to list certificates")
                    raise Pkcs11Error(
                        "Cannot read certificates without login. Please provide your token PIN.",
                        "PIN_REQUIRED",
                    )

            signing: List[CertInfo] = []
            ca_b64: List[str] = []
            for cert_obj in certs:
                cert_der = bytes(cert_obj[Attribute.VALUE])
                cert_id = bytes(cert_obj[Attribute.ID])
                cert_id_hex = cert_id.hex()

                meta = _parse_cert_der(cert_der)
                b64 = base64.b64encode(cert_der).decode()
                if meta.get("is_ca"):
                    # Issuer/chain cert — kept for CMS embedding, not selectable.
                    ca_b64.append(b64)
                    continue
                signing.append(CertInfo(
                    cert_id_hex=cert_id_hex,
                    cert_b64=b64,
                    subject_cn=meta["subject_cn"],
                    issuer_cn=meta["issuer_cn"],
                    serial_hex=meta["serial_hex"],
                    not_before=meta["not_before"],
                    not_after=meta["not_after"],
                    is_ca=False,
                ))

            # Attach the CA chain to every signing cert so the caller can embed
            # the full trust path (leaf -> intermediates -> root) in the PAdES CMS.
            for c in signing:
                c.chain_b64 = list(ca_b64)

            if not signing and ca_b64:
                # Only CA certs found (unusual). Don't hide everything from the
                # user — surface them so the UI isn't empty, but mark as CA.
                _log("TOKEN_HAS_ONLY_CA_CERTS: no end-entity signing cert found")
            if not signing and not ca_b64:
                _log("TOKEN_PRESENT_NO_CERTS: session opened but no certificate objects found")
            else:
                _log(f"CERTS_READ_OK: {len(signing)} signing cert(s), "
                     f"{len(ca_b64)} CA/chain cert(s) on token")
            return signing
        finally:
            try:
                session.close()
            except Exception:
                pass

    def sign_hash(self, hash_b64: str, algorithm: str, cert_id_hex: str, pin: str) -> str:
        """Sign a pre-computed hash with the token's private key.

        Returns the raw signature as base64.
        """
        try:
            hash_bytes = base64.b64decode(hash_b64)
        except Exception:
            raise Pkcs11Error("Invalid base64 hash", "BAD_HASH")

        digest_info = _wrap_digest_info(hash_bytes, algorithm)

        token = self._get_token()
        try:
            session = token.open(user_pin=pin)
        except PinIncorrect:
            _log("SIGN_PIN_INCORRECT")
            raise Pkcs11Error("Incorrect PIN. Please try again.", "PIN_INCORRECT")
        except PinLocked:
            _log("SIGN_PIN_LOCKED")
            raise Pkcs11Error("Token is locked after too many incorrect PIN attempts. Please contact your DSC provider.", "PIN_LOCKED")
        except Exception as e:
            raise Pkcs11Error(f"Failed to open session: {e}", "SESSION_FAILED")

        try:
            # Find the private key by CKA_ID (matches the certificate's CKA_ID)
            cert_id_bytes = bytes.fromhex(cert_id_hex) if cert_id_hex else None
            if not cert_id_bytes:
                raise Pkcs11Error("Certificate ID is required to select the signing key.", "NO_CERT_ID")

            priv_keys = list(session.get_objects({
                Attribute.CLASS: ObjectClass.PRIVATE_KEY,
                Attribute.ID: cert_id_bytes,
            }))
            if not priv_keys:
                _log(f"SIGN_NO_PRIVATE_KEY for cert id {cert_id_hex}")
                raise Pkcs11Error(
                    f"No private key found for certificate ID {cert_id_hex}. The key may not be accessible.",
                    "NO_PRIVATE_KEY",
                )

            priv_key = priv_keys[0]

            # Sign using CKM_RSA_PKCS (PKCS#1 v1.5 with DigestInfo wrapping)
            signature = priv_key.sign(digest_info, mechanism=Mechanism.RSA_PKCS)
            _log(f"SIGN_OK: signed {algorithm} hash with key id {cert_id_hex}")
            return base64.b64encode(bytes(signature)).decode()

        except Pkcs11Error:
            raise
        except Exception as e:
            _log(f"SIGN_FAILED: {e}")
            raise Pkcs11Error(f"Signing operation failed: {e}", "SIGN_FAILED")
        finally:
            try:
                session.close()
            except Exception:
                pass
