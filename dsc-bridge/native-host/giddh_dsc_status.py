#!/usr/bin/env python3
"""
Giddh DSC Bridge — status / companion application.

This is the small desktop window users see after installing the bridge. It is
not required for signing (the browser extension talks directly to the native
host). PKCS#11 module management (listing, attaching, picking a token) is
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
import shutil
import sys
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


def _bundle_search_roots() -> list[Path]:
    """Directories that can hold files shipped alongside the frozen program.

    Inside a macOS .app, PyInstaller puts the executable in Contents/MacOS,
    binaries in Contents/Frameworks and data files in Contents/Resources (the
    two mirror each other through relative symlinks). Bundled files are
    therefore NOT necessarily beside the executable, so every location has to
    be searched.
    """
    roots = [get_app_root()]

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass).resolve())

    if getattr(sys, "frozen", False) and is_mac():
        contents = Path(sys.executable).resolve().parent.parent
        roots.append(contents / "Frameworks")
        roots.append(contents / "Resources")

    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def find_bundled_path(name: str) -> Path | None:
    """Locate a file/directory shipped with the app, or None if absent."""
    for root in _bundle_search_roots():
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _resolve_version() -> str:
    """Return the build-time version if available, else the repo VERSION file."""
    buildinfo = find_bundled_path("_buildinfo.py")
    if buildinfo is not None:
        try:
            ns = {}
            exec(compile(buildinfo.read_text(encoding="utf-8"), str(buildinfo), "exec"), ns)
            v = ns.get("VERSION")
            if v:
                return v
        except Exception:
            pass

    version_candidates = []
    bundled_version = find_bundled_path("VERSION")
    if bundled_version is not None:
        version_candidates.append(bundled_version)
    if not getattr(sys, "frozen", False):
        # Development: this script lives at dsc-bridge/native-host/, so the
        # repo VERSION file is two directories up.
        version_candidates.append(Path(__file__).resolve().parent.parent.parent / "VERSION")

    for version_file in version_candidates:
        if version_file.exists():
            try:
                return version_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

    return "1.0.0"


def _get_buildinfo() -> dict:
    """Read build-time metadata written by the packaging script."""
    buildinfo = find_bundled_path("_buildinfo.py")
    if buildinfo is not None:
        try:
            ns = {}
            exec(compile(buildinfo.read_text(encoding="utf-8"), str(buildinfo), "exec"), ns)
            return ns
        except Exception:
            pass
    return {}


BUILDINFO = _get_buildinfo()
VERSION = BUILDINFO.get("VERSION") or _resolve_version()
EXT_ID = BUILDINFO.get("GIDDH_EXT_ID", "")
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


def install_native_host() -> tuple[bool, str | None]:
    """Install the bundled native host and browser manifest (user-level).

    The companion app ships with the frozen host binary inside its bundle.
    On first launch it copies the binary and support libraries to the user's
    Application Support directory and registers the native-messaging manifest
    for the common Chromium-based browsers. This avoids an installer package
    and therefore a Developer ID Installer certificate.
    """
    if not is_mac():
        # The .pkg/.deb installers handle this on other platforms.
        return True, None

    bundled_host = find_bundled_path("giddh-dsc-host")
    if bundled_host is None:
        return False, "Bundled native host not found."

    if not EXT_ID:
        return False, "Extension ID not available; cannot register native host."

    support_dir = Path.home() / "Library" / "Application Support" / "Giddh DSC Bridge"
    try:
        support_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, f"Cannot create support directory: {exc}"

    installed_host = support_dir / "giddh-dsc-host"
    try:
        shutil.copy2(bundled_host, installed_host)
        os.chmod(installed_host, 0o755)
    except Exception as exc:
        return False, f"Failed to copy native host: {exc}"

    bundled_internal = find_bundled_path("_internal")
    if bundled_internal is not None:
        dest_internal = support_dir / "_internal"
        try:
            if dest_internal.exists():
                shutil.rmtree(dest_internal)
            # The bundle's Frameworks/Resources trees cross-reference each
            # other with relative symlinks that break once copied out, so
            # resolve them into real files here.
            shutil.copytree(bundled_internal, dest_internal, symlinks=False)
        except Exception as exc:
            return False, f"Failed to copy native host libraries: {exc}"

    manifest = {
        "name": "com.giddh.dsc.bridge",
        "description": "Giddh DSC Bridge — PKCS#11 token signing",
        "path": str(installed_host),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXT_ID}/"],
    }
    manifest_json = json.dumps(manifest, indent=2)

    browsers = {
        "Chrome": Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
        "Brave": Path.home() / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser",
        "Edge": Path.home() / "Library" / "Application Support" / "Microsoft Edge",
        "Chromium": Path.home() / "Library" / "Application Support" / "Chromium",
    }

    installed_any = False
    for name, base in browsers.items():
        nm_dir = base / "NativeMessagingHosts"
        try:
            nm_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = nm_dir / "com.giddh.dsc.bridge.json"
            manifest_path.write_text(manifest_json, encoding="utf-8")
            installed_any = True
        except Exception as exc:
            logger.warning("Could not register host for %s: %s", name, exc)

    if not installed_any:
        return False, "Could not register the native host for any browser."

    return True, None


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
# Main application window
# -----------------------------------------------------------------------------

class StatusApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=LIGHT_BG)
        self.root.minsize(480, 280)

        # Center on screen.
        self.root.update_idletasks()
        width, height = 560, 340
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
        # screen consistently on Aqua Tk 8.5.
        width, height = 560, 340
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
        """Draw the entire UI onto a single canvas."""
        c = self.canvas
        c.delete("static")
        w = self._canvas_width
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
        y += 36

        # Description (wrap)
        c.create_text(w // 2, y, text=APP_DESCRIPTION, fill=SECONDARY_FG,
                      font=("Helvetica", 12), width=420, anchor="n",
                      justify="center", tags="static")
        # Approximate wrap height (lines * 18)
        wrap_chars = 420 // 6  # rough
        lines = max(1, len(APP_DESCRIPTION) // max(1, wrap_chars) + 1)
        y += lines * 18 + 24

        # ── Footer text (drawn last, anchored to bottom) ───────────────────
        footer_y = max(y + 48, self.root.winfo_height() - 36)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} status companion")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    root = tk.Tk()
    _apply_palette(root)

    # On macOS the app ships the native host inside its bundle; install it
    # (and the browser manifest) into the user's Application Support folder
    # on first launch so the extension can connect without an installer pkg.
    if is_mac():
        ok, err = install_native_host()
        if not ok:
            # Not fatal: the .pkg installer registers the host system-wide, so
            # the bridge can already be working. Report it and keep the window
            # open (Check token tells the user the live state) instead of
            # exiting to a blank screen.
            logger.warning("Native host self-install skipped: %s", err)
            messagebox.showwarning(
                f"{APP_NAME} — Setup notice",
                "Could not install the DSC bridge host from this app:\n\n"
                f"{err}\n\nIf you installed via the .pkg installer this is "
                "expected. Use “Check token” to verify the bridge works.",
            )

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