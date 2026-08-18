#!/usr/bin/env python3
"""
Giddh DSC Bridge — Status / Companion App
=========================================
A small visible desktop app so users can SEE that the (otherwise headless)
native-messaging bridge is installed and working, check whether their DSC token
is detected, view the version, and uninstall.

It reuses ``pkcs11_signer`` to probe the token directly — it does NOT talk to the
running native host, so it works even if a browser isn't open. Token probing
runs on a worker thread to keep the UI responsive.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

import tkinter as tk
from tkinter import messagebox, ttk

HOST_NAME = "com.giddh.dsc.bridge"

try:  # version is baked in at build time; falls back to "dev" when run from source
    from _buildinfo import VERSION  # type: ignore
except Exception:
    VERSION = "dev"


# ── Install-location helpers ────────────────────────────────────────────────
def _host_binary_path() -> str:
    if sys.platform == "darwin":
        return "/usr/local/giddh-dsc-bridge/giddh-dsc-host"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, "Giddh DSC Bridge", "giddh-dsc-host.exe")
    return "/opt/giddh-dsc-bridge/giddh-dsc-host"


def _manifest_paths() -> list:
    """Candidate native-messaging manifest locations for Chromium browsers."""
    name = f"{HOST_NAME}.json"
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        roots = [
            os.path.join(home, "Library", "Application Support", "Google", "Chrome"),
            os.path.join(home, "Library", "Application Support", "Microsoft Edge"),
            os.path.join(home, "Library", "Application Support", "BraveSoftware", "Brave-Browser"),
            "/Library/Google/Chrome",
        ]
        return [os.path.join(r, "NativeMessagingHosts", name) for r in roots]
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", home)
        return [os.path.join(base, "Giddh DSC Bridge", name)]
    roots = [os.path.join(home, ".config", "google-chrome"),
             os.path.join(home, ".config", "chromium")]
    return [os.path.join(r, "NativeMessagingHosts", name) for r in roots]


def _host_installed() -> bool:
    return os.path.exists(_host_binary_path())


def _registration_ok() -> bool:
    return any(os.path.exists(p) for p in _manifest_paths())


# ── Token probing (worker thread) ───────────────────────────────────────────
def _probe_token() -> dict:
    """Return a dict describing token state. Never raises."""
    try:
        from pkcs11_signer import (
            list_driver_candidates, rank_driver_candidates, Pkcs11Signer,
        )
    except Exception as e:
        return {"ok": False, "msg": f"PKCS#11 module unavailable: {e}"}

    cands = list_driver_candidates()
    if not cands:
        return {"ok": False, "msg": "No PKCS#11 token driver found on this machine.\n"
                                    "Install your DSC token's vendor driver, then retry."}
    try:
        signer = Pkcs11Signer(cands)
        certs = signer.list_certificates()
    except Exception as e:
        code = getattr(e, "code", "")
        drivers = ", ".join(os.path.basename(c) for c in rank_driver_candidates(cands))
        hint = str(e)
        if code == "NO_TOKEN":
            hint = "Driver(s) found but no token is inserted. Plug in your DSC token."
        elif code == "PIN_REQUIRED":
            hint = "This token needs a PIN to read certificates."
        return {"ok": False, "msg": hint, "drivers": drivers}

    if not certs:
        return {"ok": False, "msg": "Token detected but no signing certificate found."}

    leaf = certs[0]
    d = leaf.to_dict() if hasattr(leaf, "to_dict") else leaf
    return {
        "ok": True,
        "driver": os.path.basename(getattr(signer, "driver_path", "") or ""),
        "subject": d.get("subjectCn"),
        "issuer": d.get("issuerCn"),
        "valid_to": d.get("notAfter"),
        "chain_len": len(d.get("chain", [])),
    }


# ── Uninstall ───────────────────────────────────────────────────────────────
def _osa_quote(s: str) -> str:
    """Escape a string for embedding inside an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _uninstall() -> str:
    """Remove the bridge. Returns 'ok' or 'cancelled'; raises on real failure."""
    if sys.platform == "darwin":
        # All paths are absolute and already expanded here, so no shell variable
        # expansion is needed — just double-quote each for the shell, then escape
        # the whole command for the AppleScript string.
        targets = [
            "/usr/local/giddh-dsc-bridge",
            os.path.expanduser("~/Library/Application Support/Giddh"),
            "/Applications/Giddh DSC Bridge.app",
        ] + _manifest_paths()
        cmd = " ; ".join('rm -rf "%s"' % t for t in targets)
        osa = 'do shell script "' + _osa_quote(cmd) + '" with administrator privileges'
        proc = subprocess.run(["osascript", "-e", osa],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return "ok"
        err = (proc.stderr or "").lower()
        # -128 / "User canceled" == the admin auth dialog was dismissed.
        if "-128" in err or "cancel" in err:
            return "cancelled"
        raise RuntimeError(proc.stderr.strip() or "osascript failed")
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        appdir = os.path.join(base, "Giddh DSC Bridge")
        for name in ("unins000.exe", "uninstall.exe"):
            u = os.path.join(appdir, name)
            if os.path.exists(u):
                subprocess.Popen([u])
                return "ok"
        raise RuntimeError("Uninstaller not found. Remove via Settings > Apps.")
    else:
        raise RuntimeError(
            "Remove via your package manager:\n"
            "    sudo apt remove giddh-dsc-bridge\n"
            "(or: sudo dpkg -r giddh-dsc-bridge)")


# ── GUI ─────────────────────────────────────────────────────────────────────
class StatusApp(tk.Tk):
    PAD = 12

    def __init__(self):
        super().__init__()
        self.title("Giddh DSC Bridge")
        self.resizable(False, False)
        self.configure(padx=self.PAD, pady=self.PAD)
        try:
            self.call("tk", "scaling", 1.3)
        except Exception:
            pass

        ttk.Label(self, text="Giddh DSC Bridge",
                  font=("Helvetica", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(self, text=f"Version {VERSION}",
                  foreground="#666").grid(row=1, column=0, sticky="w", pady=(0, 10))

        frame = ttk.LabelFrame(self, text="Status", padding=self.PAD)
        frame.grid(row=2, column=0, sticky="ew")
        self.lbl_host = ttk.Label(frame, text="Checking…")
        self.lbl_host.grid(row=0, column=0, sticky="w")
        self.lbl_reg = ttk.Label(frame, text="")
        self.lbl_reg.grid(row=1, column=0, sticky="w", pady=(4, 0))

        tokf = ttk.LabelFrame(self, text="DSC token", padding=self.PAD)
        tokf.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.lbl_token = ttk.Label(tokf, text="Click “Check token”.", justify="left",
                                   wraplength=360)
        self.lbl_token.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(tokf, mode="indeterminate", length=360)

        btns = ttk.Frame(self)
        btns.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        self.btn_check = ttk.Button(btns, text="Check token", command=self.on_check)
        self.btn_check.grid(row=0, column=0)
        ttk.Button(btns, text="Refresh", command=self.refresh_install).grid(row=0, column=1, padx=6)
        ttk.Button(btns, text="Uninstall…", command=self.on_uninstall).grid(row=0, column=2)
        ttk.Button(btns, text="Quit", command=self.destroy).grid(row=0, column=3, padx=(6, 0))

        self.refresh_install()

    def refresh_install(self):
        ok_host = _host_installed()
        ok_reg = _registration_ok()
        self.lbl_host.config(
            text=("✓  Native host installed" if ok_host else "✗  Native host NOT installed"),
            foreground=("#1a7f37" if ok_host else "#c1121f"))
        self.lbl_reg.config(
            text=("✓  Registered with your browser" if ok_reg
                  else "✗  Not registered — reinstall or reload the extension"),
            foreground=("#1a7f37" if ok_reg else "#c1121f"))

    def on_check(self):
        self.btn_check.config(state="disabled")
        self.lbl_token.config(text="Reading token…", foreground="#000")
        self.progress.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.progress.start(12)

        def work():
            res = _probe_token()
            self.after(0, lambda: self._show_token(res))

        threading.Thread(target=work, daemon=True).start()

    def _show_token(self, res: dict):
        self.progress.stop()
        self.progress.grid_forget()
        self.btn_check.config(state="normal")
        if res.get("ok"):
            txt = (f"✓  Token detected\n"
                   f"Signer: {res.get('subject') or '—'}\n"
                   f"Issuer: {res.get('issuer') or '—'}\n"
                   f"Valid until: {(res.get('valid_to') or '—')[:10]}\n"
                   f"Chain certs: {res.get('chain_len', 0)}\n"
                   f"Driver: {res.get('driver') or '—'}")
            self.lbl_token.config(text=txt, foreground="#1a7f37")
        else:
            extra = f"\nDrivers tried: {res['drivers']}" if res.get("drivers") else ""
            self.lbl_token.config(text="✗  " + res.get("msg", "Unknown error") + extra,
                                  foreground="#c1121f")

    def on_uninstall(self):
        if not messagebox.askyesno(
                "Uninstall Giddh DSC Bridge",
                "This removes the native host and browser registration.\n"
                "You may be asked for your password. Continue?"):
            return
        try:
            status = _uninstall()
            if status == "cancelled":
                messagebox.showwarning("Giddh DSC Bridge", "Uninstall was cancelled.")
                return
            msg = ("Uninstall started — the Windows uninstaller window will finish it. "
                   "This app will now close."
                   if sys.platform == "win32" else
                   "Uninstall complete. This app will now close.")
            messagebox.showinfo("Giddh DSC Bridge", msg)
            # The app has removed its own bundle; there is nothing left to show,
            # so quit. os._exit avoids touching the now-deleted bundle on teardown.
            self.destroy()
            os._exit(0)
        except Exception as e:
            messagebox.showerror("Giddh DSC Bridge", f"Uninstall failed: {e}")


def main():
    StatusApp().mainloop()


if __name__ == "__main__":
    main()
