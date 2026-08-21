#!/usr/bin/env python3
"""
Giddh DSC Bridge — status / companion application.

This is the small desktop window users see after installing the bridge. It is
not required for signing (the browser extension talks directly to the native
host), but it lets users check that a DSC token is detected and uninstall
cleanly. PKCS#11 module management (listing, attaching, picking a token) is
handled in the extension's test page / Giddh's own UI via listModules() and
getCertificate(), not here.

The window intentionally renders with a fixed light palette so text remains
visible even when macOS runs the bundled Tk 8.5 runtime in Dark Mode.

Implementation note: macOS Aqua Tk 8.5 silently ignores `ttk.Style.configure`
backgrounds for `ttk.Frame` containers — they keep the OS-managed dark window
background. So the entire visible chrome is built with `tk.Frame`/`tk.Label`
(which respect `bg=`/`fg=`), and only the buttons use `ttk` (the `clam`
theme styles them correctly).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


# -----------------------------------------------------------------------------
# Platform helpers
# -----------------------------------------------------------------------------

def is_mac() -> bool:
    return platform.system() == "Darwin"


def is_windows() -> bool:
    return platform.system() == "Windows"


def get_app_root() -> Path:
    """Return the repository/app root regardless of how we are executed."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_version() -> str:
    """Return the build-time version if available, else the repo VERSION file."""
    buildinfo = get_app_root() / "_buildinfo.py"
    if buildinfo.exists():
        try:
            ns = {}
            exec(compile(buildinfo.read_text(encoding="utf-8"), str(buildinfo), "exec"), ns)
            v = ns.get("VERSION")
            if v:
                return v
        except Exception:
            pass
    version_file = get_app_root() / "VERSION"
    if version_file.exists():
        try:
            return version_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "1.7.0"


VERSION = _resolve_version()
APP_NAME = "Giddh DSC Bridge"
APP_DESCRIPTION = "Connect your DSC token to Giddh for secure, client-side PDF signing."


def get_resource_root() -> Path:
    """Return the directory that holds packaged resources (icons etc.).

    Frozen: the PyInstaller spec copies `icons/app.icns` / `app.ico` next to
    the executable (macOS bundle also has Contents/Resources). On macOS we
    prefer `Contents/Resources` if it exists.
    Source:  the script lives in `dsc-bridge/native-host/`, so icons are
    two levels up (`<repo>/icons/`).
    """
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        if is_mac():
            bundle = root.parent.parent  # .../Giddh DSC Bridge.app/Contents/
            resources = bundle / "Resources"
            if resources.exists():
                return resources
        return root
    return Path(__file__).resolve().parent.parent.parent  # <repo>


def get_icon_path() -> str | None:
    """Best available icon for the current platform.

    When frozen, PyInstaller copies `icons/app.icns` and `icons/app.ico`
    next to the executable (per the pyinstaller spec), and the entire
    `icons/` folder is bundled as `icons/` resources. We resolve in that
    order, then fall back to the higher-resolution tray PNGs.
    """
    resources = get_resource_root()
    candidates = []
    if is_mac():
        candidates = [resources / "app.icns", resources / "icons" / "app.icns",
                      resources / "icons" / "tray@2x.png"]
    elif is_windows():
        candidates = [resources / "app.ico", resources / "icons" / "app.ico",
                      resources / "icons" / "tray.png"]
    else:
        candidates = [resources / "icons" / "app.icns", resources / "icons" / "app.ico",
                      resources / "icons" / "tray.png", resources / "icons" / "tray@2x.png"]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def get_tray_icon_path() -> str | None:
    resources = get_resource_root()
    candidates = [resources / "icons" / "tray.png", resources / "icons" / "tray-small.png"]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("giddh_dsc_status")


# -----------------------------------------------------------------------------
# Theme / palette
# -----------------------------------------------------------------------------

LIGHT_BG = "#ffffff"
LIGHT_FG = "#1d1d1f"
SECONDARY_FG = "#6b7280"
ACCENT_BG = "#f3f4f6"
PRIMARY_BLUE = "#2563eb"
PRIMARY_BLUE_HOVER = "#1d4ed8"


def _apply_palette(root: tk.Tk) -> None:
    """Force a light, readable palette on every widget.

    macOS Aqua Tk 8.5 only honors backgrounds on widgets that go through the
    ttk theme (clam), and `tk_setPalette` for the toplevel. To get every
    container/label/separator to draw a non-default background we therefore
    configure EVERY ttk style with explicit background/foreground and use
    ttk widgets throughout the UI (see _build_ui).
    """
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")

    # Base — wildcard so all unstyled widgets inherit
    style.configure(".",
                    background=LIGHT_BG,
                    foreground=LIGHT_FG,
                    fieldbackground=LIGHT_BG)

    # Containers
    style.configure("TFrame", background=LIGHT_BG)
    style.configure("Card.TFrame", background=ACCENT_BG)

    # Labels (ttk.Label is what Aqua renders reliably on macOS Tk 8.5)
    style.configure("TLabel",
                    background=LIGHT_BG, foreground=LIGHT_FG,
                    font=("Helvetica", 13))
    style.configure("Secondary.TLabel",
                    background=LIGHT_BG, foreground=SECONDARY_FG,
                    font=("Helvetica", 12))
    style.configure("Title.TLabel",
                    background=LIGHT_BG, foreground=LIGHT_FG,
                    font=("Helvetica", 22, "bold"))
    style.configure("Version.TLabel",
                    background=LIGHT_BG, foreground=SECONDARY_FG,
                    font=("Helvetica", 11))
    style.configure("Description.TLabel",
                    background=LIGHT_BG, foreground=SECONDARY_FG,
                    font=("Helvetica", 12))
    style.configure("CardTitle.TLabel",
                    background=ACCENT_BG, foreground=LIGHT_FG,
                    font=("Helvetica", 13))
    style.configure("CardSecondary.TLabel",
                    background=ACCENT_BG, foreground=SECONDARY_FG,
                    font=("Helvetica", 12))
    style.configure("Footer.TLabel",
                    background=LIGHT_BG, foreground=SECONDARY_FG,
                    font=("Helvetica", 11))

    # Buttons
    style.configure("TButton",
                    font=("Helvetica", 13), padding=6,
                    background=LIGHT_BG, foreground=LIGHT_FG)
    style.map("TButton",
              background=[("active", PRIMARY_BLUE_HOVER),
                          ("pressed", PRIMARY_BLUE_HOVER)],
              foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
    style.configure("Primary.TButton",
                    background=PRIMARY_BLUE, foreground="#ffffff")
    style.map("Primary.TButton",
              background=[("active", PRIMARY_BLUE_HOVER),
                          ("pressed", PRIMARY_BLUE_HOVER)],
              foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])

    # Force the toplevel window background to light.
    try:
        root.tk_setPalette(LIGHT_BG, LIGHT_FG, PRIMARY_BLUE_HOVER)
    except Exception:
        pass
    root.configure(background=LIGHT_BG, highlightthickness=0, borderwidth=0)

    # DEBUG: confirm visible text would render by querying font resolution
    try:
        font = style.lookup("Title.TLabel", "font")
        logger.info("Title.TLabel font resolves to: %s", font)
    except Exception:
        logger.exception("font lookup failed")


# -----------------------------------------------------------------------------
# Native-host status helpers
# -----------------------------------------------------------------------------

HOST_NAME = "com.giddh.dsc.bridge"
HOST_EXE = "giddh-dsc-host"
if is_windows():
    HOST_EXE += ".exe"


def _find_host_executable() -> Path | None:
    """Locate the native host binary next to this app.

    The host and the status app are packaged as separate components that
    don't always share an install root:
      - macOS: host -> /usr/local/giddh-dsc-bridge/giddh-dsc-host,
               status app -> /Applications/Giddh DSC Bridge.app (unrelated
               location, so the host path must be hardcoded).
      - Linux (.deb): both live under /opt/giddh-dsc-bridge, with the
               status app one level down in a "status" subfolder, so
               root.parent finds the host.
      - Windows (Inno Setup): both live under {app}, with the status app
               one level down in a "status" subfolder — same as Linux —
               but the default install dir is {localappdata}, not
               Program Files, so that must be checked too.
    """
    root = get_app_root()
    candidates = [
        root / HOST_EXE,
        root / "native-host" / HOST_EXE,
        root.parent / HOST_EXE,
        Path(f"/usr/local/bin/{HOST_EXE}"),
        Path.home() / ".giddh-dsc-bridge" / HOST_EXE,
    ]
    if is_mac():
        candidates.append(Path("/usr/local/giddh-dsc-bridge") / HOST_EXE)
    elif not is_windows():
        candidates.append(Path("/opt/giddh-dsc-bridge") / HOST_EXE)
    if is_windows():
        candidates.insert(0, root / f"{HOST_EXE}")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        candidates.append(Path(program_files) / "Giddh DSC Bridge" / HOST_EXE)
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "Giddh DSC Bridge" / HOST_EXE)
    for c in candidates:
        if c.exists():
            return c
    return None


def _registry_key_for_host() -> str:
    return r"SOFTWARE\Google\Chrome\NativeMessagingHosts\com.giddh.dsc.bridge"


def is_host_registered() -> bool:
    if is_windows():
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _registry_key_for_host()) as key:
                value, _ = winreg.QueryValueEx(key, None)
                return bool(value)
        except FileNotFoundError:
            return False
        except Exception:
            return False
    elif is_mac():
        paths = [
            Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.giddh.dsc.bridge.json",
            Path.home() / "Library/Application Support/Chromium/NativeMessagingHosts/com.giddh.dsc.bridge.json",
            Path.home() / "Library/Application Support/Microsoft Edge/NativeMessagingHosts/com.giddh.dsc.bridge.json",
            Path("/Library/Google/Chrome/NativeMessagingHosts/com.giddh.dsc.bridge.json"),
            Path("/Library/Application Support/Chromium/NativeMessagingHosts/com.giddh.dsc.bridge.json"),
            Path("/Library/Microsoft/Edge/NativeMessagingHosts/com.giddh.dsc.bridge.json"),
        ]
        return any(p.exists() for p in paths)
    else:
        paths = [
            Path.home() / ".config/google-chrome/NativeMessagingHosts/com.giddh.dsc.bridge.json",
            Path.home() / ".config/chromium/NativeMessagingHosts/com.giddh.dsc.bridge.json",
        ]
        return any(p.exists() for p in paths)


def is_host_installed() -> bool:
    """True if the native host executable is present on disk."""
    return _find_host_executable() is not None


def run_host_diagnose() -> dict:
    """Run a quick diagnostic via the native host."""
    host = _find_host_executable()
    if not host:
        return {"installed": False, "error": "Native host executable not found"}
    try:
        proc = subprocess.run(
            [str(host), "--diagnose"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0 and proc.stdout:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                return {"installed": True, "raw_output": proc.stdout.strip()}
        return {"installed": True, "error": proc.stderr.strip() or "diagnose failed"}
    except subprocess.TimeoutExpired:
        return {"installed": True, "error": "diagnose timed out"}
    except Exception as e:
        return {"installed": True, "error": str(e)}


# -----------------------------------------------------------------------------
# Main application window
# -----------------------------------------------------------------------------

class StatusApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=LIGHT_BG)
        self.root.minsize(560, 520)

        # Center on screen.
        self.root.update_idletasks()
        width, height = 620, 560
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self._build_menu()
        self._build_ui()

    # -------------------------------------------------------------------------
    # Menu bar (minimal — removes Tk's default "Widget Demonstration" items)
    # -------------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root, background=LIGHT_BG, fg=LIGHT_FG,
                          activebackground=PRIMARY_BLUE, activeforeground="#ffffff")
        self.root.config(menu=menubar)

        if is_mac():
            app_menu = tk.Menu(menubar, name="apple", tearoff=0,
                               background=LIGHT_BG, fg=LIGHT_FG,
                               activebackground=PRIMARY_BLUE, activeforeground="#ffffff")
            menubar.add_cascade(label=APP_NAME, menu=app_menu)
            app_menu.add_command(label=f"About {APP_NAME}", command=self._show_about)
            app_menu.add_separator()
            app_menu.add_command(label=f"Quit {APP_NAME}", command=self.root.quit, accelerator="Cmd+Q")

            window_menu = tk.Menu(menubar, name="window", tearoff=0,
                                  background=LIGHT_BG, fg=LIGHT_FG,
                                  activebackground=PRIMARY_BLUE, activeforeground="#ffffff")
            menubar.add_cascade(label="Window", menu=window_menu)
            window_menu.add_command(label="Close", command=self.root.destroy, accelerator="Cmd+W")
            window_menu.add_command(label="Minimize", command=lambda: self.root.iconify())
        else:
            file_menu = tk.Menu(menubar, tearoff=0,
                                background=LIGHT_BG, fg=LIGHT_FG,
                                activebackground=PRIMARY_BLUE, activeforeground="#ffffff")
            menubar.add_cascade(label="File", menu=file_menu)
            file_menu.add_command(label="About", command=self._show_about)
            file_menu.add_separator()
            file_menu.add_command(label="Exit", command=self.root.quit)

    # -------------------------------------------------------------------------
    # UI — ALL ttk widgets, all configured via the styles set up in
    # _apply_palette. Plain tk widgets on macOS Aqua Tk 8.5 do not draw a
    # background even with `bg=` set, which leaves the body blank.
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        # macOS Aqua Tk 8.5 (bundled in the frozen app) renders ttk.Label text
        # invisibly — confirmed by inspection: ttk.Label exists in the widget
        # tree but macOS accessibility and screencapture only see the toplevel
        # window title. The reliable workaround is to use a single tk.Canvas
        # that paints every text/background manually — Canvas reaches the
        # screen consistently on Aqua Tk 8.5. Buttons stay ttk because they
        # render fine.
        width, height = 620, 560
        self.canvas = tk.Canvas(self.root, width=width, height=height,
                                bg=LIGHT_BG, highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # We populate the canvas once we know its actual width.
        self._canvas_width = width
        self._render_canvas()

    def _on_canvas_configure(self, event) -> None:
        self._canvas_width = event.width
        self._render_canvas()

    def _render_canvas(self) -> None:
        """Draw the entire UI onto a single canvas.

        Only items tagged "static" are cleared and redrawn here. The
        embedded widgets (button row, advanced checkbutton, advanced
        frame) are created once and only repositioned afterwards — a
        plain `delete("all")` would remove the `create_window` items that
        host them, and since they are only ever (re)created behind
        `hasattr` guards, they would disappear permanently after the very
        first <Configure> event (which macOS fires automatically while the
        window is being laid out at startup).
        """
        c = self.canvas
        c.delete("static")
        w = self._canvas_width
        # Layout constants
        pad = 24
        y = 16

        # DEBUG: dump canvas state to file so we can verify rendering.
        try:
            with open("/tmp/giddh-canvas-debug.log", "w") as _f:
                _f.write(f"canvas bg={c.cget('bg')}\n")
                _f.write(f"canvas width={c.winfo_width()}\n")
                _f.write(f"canvas height={c.winfo_height()}\n")
                _f.write(f"canvas viewable={c.winfo_viewable()}\n")
        except Exception:
            pass

        # ── Hero ───────────────────────────────────────────────────────────
        # Icon (if available)
        icon_path = get_icon_path()
        icon_size = 72
        icon_x = (w - icon_size) // 2
        if icon_path:
            try:
                if not hasattr(self, "_logo_photo_rendered"):
                    from PIL import Image, ImageTk
                    img = Image.open(icon_path).convert("RGBA")
                    img = img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                    self._logo_photo = ImageTk.PhotoImage(img)
                c.create_image(icon_x + icon_size // 2, y + icon_size // 2,
                               image=self._logo_photo, tags="static")
            except Exception:
                logger.exception("Failed to load hero icon at %s", icon_path)
        y += icon_size + 12

        # Title (large bold)
        c.create_text(w // 2, y, text=APP_NAME, fill=LIGHT_FG,
                      font=("Helvetica", 22, "bold"), anchor="n", tags="static")
        y += 32

        # Version (smaller gray)
        c.create_text(w // 2, y, text=f"Version {VERSION}", fill=SECONDARY_FG,
                      font=("Helvetica", 11), anchor="n", tags="static")
        y += 20

        # Description (wrap)
        c.create_text(w // 2, y, text=APP_DESCRIPTION, fill=SECONDARY_FG,
                      font=("Helvetica", 12), width=420, anchor="n",
                      justify="center", tags="static")
        # Approximate wrap height (lines * 18)
        wrap_chars = 420 // 6  # rough
        lines = max(1, len(APP_DESCRIPTION) // max(1, wrap_chars) + 1)
        y += lines * 18 + 14 + 20

        # NOTE: an always-on "Native host / Browser registered / DSC token"
        # status card used to render here. It relied on locating the native
        # host executable on disk (_find_host_executable), which is only
        # reliable right after a fresh install — a user simply reopening
        # this app later (leftover .app from an older/partial install,
        # host moved, etc.) would see a permanently red "not installed"
        # error box even when everything actually works, with no easy way
        # to tell a real problem from a stale one. Removed in favor of the
        # on-demand "Check token" button below, which reports the live
        # result the moment it's clicked instead of a static guess at
        # startup.

        # ── Buttons (ttk.Button — these render fine on Aqua Tk 8.5) ────────
        # We want them at the same vertical position as `y`. Use a tk.Frame
        # overlay to host the buttons at known coordinates.
        if not hasattr(self, "_button_frame"):
            self._button_frame = tk.Frame(self.canvas, bg=LIGHT_BG)
            self._button_window = c.create_window(pad, y, window=self._button_frame,
                                                  anchor="nw")
            ttk.Button(self._button_frame, text="Check token",
                       command=self._on_check_token,
                       style="Primary.TButton").pack(side=tk.LEFT, padx=(0, 8))
            ttk.Button(self._button_frame, text="Uninstall…",
                       command=self._on_uninstall).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Button(self._button_frame, text="Quit",
                       command=self.root.quit).pack(side=tk.LEFT)
        else:
            c.coords(self._button_window, pad, y)
        y += 50

        # ── Footer text (drawn last, anchored to bottom) ───────────────────
        footer_y = max(y + 200, self.root.winfo_height() - 36)
        c.create_text(w // 2, footer_y,
                      text="The browser extension uses this bridge automatically. You can close this window.",
                      fill=SECONDARY_FG, font=("Helvetica", 11),
                      width=520, justify="center", tags="static")

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def _show_about(self) -> None:
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME}\nVersion {VERSION}\n\n{APP_DESCRIPTION}",
        )

    def _on_check_token(self) -> None:
        self.root.config(cursor="wait")
        self.canvas.update_idletasks()

        def run() -> None:
            result = run_host_diagnose()
            # A successful CLI run returns the host's own diagnose() reply —
            # {"success": true, "diagnostics": {...}} — which has no
            # top-level "token_present"/"certificates" keys; those never
            # existed, so reading them always reported "not detected" even
            # with a token plugged in. Token presence has to be derived from
            # diagnostics.tokens / pcsc_readers instead.
            error = result.get("error")
            diagnostics = result.get("diagnostics") or {}
            tokens = [t for t in (diagnostics.get("tokens") or []) if t]
            card_present = any(r.get("card_present") for r in (diagnostics.get("pcsc_readers") or []))
            token_ok = bool(tokens) or card_present
            self.root.after(0, lambda: self._update_token_status(token_ok, tokens, error))

        threading.Thread(target=run, daemon=True).start()

    def _update_token_status(self, token_ok: bool, tokens: list[str], error: str | None) -> None:
        self.root.config(cursor="")
        if error:
            messagebox.showerror("Check token", f"Could not check the token:\n\n{error}")
        elif token_ok:
            label = f"\n\nDetected: {tokens[0]}" if tokens else ""
            messagebox.showinfo("Check token", f"DSC token detected.{label}")
        else:
            messagebox.showwarning(
                "Check token",
                "No DSC token detected. Plug in the token and click Check token again.",
            )

    def _on_uninstall(self) -> None:
        if not messagebox.askyesno("Uninstall", f"Remove {APP_NAME} from this computer?", icon="warning"):
            return
        # TODO: implement platform-specific uninstall script invocation
        messagebox.showinfo(
            "Uninstall",
            "Uninstall script is not bundled in this build.\n"
            "Please delete the app and native-host manifest manually.")


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} status companion")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    root = tk.Tk()
    _apply_palette(root)
    app = StatusApp(root)

    # macOS Aqua Tk 8.5 windowed apps can fail to map their window onto the
    # screen until a few idle/updater cycles have passed. Forcing the
    # toplevel to "raised" + "wm_state" + multiple update_idletasks() calls
    # pushes the window through the macOS window-server registration.
    root.update_idletasks()
    root.update()
    try:
        root.wm_attributes("-topmost", True)
        root.update()
        root.wm_attributes("-topmost", False)
        root.update_idletasks()
    except Exception:
        pass
    root.lift()
    root.focus_force()
    root.update_idletasks()

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())