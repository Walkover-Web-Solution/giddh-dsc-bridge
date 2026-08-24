/*
 * Giddh DSC Bridge — Content Script
 * ====================================
 * Injects window.GiddhBridge into the page context and relays calls
 * to the background service worker (which talks to the native host).
 *
 * Communication flow:
 *   page (GiddhBridge) --postMessage--> content script --chrome.runtime--> background
 *   background --sendNativeMessage--> native host (Python PKCS#11)
 *   native host --> background --> content script --postMessage--> page
 *
 * The injected bridge exposes the same API surface as the old Signer.Digital
 * adapter so dsc-signing.js can swap adapters with minimal changes.
 */

(function () {
  "use strict";

  // ── Inject the bridge into the page's main world ──────────────────────
  // We can't directly set window.GiddhBridge from the isolated content
  // script world, so we inject a <script src> that runs in the page context.
  // Using an external file (not inline) to comply with page CSP policies.
  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("bridge-inject.js");
  script.onload = function () { this.remove(); };
  (document.head || document.documentElement).appendChild(script);

  // ── Relay between page (postMessage) and background (chrome.runtime) ──
  // SECURITY NOTE: `event.source !== window` rejects messages posted from
  // any other frame (e.g. an untrusted third-party iframe embedded in an
  // allowed page); only messages the top document posts to itself are
  // relayed. This still runs on every page matched by manifest.json's
  // content_scripts (including localhost/file://), so background.js's own
  // origin allowlist (sender.origin) is the real trust boundary — this
  // content script is just a transport, not a security decision point.
  window.addEventListener("message", function (event) {
    if (event.source !== window || !event.data || !event.data.__giddhDsc) return;

    var id = event.data.id;
    var action = event.data.action;
    var data = event.data.data || {};

    // Field-by-field copy (not a spread/passthrough of `data`) is a
    // deliberate allowlist: only these known keys ever leave this script,
    // so a page cannot inject arbitrary extra properties into the message
    // that reaches background.js / the native host.
    var bgMsg = { type: "GIDDH_DSC", action: action };
    if (data.hash !== undefined) bgMsg.hash = data.hash;
    if (data.algorithm !== undefined) bgMsg.algorithm = data.algorithm;
    if (data.certId !== undefined) bgMsg.certId = data.certId;
    if (data.pin !== undefined) bgMsg.pin = data.pin;
    if (data.driver !== undefined) bgMsg.driver = data.driver;

    function postError(message, code) {
      window.postMessage({ __giddhDscResp: true, id: id, error: message, code: code || null }, "*");
    }

    try {
      chrome.runtime.sendMessage(bgMsg, function (response) {
        // A killed/reloaded service worker leaves lastError set with an
        // undefined response — surface it instead of hanging the page.
        if (chrome.runtime.lastError) {
          postError(chrome.runtime.lastError.message || "Bridge service worker unavailable — reload the page.", "SW_UNAVAILABLE");
          return;
        }
        var reply = { __giddhDscResp: true, id: id };
        if (response && response.success) {
          reply.result = response;
        } else {
          reply.error = (response && response.error) || "Unknown bridge error";
          reply.code = (response && response.code) || null;
        }
        window.postMessage(reply, "*");
      });
    } catch (e) {
      // Thrown synchronously when the extension context is invalidated
      // (extension was reloaded/updated while this page stayed open).
      postError("DSC bridge disconnected (extension was reloaded). Please refresh this page and try again.", "CONTEXT_INVALIDATED");
    }
  });
})();
