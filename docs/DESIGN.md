---
covers: ["dsc-bridge/extension/popup.*", "dsc-bridge/extension/icons/**", "dsc-bridge/native-host/giddh_dsc_status.py", "dsc-bridge/test-page/**", "icons/**"]
verified_at: c591bc2
verified_on: 2026-11-17
---
# Design System & UI Standards

The bridge has no web app; the signing UI (certificate picker, PIN prompt, progress) lives in the Giddh web app. This repo owns three small surfaces plus the icon set:

| Surface | File | Audience |
|---|---|---|
| Extension toolbar popup | `dsc-bridge/extension/popup.html`, `popup.css`, `popup.js` | End users: "is it installed, which version" |
| Desktop companion ("status") app | `dsc-bridge/native-host/giddh_dsc_status.py` | End users after install; Tkinter, one window |
| Standalone test page | `dsc-bridge/test-page/index.html` | Giddh staff / support / developers, not end users |

No framework, no shared component library, no build step. Each surface is plain HTML+CSS or Tk drawn by hand.

## Visual & Interaction Principles

1. **Read-only, reassuring surfaces.** The popup and the status app only say "installed, version, Powered by Giddh"; neither talks to the token. Keep them that way — the popup must never call `chrome.runtime.sendMessage`/`connectNative` (see the security note in `dsc-bridge/extension/popup.js`).
2. **Light palette everywhere, forced.** The status app ignores OS dark mode on purpose (bundled Tk 8.5 on macOS rendered black-on-black → "blank window"); the popup and test page define only light colours.
3. **One brand mark.** Every surface shows the same logo derived from `icons/app_source_1024.png`.
4. **The test page shows raw truth.** It dumps full JSON replies in an Output panel and labels tokens by certificate owner, because support needs facts, not friendly copy.

## Design Tokens & Styling Primitives

There is no shared token file; each surface declares its own. Keep them consistent by hand.

| Surface | Where | Tokens |
|---|---|---|
| Popup | `dsc-bridge/extension/popup.css` `:root` | `--brand #145be7` (sampled from the master icon), `--slate #475569`, `--bg #f8fafc`; body 220px wide; system font stack |
| Status app | `giddh_dsc_status.py` constants | `LIGHT_BG #ffffff`, `LIGHT_FG #1d1d1f`, `SECONDARY_FG #6b7280`, `ACCENT_BG #f3f4f6`, `PRIMARY_BLUE #2563eb`, `PRIMARY_BLUE_HOVER #1d4ed8`; Helvetica 22 bold (title), 12 (description), 11 (footer) |
| Test page | inline `<style>` `:root` | `--teal #0891b2`, `--teal-d #0e7490`, `--slate #475569`, `--ink #0f172a`, `--bd #e2e8f0`; status dot ok `#059669` / bad `#dc2626`; cards 12px radius |

- **Mismatch to know:** three different "primary" blues/teals (`#145be7`, `#2563eb`, `#0891b2`). Only the popup's is sampled from the logo. Prefer `--brand #145be7` for new end-user UI.
- **Dark mode:** none on any surface. Status app: `StatusApp` paints on a single `tk.Canvas`; `_apply_palette` forces light `ttk` styles and `packaging/pyinstaller/giddh_dsc_status.spec` sets `NSRequiresAquaSystemAppearance`. Do not remove either without re-testing macOS dark mode.

## Surfaces and states

### Extension popup
Static column: 72px logo (`dsc-bridge/extension/icons/icon128.png`), `Version <manifest.version>` (placeholder `Version …` until `popup.js` runs), tagline "Powered by Giddh". One state; it does not detect the native host or a token.

### Status app (`giddh_dsc_status.py`)

| Element | Copy / behaviour |
|---|---|
| Window | Title "Giddh DSC Bridge", 560×340 centred, min 480×280 |
| Hero | 72px logo (`get_icon_path`: `app.icns` / `app.ico` / tray PNG fallback), title, description `APP_DESCRIPTION` = "Connect your DSC token to Giddh for secure, client-side PDF signing." |
| Footer | "The browser extension uses this bridge automatically. You can close this window." |
| Menu (macOS) | Giddh DSC Bridge → About, Quit (Cmd+Q); Window → Close (Cmd+W), Minimize |
| Menu (Win/Linux) | File → About, Exit |
| About dialog | name, `Version <VERSION>`, description (the only place the version shows; removed from the window in `992332f`) |
| Startup warning (macOS) | "Could not register the DSC bridge host from this app: … Use “Check token” to verify the bridge works." when `install_native_host` fails |

There are **no buttons** at `c591bc2`: "Check token" and "Uninstall…" were removed in `11810e8`, the module manager in `fe15172`. README, `PROJECT.md` §6, the Linux `.desktop` comment ("DSC token status, check, and uninstall"), the startup warning, and host error text ("attach the module in the Giddh DSC Bridge app") still promise them. Treat that copy as stale; see [OPEN_QUESTIONS](OPEN_QUESTIONS.md).

### Test page (`dsc-bridge/test-page/index.html`)

Five cards, top to bottom:

| Card | Controls | States |
|---|---|---|
| 1. Bridge status | Re-check, Run diagnose | dot grey (checking) → green "Giddh DSC Bridge detected (page script <version>)" / red "Bridge NOT detected — install the extension + native host"; buttons disabled when not detected |
| 2. PKCS#11 modules | List modules | PC/SC banner per reader; red "No module can read this card" banner when a card is present but no module serves it; one row per module with ★ pinned / + attached and a status (`moduleStatus`) |
| 3. Certificates on token | Token `<select>` (default "All connected tokens (aggregate)"), Read certificates | cert cards, click to select (`.cert.sel`); token options labelled "<cert owner> (<token label>)" |
| 4. Sign a test hash | text input, "already a SHA-256 hash" checkbox, PIN (password), Sign (disabled until a cert is selected) | |
| Output | `<pre>` with raw JSON | |

Stale-extension guard: `requireApi` tells the user to reload the extension when a method is missing.

> [!WARNING]
> The test page assumes failures resolve as `{success:false}`; the bridge actually **rejects** ([CONTRACTS](CONTRACTS.md)). On any error from Read certificates / Sign / Run diagnose / List modules the Promise rejects uncaught and Output stays on "Reading…/Signing…". Wrap calls in `try/catch` and print `err.code` + `err.message` when touching this page. It also interpolates certificate and module strings into `innerHTML` unescaped.

## Icon set

Master: `icons/app_source_1024.png` (1024×1024). Regeneration recipe: carried over from `PROJECT.md` §7 into [DEVELOPMENT](DEVELOPMENT.md).

| File | Size | Used by |
|---|---|---|
| `icons/app.icns` | multi | macOS status `.app` icon (`packaging/pyinstaller/giddh_dsc_status.spec`); status-app hero on macOS |
| `icons/app.ico` | 16–256 | Windows host + status exe (`giddh_dsc_host.spec`, `giddh_dsc_status.spec`), installer `SetupIconFile` (`packaging/windows/installer.iss`); hero on Windows |
| `icons/tray.png` | 16×16 | Linux status build icon (`giddh_dsc_status.spec`); hero fallback |
| `icons/tray@2x.png` | 32×32 | macOS hero fallback |
| `icons/tray-small.png` | 16×16 | only `get_tray_icon_path`, which nothing calls (no tray exists) |
| `dsc-bridge/extension/icons/icon{16,32,48,128}.png` | as named | `manifest.json` `icons` + `action.default_icon`; `icon128.png` is the popup logo. Separate copies because an extension can only reference files inside its own folder |

The Linux `.desktop` entry written by `packaging/linux/build_deb.sh` has no `Icon=` line, so the app menu shows a generic icon.

## Responsive Behavior & Breakpoints

Not applicable beyond: popup fixed 220px; status window resizable but content is centred on a canvas re-rendered on `<Configure>`; test page `max-width: 820px`, `.row` wraps.

## Accessibility (A11y) Standards

Observed, not enforced:
- Popup logo has `alt`; the version is plain text. Test page uses native `<button>`, `<select>`, `<input>`; labels are not bound with `for=` and cert cards are clickable `<div>`s (not keyboard-focusable).
- Status app text is painted on a `tk.Canvas`, so it is **invisible to screen readers** (the comment in `_build_ui` notes macOS accessibility "only sees the toplevel window title"). The About dialog is the accessible path to the version.
- Secondary grey `#6b7280` on white is ~4.8:1; footer text is 11pt.

## Messaging & Copy Guidelines (end users)

Users are non-technical staff at Giddh's customer businesses signing with their own DSC token (confirmed by the owner, 2026-11-17).

- **Error text is product copy.** Host `error` strings are shown verbatim by Giddh's UI (INTEGRATION.md tells it to). Each must say what happened and the one action to take ("Unplug and replug the token, then try again."). Keep `code` stable; change wording freely but deliberately.
- **Say "DSC token", "token PIN", "Giddh DSC Bridge"** — the words users see on their token packaging and in the app menu. Avoid "PKCS#11", "module", "slot" except in advanced/support copy (test page, `TOKEN_UNSUPPORTED_BY_MODULE`, which already explains in plain terms).
- **Never blame the card from module errors**; copy about busy/missing/unsupported cards comes from PC/SC facts (`_classify_card_failure`).
- **PIN lock copy must send users to their DSC provider**, not Giddh support — Giddh cannot unblock a token.
- **Do not promise UI that does not exist** (current violations: "Check token", "Attach module…", "Uninstall…").
- Name things users can find: the Start-menu / Applications entry "Giddh DSC Bridge", `chrome://extensions` → Reload.

## SEO & Metadata Expectations

Not applicable — no public pages. Only document titles: popup "Giddh DSC Bridge", test page "Giddh DSC Bridge — Standalone Tester".
