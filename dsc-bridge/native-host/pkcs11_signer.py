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
import json
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
    # Which PKCS#11 driver this cert was actually read from (set only by
    # IsolatedSigner.list_certificates(), which may aggregate several
    # tokens). Exposed to callers as plain text — separate from the opaque
    # driver tag baked into cert_id_hex — so a UI can label "which token is
    # this really?" (e.g. by the certificate owner's name) instead of only
    # a generic vendor/model name that can't tell two same-model tokens
    # apart or reflect that a token's certificate was renewed for someone
    # else.
    driver_path: Optional[str] = None

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
            "driverPath": self.driver_path,
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


# -- Last-known-good driver persistence -------------------------------------
# When several vendor PKCS#11 libraries are installed at once (common on dev
# machines that have tested multiple DSC tokens), probing them all in a single
# process can corrupt a later library's view of the shared smartcard stack
# (observed on macOS: an unrelated vendor driver loaded earlier causes a
# perfectly healthy token to come back as CKR_TOKEN_NOT_RECOGNIZED from a
# driver that works fine standalone). Remembering which driver last actually
# found a token lets us try it FIRST, in isolation, on the next run — matching
# how a standalone vendor tool (which never loads a competing library) behaves.

def _state_dir() -> str:
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Giddh")
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Giddh")
    return os.path.join(os.path.expanduser("~"), ".config", "giddh")


_STATE_FILE = os.path.join(_state_dir(), "dsc_last_driver.txt")


def _load_last_driver() -> Optional[str]:
    """Best-effort read of the last driver path that found a token. Never raises."""
    try:
        with open(_STATE_FILE, "r") as f:
            path = f.read().strip()
        return path or None
    except Exception:
        return None


def _save_last_driver(path: str) -> None:
    """Best-effort persist of a driver path that just found a token. Never raises."""
    try:
        os.makedirs(_state_dir(), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            f.write(path)
    except Exception:
        pass


# -- User-managed PKCS#11 modules (Adobe-style "Attach Module") -------------
# Auto-detection cannot always win: a machine may have five vendor middlewares
# installed, a driver may sit in a non-standard location, or the user may simply
# know better than our heuristics. Mirroring Adobe Acrobat's "PKCS#11 Modules
# and Tokens" pane, the companion app lets the user attach a module explicitly
# and optionally pin it. That choice lives in the SAME config file the native
# host already reads, so the GUI and the host can never disagree.
#
# Schema (all keys optional, unknown keys preserved on save):
#   {
#     "pkcs11_driver":    "/path/lib.dylib",   # legacy: implies pin + strict
#     "modules":          ["/path/lib.dylib"], # user-attached, additive
#     "preferred_module": "/path/lib.dylib",   # tried first
#     "strict_module":    false                # use ONLY preferred_module
#   }

MODULE_SUFFIXES = (".dylib", ".so", ".dll")


def config_path() -> str:
    """Path of the shared bridge config file (host + companion app)."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Giddh",
                            "dsc-bridge.json")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", home), "Giddh",
                            "dsc-bridge.json")
    if sys.platform.startswith("linux"):
        return os.path.join(home, ".config", "giddh", "dsc-bridge.json")
    return os.path.join(home, ".giddh", "dsc-bridge.json")


def load_module_config() -> dict:
    """Read the bridge config. Never raises; returns {} when absent/corrupt."""
    try:
        with open(config_path(), "r") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_module_config(cfg: dict) -> None:
    """Persist the bridge config, writing atomically. Raises on real I/O failure.

    Unlike the best-effort state helpers above, this one propagates errors: the
    GUI must tell the user when their module choice could not be saved.
    """
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def attached_modules() -> List[str]:
    """User-attached module paths, in the order the user added them."""
    cfg = load_module_config()
    out: List[str] = []
    for p in (cfg.get("modules") or []):
        if isinstance(p, str) and p.strip() and p not in out:
            out.append(p.strip())
    legacy = cfg.get("pkcs11_driver")
    if isinstance(legacy, str) and legacy.strip() and legacy.strip() not in out:
        out.append(legacy.strip())
    return out


def resolve_driver_paths():
    """Return ``(paths, preferred, strict)`` for this machine.

    ``paths`` merges auto-detected drivers with user-attached modules (ranked);
    ``preferred`` is tried first; ``strict`` restricts operations to it alone.
    """
    cfg = load_module_config()
    legacy = (cfg.get("pkcs11_driver") or "").strip() \
        if isinstance(cfg.get("pkcs11_driver"), str) else ""
    preferred = (cfg.get("preferred_module") or "").strip() \
        if isinstance(cfg.get("preferred_module"), str) else ""
    strict = bool(cfg.get("strict_module"))

    # The legacy single-driver key always meant "use only this one".
    if legacy and not preferred:
        preferred, strict = legacy, True

    paths = list_driver_candidates()
    for p in attached_modules() + ([preferred] if preferred else []):
        if p and p not in paths:
            paths.append(p)

    ordered = rank_driver_candidates(paths) or paths
    # An explicitly attached module outranks our heuristics — including one the
    # ranker would normally drop (e.g. the pkcs11-spy debug shim).
    if preferred and preferred not in ordered:
        ordered.insert(0, preferred)
    return ordered, (preferred or None), strict


def attach_module(path: str) -> None:
    """Add a module path to the config (idempotent). Raises on bad input/IO."""
    path = (path or "").strip()
    if not path:
        raise Pkcs11Error("No module path given.", "BAD_REQUEST")
    if not os.path.isfile(path):
        raise Pkcs11Error(f"Module not found: {path}", "NO_DRIVER")
    if not path.lower().endswith(MODULE_SUFFIXES):
        raise Pkcs11Error(
            f"Not a PKCS#11 library: expected one of "
            f"{', '.join(MODULE_SUFFIXES)}.", "BAD_REQUEST")
    cfg = load_module_config()
    mods = [p for p in (cfg.get("modules") or []) if isinstance(p, str)]
    if path not in mods:
        mods.append(path)
    cfg["modules"] = mods
    save_module_config(cfg)


def detach_module(path: str) -> None:
    """Remove a module path from the config, clearing the pin if it was pinned."""
    cfg = load_module_config()
    cfg["modules"] = [p for p in (cfg.get("modules") or [])
                      if isinstance(p, str) and p != path]
    if cfg.get("preferred_module") == path:
        cfg["preferred_module"] = ""
        cfg["strict_module"] = False
    if cfg.get("pkcs11_driver") == path:
        cfg["pkcs11_driver"] = ""
    save_module_config(cfg)


def set_preferred_module(path: Optional[str], strict: Optional[bool] = None) -> None:
    """Pin (or unpin, with ``path=None``) the module tried first."""
    cfg = load_module_config()
    cfg["preferred_module"] = (path or "").strip()
    if strict is not None:
        cfg["strict_module"] = bool(strict)
    if not cfg["preferred_module"]:
        cfg["strict_module"] = False
    # Drop the legacy key so it can never contradict the new pin.
    if cfg.get("pkcs11_driver"):
        cfg["pkcs11_driver"] = ""
    save_module_config(cfg)


def set_strict_module(strict: bool) -> None:
    """Toggle 'use only the pinned module'."""
    cfg = load_module_config()
    cfg["strict_module"] = bool(strict) and bool(cfg.get("preferred_module"))
    save_module_config(cfg)


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

        NOTE: in production this class is driven by :class:`IsolatedSigner` with
        exactly ONE candidate driver per process, so the loop below normally
        runs a single iteration. Probing several drivers in one process is
        unsafe (see the process-isolation notes at the bottom of this module).
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
            # A slot can advertise CKF_TOKEN_PRESENT and still refuse
            # C_GetTokenInfo — that is what an exclusive lock held by other
            # middleware looks like (CKR_TOKEN_NOT_RECOGNIZED / not present).
            # Treat it as "reader there, token unusable" so the caller reports
            # the contention instead of leaking a raw driver exception.
            try:
                token = tok_slots[0].get_token()
            except Exception as e:
                saw_reader = True
                _log(f"  [{base}] READER_PRESENT_TOKEN_UNREADABLE: "
                     f"{type(e).__name__}: {e}")
                errors.append(f"{base}:{type(e).__name__}")
                continue
            # Success — this provider owns the plugged-in token.
            self._active_lib = lib
            self._active_driver = path
            label = str(getattr(token, "label", "")).strip()
            _log(f"  [{base}] TOKEN_FOUND -> selected provider "
                 f"(label={label!r}, {len(tok_slots)} token slot(s))")
            _save_last_driver(path)
            return lib, token

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


# ===========================================================================
# Process isolation: exactly ONE PKCS#11 library per OS process
# ===========================================================================
# PKCS#11 modules are not designed to coexist inside one process. Real-world
# breakage seen with Indian DSC middleware:
#   * OpenSC's two modules share a process-global context, so loading the
#     second one fails with "already initialized".
#   * Vendor drivers grab the shared smartcard/PC-SC stack during C_Initialize
#     and C_GetSlotList. Once one vendor library has claimed a reader, another
#     vendor's library may see the very same (healthy) token as
#     CKR_TOKEN_NOT_RECOGNIZED — reproduced with HyperSecu/Castle after
#     WatchData + Longmai drivers were probed first in the same process, while
#     the vendor's own tool and Adobe (each loading only their own module)
#     worked fine.
#
# The only correct fix is strict isolation: every PKCS#11 operation runs in a
# short-lived worker process that loads exactly one driver and then exits, so
# C_Finalize + process teardown always release the reader. This mirrors how
# standalone vendor tools behave and holds for any combination of tokens —
# one device, several devices swapped one by one, or several plugged in at once.

WORKER_FLAG = "--pkcs11-worker"

# Worker timeouts (seconds). Token hardware and vendor lock waits are slow;
# signing additionally waits on on-token RSA and possible PIN-pad prompts.
_PROBE_TIMEOUT = 25.0
_SIGN_TIMEOUT = 120.0

# Wall-clock budget for a whole request across every installed driver, so the
# caller always gets an answer instead of an indefinite spinner.
_LIST_BUDGET = 45.0
_SIGN_BUDGET = 180.0

# Exclusive token locks held by other middleware are usually short-lived, so a
# pass that finds a busy card is retried before reporting failure. Kept low:
# when the lock is held by a long-running app (Adobe, a token manager), extra
# passes only delay the "close that app" advice the user actually needs.
_BUSY_RETRIES = 2
_BUSY_RETRY_DELAY = 1.5


def _worker_argv() -> List[str]:
    """Command that re-launches this program in single-driver worker mode."""
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable is our own binary; it branches on the flag.
        return [sys.executable, WORKER_FLAG]
    return [sys.executable, os.path.abspath(__file__), WORKER_FLAG]


def _version_str(value) -> str:
    """Render a PKCS#11 version tuple/object as 'major.minor'. Never raises."""
    try:
        if value is None:
            return ""
        if isinstance(value, (tuple, list)):
            return ".".join(str(int(v)) for v in value[:2])
        major = getattr(value, "major", None)
        minor = getattr(value, "minor", None)
        if major is not None:
            return f"{int(major)}.{int(minor or 0)}"
        return str(value)
    except Exception:
        return ""


def _worker_probe(signer: "Pkcs11Signer", path: str) -> dict:
    """Slot/token census for one driver (used by probe + diagnose ops)."""
    out = {
        "driver_path": path,
        "driver_exists": os.path.exists(path),
        "driver_arches": macho_arches(path) if sys.platform == "darwin" else [],
        "arch_mismatch": signer._arch_mismatch_hint_for(path),
        "driver_loads": False,
        "manufacturer_id": "",
        "library_description": "",
        "library_version": "",
        "cryptoki_version": "",
        "slots": [],
        "tokens": [],
        "error": None,
    }
    lib = signer._load_lib(path)          # may raise Pkcs11Error
    out["driver_loads"] = True
    # Module identity, as shown in Adobe's "PKCS#11 Modules and Tokens" pane.
    # Purely informational, so a driver that omits any of it must not fail here.
    try:
        out["manufacturer_id"] = str(getattr(lib, "manufacturer_id", "") or "").strip()
        out["library_description"] = str(getattr(lib, "library_description", "") or "").strip()
        out["library_version"] = _version_str(getattr(lib, "library_version", None))
        out["cryptoki_version"] = _version_str(getattr(lib, "cryptoki_version", None))
    except Exception:
        pass
    slots = lib.get_slots(token_present=False)
    out["slots"] = [str(getattr(s, "slot_description", s)) for s in slots]
    for s in slots:
        try:
            out["tokens"].append(str(getattr(s.get_token(), "label", "")).strip())
        except Exception as e:
            # A slot that reports a card but whose token info is unreadable is
            # the single most useful diagnostic signal — surface it, don't hide it.
            out.setdefault("slot_errors", []).append(
                f"{getattr(s, 'slot_description', s)}: {type(e).__name__}: {e}"
            )
    return out


def _reader_names(signer: "Pkcs11Signer", path: str) -> List[str]:
    """Reader/slot names for a driver, ignoring token presence. Best-effort."""
    try:
        return [str(getattr(s, "slot_description", s))
                for s in signer._load_lib(path).get_slots(token_present=False)]
    except Exception:
        return []


# -- PC/SC ground truth ------------------------------------------------------
# A PKCS#11 module's own error codes are NOT reliable evidence about the card.
# Observed on a real machine with a Hypersecu HYP2003 inserted and nothing
# holding it: the vendor module reported CKR_TOKEN_NOT_PRESENT ("empty reader")
# and OpenSC reported CKR_TOKEN_NOT_RECOGNIZED, while PC/SC happily granted an
# EXCLUSIVE connection to the card. Guessing "another app locked it" from module
# errors alone therefore produces confidently wrong advice.
#
# So we ask the smartcard layer itself, one level below every PKCS#11 driver:
#   card absent            -> nothing is inserted
#   sharing violation      -> another process really does hold it exclusively
#   connect succeeds       -> the card is fine and free; the MODULE cannot drive
#                             it, so the user needs the right vendor module

_SCARD_SCOPE_SYSTEM = 2
_SCARD_SHARE_EXCLUSIVE = 1
_SCARD_PROTOCOL_ANY = 3
_SCARD_LEAVE_CARD = 0

_SCARD_S_SUCCESS = 0x00000000
_SCARD_E_SHARING_VIOLATION = 0x8010000B
_SCARD_E_NO_SMARTCARD = 0x8010000C
_SCARD_W_REMOVED_CARD = 0x80100069
_SCARD_W_UNPOWERED_CARD = 0x80100068
_SCARD_W_UNRESPONSIVE_CARD = 0x80100067

_NO_CARD_CODES = (_SCARD_E_NO_SMARTCARD, _SCARD_W_REMOVED_CARD)


def _pcsc_lib():
    """Load the platform smartcard library, or None when unavailable."""
    import ctypes

    try:
        if sys.platform == "darwin":
            return ctypes.CDLL("/System/Library/Frameworks/PCSC.framework/PCSC")
        if sys.platform == "win32":
            return ctypes.WinDLL("winscard.dll")
        return ctypes.CDLL("libpcsclite.so.1")
    except Exception:
        return None


def pcsc_reader_states() -> List[dict]:
    """Ground-truth reader/card census straight from PC/SC. Never raises.

    Each entry: ``{reader, card_present, exclusive_ok, locked, status}``.
    Returns ``[]`` when PC/SC is unavailable (then callers must not draw
    conclusions from its silence).
    """
    import ctypes

    pcsc = _pcsc_lib()
    if pcsc is None:
        return []

    out: List[dict] = []
    ctx = ctypes.c_void_p()
    try:
        if pcsc.SCardEstablishContext(_SCARD_SCOPE_SYSTEM, None, None,
                                      ctypes.byref(ctx)) != _SCARD_S_SUCCESS:
            return []
    except Exception:
        return []

    try:
        size = ctypes.c_uint32(0)
        pcsc.SCardListReaders(ctx, None, None, ctypes.byref(size))
        if not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if pcsc.SCardListReaders(ctx, None, buf,
                                 ctypes.byref(size)) != _SCARD_S_SUCCESS:
            return []

        for raw in buf.raw.split(b"\x00"):
            if not raw:
                continue
            reader = raw.decode("utf-8", "replace")
            card = ctypes.c_void_p()
            proto = ctypes.c_uint32(0)
            # Exclusive is the strictest ask: if it succeeds, nothing else holds
            # the card and any "it is locked" claim would be false.
            rv = pcsc.SCardConnect(ctx, raw, _SCARD_SHARE_EXCLUSIVE,
                                   _SCARD_PROTOCOL_ANY, ctypes.byref(card),
                                   ctypes.byref(proto)) & 0xFFFFFFFF
            if rv == _SCARD_S_SUCCESS:
                try:
                    pcsc.SCardDisconnect(card, _SCARD_LEAVE_CARD)
                except Exception:
                    pass
                entry = {"card_present": True, "exclusive_ok": True,
                         "locked": False, "status": "card present and free"}
            elif rv == _SCARD_E_SHARING_VIOLATION:
                entry = {"card_present": True, "exclusive_ok": False,
                         "locked": True, "status": "card locked by another application"}
            elif rv in _NO_CARD_CODES:
                entry = {"card_present": False, "exclusive_ok": False,
                         "locked": False, "status": "no card in reader"}
            elif rv in (_SCARD_W_UNPOWERED_CARD, _SCARD_W_UNRESPONSIVE_CARD):
                entry = {"card_present": True, "exclusive_ok": False,
                         "locked": False, "status": "card unresponsive — re-insert it"}
            else:
                entry = {"card_present": False, "exclusive_ok": False,
                         "locked": False, "status": f"PC/SC error 0x{rv:08X}"}
            entry["reader"] = reader
            out.append(entry)
    except Exception:
        return out
    finally:
        try:
            pcsc.SCardReleaseContext(ctx)
        except Exception:
            pass
    return out


def _card_evidence() -> dict:
    """Summarise PC/SC state into the facts the error messages need."""
    states = pcsc_reader_states()
    locked = [s["reader"] for s in states if s.get("locked")]
    free = [s["reader"] for s in states if s.get("card_present")
            and s.get("exclusive_ok")]
    unresponsive = [s["reader"] for s in states
                    if s.get("card_present") and not s.get("exclusive_ok")
                    and not s.get("locked")]
    return {"states": states, "locked": locked, "free": free,
            "unresponsive": unresponsive, "available": bool(states)}


_BUSY_HINT = (
    "Another application is holding it exclusively. DSC middleware "
    "(HyperPKI/EnterSafe Manager, ProxKey, epass2003 tools), Adobe Acrobat, or "
    "another browser will lock the token and block every other application. "
    "Close those applications (or re-insert the token) and try again."
)

_UNSUPPORTED_HINT = (
    "The card is present and not locked by any application, but no installed "
    "PKCS#11 module can read it — so the module for this token is missing or is "
    "the wrong one. Install your DSC vendor's macOS PKCS#11 library, then open "
    "the Giddh DSC Bridge app and use “Attach module…” to select it. Tip: if the "
    "token already works in Adobe Acrobat, open Acrobat’s Digital ID and Trusted "
    "Certificate Settings → PKCS#11 Modules and Tokens and attach the very same "
    "library path here."
)


def _classify_card_failure(readers: List[str]) -> Optional[tuple]:
    """Return ``(code, message)`` from PC/SC facts, or None when uninformative."""
    ev = _card_evidence()
    if not ev["available"]:
        return None
    if ev["locked"]:
        return ("TOKEN_BUSY_OR_ABSENT",
                f"Reader '{ev['locked'][0]}' holds a card that could not be "
                f"opened. {_BUSY_HINT}")
    if ev["unresponsive"]:
        return ("TOKEN_BUSY_OR_ABSENT",
                f"The card in reader '{ev['unresponsive'][0]}' did not respond. "
                f"Re-insert the token and try again.")
    if ev["free"]:
        tried = ", ".join(os.path.basename(r) for r in readers) if readers else ""
        return ("TOKEN_UNSUPPORTED_BY_MODULE",
                f"A card is inserted in reader '{ev['free'][0]}' but no PKCS#11 "
                f"module could open it. {_UNSUPPORTED_HINT}"
                + (f" (modules tried: {tried})" if tried else ""))
    return ("NO_TOKEN",
            "No card is inserted in any smartcard reader. Plug in your DSC "
            "token and try again.")


def worker_main(argv: Optional[List[str]] = None) -> int:
    """Single-driver worker: read one JSON request on stdin, reply on stdout, exit.

    Loading exactly one PKCS#11 library per process is the whole point of this
    entry point — never add a second driver load here.
    """
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        print(json.dumps({"ok": False, "code": "BAD_REQUEST", "error": str(e)}))
        return 0

    op = req.get("op", "")
    path = req.get("driver", "")
    signer: Optional[Pkcs11Signer] = None
    resp: dict
    try:
        if not path:
            raise Pkcs11Error("Worker requires a driver path", "BAD_REQUEST")
        signer = Pkcs11Signer([path])

        if op in ("probe", "diagnose_one"):
            entry = _worker_probe(signer, path)
            resp = {"ok": True, "provider": entry,
                    "has_token": bool(entry["tokens"]),
                    "has_reader": bool(entry["slots"])}
        elif op == "list_certs":
            certs = signer.list_certificates()
            resp = {"ok": True, "certs": [c.to_dict() for c in certs],
                    "driver": signer.driver_path}
        elif op == "sign":
            sig = signer.sign_hash(req.get("hash", ""), req.get("algorithm", "SHA256"),
                                   req.get("certId", ""), req.get("pin", ""))
            resp = {"ok": True, "signature": sig, "driver": signer.driver_path}
        else:
            resp = {"ok": False, "code": "BAD_REQUEST", "error": f"Unknown op: {op}"}
    except Pkcs11Error as e:
        # The worker reports only what its own driver saw. Deciding *why* the
        # card could not be read is the broker's job: that verdict needs PC/SC
        # ground truth plus the outcome of every other module, neither of which
        # a single-driver worker can see.
        resp = {"ok": False, "code": e.code, "error": str(e),
                "readers": _reader_names(signer, path) if signer is not None else []}
    except Exception as e:
        resp = {"ok": False, "code": "INTERNAL",
                "error": f"{type(e).__name__}: {e}" if str(e) else type(e).__name__}

    print(json.dumps(resp))
    return 0


def _encode_cert_id(driver_path: str, raw_id_hex: str) -> str:
    """Tag a raw PKCS#11 CKA_ID with which driver/token it came from.

    With several DSC tokens plugged in at once (a common setup — see
    list_certificates below), sign_hash needs to know which specific token a
    chosen certificate lives on without re-probing every driver again: that
    is slow, and risks grabbing a session on the wrong token if two of the
    user's own tokens happen to share a CKA_ID. The browser/companion app
    treats certId as an opaque string and echoes it back verbatim, so it is
    free to carry this extra routing info.
    """
    token = base64.urlsafe_b64encode(driver_path.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{token}.{raw_id_hex}"


def _decode_cert_id(cert_id_hex: str):
    """Reverse of _encode_cert_id. Returns (driver_path_or_None, raw_id_hex).

    Falls back gracefully to (None, cert_id_hex) for certIds that predate
    this encoding (plain hex, no driver tag) or that don't decode cleanly —
    callers then probe all drivers as before.
    """
    if cert_id_hex and "." in cert_id_hex:
        token, _, raw = cert_id_hex.partition(".")
        try:
            padded = token + "=" * (-len(token) % 4)
            driver_path = base64.urlsafe_b64decode(padded).decode("utf-8")
            if os.path.exists(driver_path):
                return driver_path, raw
        except Exception:
            pass
    return None, cert_id_hex


class IsolatedSigner:
    """Drop-in replacement for :class:`Pkcs11Signer` that never loads a PKCS#11
    library in this process — each operation runs in its own worker process.

    Public API (``list_certificates`` / ``sign_hash`` / ``diagnose`` /
    ``driver_path``) matches ``Pkcs11Signer`` so callers are unchanged.
    """

    def __init__(self, driver_paths, preferred=None, strict=False):
        if isinstance(driver_paths, str):
            driver_paths = [driver_paths]
        cleaned = [p for p in (driver_paths or []) if p]
        self.driver_paths = rank_driver_candidates(cleaned) or cleaned
        # A module the user attached/pinned in the companion app. Explicit user
        # intent always beats our ranking heuristics, so it is honoured even if
        # the ranker would have dropped or demoted it.
        self.preferred = preferred or None
        self.strict = bool(strict) and bool(self.preferred)
        if self.preferred and self.preferred not in self.driver_paths:
            self.driver_paths.insert(0, self.preferred)
        self._active_driver = None

    @classmethod
    def from_config(cls) -> "IsolatedSigner":
        """Build a signer from the shared config (auto-detected + attached)."""
        paths, preferred, strict = resolve_driver_paths()
        return cls(paths, preferred=preferred, strict=strict)

    @property
    def driver_path(self):
        return self._active_driver or (self.driver_paths[0] if self.driver_paths else None)

    # -- worker plumbing ---------------------------------------------------

    def _call_worker(self, payload: dict, timeout: float) -> dict:
        """Run one worker process for one driver. Never raises; returns a dict."""
        import subprocess

        base = os.path.basename(payload.get("driver", "?"))
        try:
            proc = subprocess.run(
                _worker_argv(),
                input=json.dumps(payload),
                stdout=subprocess.PIPE,
                stderr=None,          # inherit: worker logs land in our stderr
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            _log(f"  [{base}] WORKER_TIMEOUT after {timeout:.0f}s")
            return {"ok": False, "code": "DRIVER_TIMEOUT",
                    "error": f"Driver {base} did not respond within {timeout:.0f}s. "
                             f"Unplug and re-insert the token, then try again."}
        except Exception as e:
            return {"ok": False, "code": "WORKER_SPAWN_FAILED",
                    "error": f"Could not start PKCS#11 worker: {e}"}

        out = (proc.stdout or "").strip()
        if not out:
            # Worker died before replying — almost always a driver-level crash
            # (segfault in vendor middleware). Isolation is what keeps this from
            # taking the whole bridge down with it.
            _log(f"  [{base}] WORKER_CRASHED (exit={proc.returncode}, no reply)")
            return {"ok": False, "code": "DRIVER_CRASHED",
                    "error": f"Driver {base} crashed while being probed "
                             f"(exit code {proc.returncode})."}
        try:
            # Vendor drivers sometimes print to stdout; our reply is the last line.
            return json.loads(out.splitlines()[-1])
        except Exception as e:
            _log(f"  [{base}] WORKER_BAD_REPLY: {out[:200]!r}")
            return {"ok": False, "code": "WORKER_BAD_REPLY",
                    "error": f"Malformed worker reply from {base}: {e}"}

    def _ordered_candidates(self) -> List[str]:
        """Candidates to try: pinned module first, then last-known-good.

        Isolation already prevents cross-driver interference; ordering simply
        avoids needless probes of unrelated middleware (and the seconds each one
        costs). When the user pinned a module in strict mode we probe nothing
        else at all, so a wrong pin fails fast with a clear error instead of
        being silently papered over by another provider.
        """
        if self.strict:
            return [self.preferred]

        order = list(self.driver_paths)
        for first in (_load_last_driver(), self.preferred):
            if first and first in order:
                order.remove(first)
                order.insert(0, first)
        return order

    def _run_on_token(self, payload: dict, timeout: float, budget: float,
                      only_driver: Optional[str] = None) -> dict:
        """Try an op against each candidate until one owns a present token.

        An exclusive token lock held by other middleware is usually transient
        (the other app releases it between its own operations), so a full pass
        that finds only busy/absent tokens is retried a couple of times before
        giving up. Returns the successful worker reply; raises Pkcs11Error with
        the most specific diagnosis otherwise.

        ``only_driver`` restricts the probe to exactly one driver — used by
        sign_hash() once list_certificates() has already told us which
        specific token a chosen certificate lives on, so signing doesn't
        re-probe (and potentially grab a session on) every other plugged-in
        token first.
        """
        if not self.driver_paths and not only_driver:
            raise Pkcs11Error(
                "No PKCS#11 driver found. Install your DSC token's driver and restart.",
                "NO_DRIVER",
            )

        candidates = [only_driver] if only_driver else self._ordered_candidates()
        _log(f"{payload['op']}: probing {len(candidates)} provider(s) in isolated "
             f"workers: {[os.path.basename(p) for p in candidates]}")

        # Hard wall-clock budget for the whole operation. Without it, a machine
        # with several installed middlewares (each slow to initialise) could
        # keep the caller waiting for minutes with no answer at all — the UI
        # just sits on "Reading certificates…". Always answer, even if the
        # answer is a diagnosis.
        deadline = time.monotonic() + budget
        blocking: Optional[Pkcs11Error] = None
        errors: List[str] = []
        timed_out = False

        for attempt in range(1, _BUSY_RETRIES + 1):
            for path in candidates:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _log(f"  Budget of {budget:.0f}s exhausted; stopping probe.")
                    break
                base = os.path.basename(path)
                reply = self._call_worker({**payload, "driver": path},
                                          min(timeout, remaining))
                if reply.get("ok"):
                    self._active_driver = path
                    _save_last_driver(path)
                    _log(f"  [{base}] OK -> token served by this provider")
                    return reply

                code = reply.get("code", "INTERNAL")
                msg = reply.get("error", "")
                _log(f"  [{base}] {code}: {msg}")

                if code in ("NO_TOKEN", "NO_DRIVER", "DRIVER_LOAD_FAILED",
                            "ARCH_MISMATCH", "DRIVER_CRASHED", "WORKER_BAD_REPLY",
                            "WORKER_SPAWN_FAILED", "SLOT_ENUM_FAILED"):
                    # This provider simply isn't the one holding the token.
                    errors.append(f"{base}:{code}")
                    continue

                # Codes below mean the token WAS found by this provider; the
                # failure is real and actionable, so stop and report it rather
                # than masking it behind a later provider's "no token".
                if code in ("PIN_INCORRECT", "PIN_LOCKED", "PIN_REQUIRED",
                            "NO_PRIVATE_KEY", "NO_CERT_ID", "BAD_HASH", "BAD_ALGO"):
                    self._active_driver = path
                    _save_last_driver(path)
                    raise Pkcs11Error(msg, code)

                # Anything else (SESSION_FAILED, SIGN_FAILED, INTERNAL, TIMEOUT):
                # remember it as the best explanation, but let others try.
                blocking = blocking or Pkcs11Error(msg, code)
                errors.append(f"{base}:{code}")

            # Retrying only helps while another process still holds the card, and
            # PC/SC is the only trustworthy source for that. An empty reader — or
            # a card no module can drive — never improves by waiting.
            locked = bool(_card_evidence()["locked"])
            if (blocking or timed_out or not locked
                    or attempt == _BUSY_RETRIES
                    or deadline - time.monotonic() <= _BUSY_RETRY_DELAY):
                break
            _log(f"  Card locked by another process; retrying "
                 f"({attempt}/{_BUSY_RETRIES - 1}) in {_BUSY_RETRY_DELAY}s")
            time.sleep(_BUSY_RETRY_DELAY)

        if blocking:
            raise blocking
        if timed_out:
            raise Pkcs11Error(
                f"Timed out after {budget:.0f}s while checking "
                f"{len(candidates)} installed PKCS#11 driver(s). "
                f"Unplug and re-insert the token, then try again."
                + (f" (driver issues: {'; '.join(errors)})" if errors else ""),
                "TIMEOUT",
            )

        # No module could serve the token. Ask PC/SC what is actually true before
        # blaming anything: is a card even inserted, is it locked, or is it fine
        # and simply unsupported by every installed module?
        verdict = _classify_card_failure(candidates)
        if verdict:
            code, msg = verdict
            raise Pkcs11Error(
                msg + (f" Only the pinned module "
                       f"'{os.path.basename(self.preferred or '')}' was tried — open "
                       f"the Giddh DSC Bridge app to change or unpin it."
                       if self.strict else ""),
                code,
            )

        raise Pkcs11Error(
            "No token found. Ensure your DSC token is plugged in and its driver "
            "is installed."
            + (f" Only the pinned module "
               f"'{os.path.basename(self.preferred or '')}' was tried — open the "
               f"Giddh DSC Bridge app to change or unpin it." if self.strict else "")
            + (f" (driver issues: {'; '.join(errors)})" if errors else ""),
            "NO_TOKEN",
        )

    # -- public API --------------------------------------------------------

    def list_certificates(self, only_driver: Optional[str] = None) -> List[CertInfo]:
        """Aggregate certificates from every plugged-in token, not just the
        first driver that answers.

        The old behaviour stopped probing as soon as one driver returned a
        usable reply (see _run_on_token), so with several DSC tokens
        attached at once — a common setup — only the first-ranked token's
        certificate was ever offered, with no way to pick a different one.
        Each cert's certId is tagged with the driver it came from
        (_encode_cert_id) so sign_hash can route directly to the right token.

        ``only_driver`` lets a caller (the "Token" picker in the test page /
        Giddh's UI) restrict the read to exactly one already-known driver
        path instead of aggregating across all of them — useful once the
        user has told us which physical token they want, so we don't touch
        the other two tokens (and their locks) at all. It is validated
        against self.driver_paths (paths the host itself already detected)
        so a web page can never make the host dlopen an arbitrary library —
        the same code-execution concern that keeps listModules read-only.
        """
        if only_driver and only_driver not in self.driver_paths:
            only_driver = None
        if only_driver:
            candidates = [only_driver]
        elif self.strict:
            candidates = [self.preferred]
        else:
            candidates = self._ordered_candidates()
        deadline = time.monotonic() + _LIST_BUDGET
        certs: List[CertInfo] = []
        last_ok_driver: Optional[str] = None

        for path in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            reply = self._call_worker({"op": "list_certs", "driver": path},
                                      min(_PROBE_TIMEOUT, remaining))
            if not reply.get("ok") or not reply.get("certs"):
                continue
            last_ok_driver = path
            for d in reply["certs"]:
                certs.append(CertInfo(
                    cert_id_hex=_encode_cert_id(path, d.get("certId", "")),
                    cert_b64=d.get("certB64", ""),
                    subject_cn=d.get("subjectCn"),
                    issuer_cn=d.get("issuerCn"),
                    serial_hex=d.get("serial"),
                    not_before=d.get("notBefore"),
                    not_after=d.get("notAfter"),
                    is_ca=bool(d.get("isCa")),
                    chain_b64=list(d.get("chain") or []),
                    driver_path=path,
                ))

        if certs:
            self._active_driver = last_ok_driver
            _save_last_driver(last_ok_driver)
            return certs

        # Nothing on the selected driver(s) — fall back to the single-driver
        # probe purely for its rich, PC/SC-aware error diagnosis (locked vs
        # missing vs wrong module) and busy-retry behaviour, which callers
        # rely on for the message shown to the user.
        self._run_on_token({"op": "list_certs"}, _PROBE_TIMEOUT, _LIST_BUDGET,
                           only_driver=only_driver)
        return []

    def sign_hash(self, hash_b64: str, algorithm: str, cert_id_hex: str, pin: str) -> str:
        # certId may carry a driver tag added by list_certificates() so
        # signing goes straight to the token that actually has this
        # certificate, instead of re-probing (and possibly grabbing a
        # session on) every other plugged-in token first. Untagged/legacy
        # certIds fall back to probing all drivers as before.
        driver_path, raw_id = _decode_cert_id(cert_id_hex)
        # The PIN travels over the worker's stdin pipe only — never argv, so it
        # cannot leak into process listings.
        reply = self._run_on_token({
            "op": "sign", "hash": hash_b64, "algorithm": algorithm,
            "certId": raw_id, "pin": pin,
        }, _SIGN_TIMEOUT, _SIGN_BUDGET, only_driver=driver_path)
        return reply.get("signature", "")

    def _module_rows(self, paths: Optional[List[str]] = None) -> List[dict]:
        """Probe each module in its OWN worker and return one row per module.

        Shared by ``diagnose`` and ``list_modules`` so the companion app and the
        support report can never show different facts about the same module.
        """
        targets = paths if paths is not None else (self.driver_paths or list_driver_candidates())
        attached = attached_modules()
        rows: List[dict] = []
        for path in targets:
            reply = self._call_worker({"op": "diagnose_one", "driver": path},
                                      _PROBE_TIMEOUT)
            if reply.get("ok"):
                row = dict(reply["provider"])
            else:
                row = {
                    "driver_path": path,
                    "driver_exists": os.path.exists(path),
                    "driver_arches": macho_arches(path) if sys.platform == "darwin" else [],
                    "arch_mismatch": None,
                    "driver_loads": False,
                    "manufacturer_id": "",
                    "library_description": "",
                    "library_version": "",
                    "cryptoki_version": "",
                    "slots": [],
                    "tokens": [],
                    "error": f"{reply.get('code', 'INTERNAL')}: {reply.get('error', '')}",
                }
            row["attached"] = path in attached
            row["preferred"] = (path == self.preferred)
            rows.append(row)
        return rows

    def list_modules(self) -> dict:
        """Module inventory for the companion app's 'Attach Module' pane."""
        return {
            "configPath": config_path(),
            "preferred": self.preferred,
            "strict": self.strict,
            "detected": list_driver_candidates(),
            "attached": attached_modules(),
            "readers": pcsc_reader_states(),
            "modules": self._module_rows(),
        }

    def diagnose(self) -> dict:
        """Per-provider diagnostics, each gathered in its own worker process.

        Because every driver is probed in isolation, running diagnose can no
        longer poison a subsequent read/sign — and the report reflects what each
        driver sees on its own, which is what vendor tools see.
        """
        candidates = self.driver_paths or list_driver_candidates()
        info = {
            "platform": sys.platform,
            "python_arch": python_arch(),
            "python_version": sys.version.split()[0],
            "isolated_workers": True,
            "config_path": config_path(),
            "pcsc_readers": pcsc_reader_states(),
            "attached_modules": attached_modules(),
            "preferred_module": self.preferred,
            "strict_module": self.strict,
            "candidates": list_driver_candidates(),
            "ranked": candidates,
            "active_driver": self._active_driver,
            "providers": self._module_rows(candidates),
            "driver_path": None,
            "driver_loads": False,
            "arch_mismatch": None,
            "slots": [],
            "tokens": [],
            "error": None,
        }

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


if __name__ == "__main__":
    # Worker mode when run directly with the flag (development / non-frozen).
    if WORKER_FLAG in sys.argv:
        sys.exit(worker_main())
