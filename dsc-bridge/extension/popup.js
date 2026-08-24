/*
 * Giddh DSC Bridge — Popup Script
 * ================================
 * SECURITY NOTE (read before extending this file):
 * This script only reads chrome.runtime.getManifest(), which is local to
 * the extension process, and writes the version string into this popup's
 * own DOM. It never calls chrome.runtime.sendMessage / connectNative, so
 * it cannot reach the native host and does not participate in the
 * origin-allowlist gate enforced in background.js (_isAllowedOrigin).
 * Keep it that way: if a future change adds a native-messaging call from
 * this popup, it will arrive at background.js's onMessage listener
 * WITHOUT a sender.tab (popups aren't content scripts in a tab), which
 * today's _isAllowedOrigin check does not special-case and will simply
 * reject. That gate must be re-reviewed deliberately (not just relaxed)
 * before this popup is ever given the ability to talk to the token.
 */
(function () {
  "use strict";

  var manifest = chrome.runtime.getManifest();
  document.getElementById("appVersion").textContent = "Version " + (manifest.version || "?");
})();
