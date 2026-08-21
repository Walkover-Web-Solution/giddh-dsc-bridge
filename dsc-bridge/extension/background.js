/*
 * Giddh DSC Bridge — Background Service Worker (MV3)
 * ====================================================
 * Relays messages between the page (content script) and the native
 * messaging host (Python PKCS#11 process).
 *
 * Uses a fresh native port (chrome.runtime.connectNative) PER request:
 *   getCertificate — list certs from token (no PIN needed on most drivers)
 *   signHash       — login + sign (PIN passed from page)
 *
 * Why one process per call (not a shared long-lived port)?
 * ----------------------------------------------------------------
 * PKCS#11 C_Initialize is bound to the process and to the token present when it
 * ran. A shared/long-lived host caches that state, so hot-swapping the physical
 * token leaves the driver stale — and a wedged host cannot be force-killed from
 * the extension, causing multi-second driver-lock hangs. A fresh host per call
 * gives a clean C_Initialize every time and dies cleanly after, which is the
 * only robust model for the fragile Indian-DSC PKCS#11 drivers. The dominant
 * cost is the hardware token read itself, which no amount of caching avoids.
 *
 * Chrome keeps the service worker alive while a native messaging port is open,
 * so connectNative (not one-shot sendNativeMessage) avoids the MV3 tear-down
 * "message channel closed before a response was received" bug.
 *
 * Native host name: com.giddh.dsc.bridge
 */

const NATIVE_HOST_NAME = "com.giddh.dsc.bridge";

// Hard ceiling for a single native round-trip (cert read / sign). Must stay
// under the page-side timeouts in dsc-signing.js (30s cert, 60s sign).
const NATIVE_OP_TIMEOUT_MS = 55000;

// ═════════════════════════════════════════════════════════════════════════
// DOMAIN RESTRICTION — PRODUCTION MODE
// ═════════════════════════════════════════════════════════════════════════
// Set this to true ONLY for local development/testing. When false, the bridge
// only responds to origins matching the allowlist below and localhost.
const ALLOW_ALL_ORIGINS = false;

// Production allowlist (used only when ALLOW_ALL_ORIGINS is false).
// sender.origin is set by Chrome from the real page origin and cannot be
// spoofed by page JS. Keep in sync with "matches" in manifest.json.
const ALLOWED_HOST_SUFFIXES = [".giddh.com", ".erpdocs.com"]; // + all subdomains
const ALLOWED_HOSTS_EXACT = ["giddh.com", "erpdocs.com"];      // apex domains
const ALLOW_LOCALHOST = true;                                   // allow http(s)://localhost

function _isAllowedOrigin(origin, senderUrl) {
  if (ALLOW_ALL_ORIGINS) return true;        // development override
  // file:// pages have an opaque ("null") origin, so allow them via
  // sender.url instead — that value is set by Chrome from the real page
  // location and cannot be spoofed by page JS. This only enables the
  // standalone file:// test page; it grants no access to remote origins.
  if (senderUrl && senderUrl.startsWith("file://")) return true;
  if (!origin) return false;
  let scheme, host;
  try {
    const u = new URL(origin);
    scheme = u.protocol;
    host = u.hostname;
  } catch (_) {
    return false;
  }
  if (ALLOW_LOCALHOST && (host === "localhost" || host === "127.0.0.1")) return true;
  if (scheme !== "https:") return false;     // production origins must be HTTPS
  if (ALLOWED_HOSTS_EXACT.includes(host)) return true;
  return ALLOWED_HOST_SUFFIXES.some((s) => host.endsWith(s));
}

// ── Single-flight native calls ──────────────────────────────────────────────
// The vendor PKCS#11 driver (e.g. ProxKey's libwdpkcs) guards the token with a
// CROSS-PROCESS mutex: C_GetSlotList -> StartMutexDevice checks for other live
// host processes (isProcessexist via popen) and does a filesystem walk. If two
// host processes run at once, the second spin-sleeps for seconds waiting on the
// first's mutex — the "taking too much time" / NATIVE_TIMEOUT hang. So we run
// AT MOST ONE host at a time and queue the rest. Each call still gets a fresh
// process (clean C_Initialize, so token swaps keep working).
let _nativeChain = Promise.resolve();

function _enqueueNative(payload) {
  const result = _nativeChain.then(() => _nativeCall(payload));
  _nativeChain = result.then(() => {}, () => {}); // keep the queue alive on error
  return result;
}

// One request = one fresh host process, opened and torn down here. Always
// resolves (never rejects) with the host's JSON response or a typed error.
function _nativeCall(payload) {
  return new Promise((resolve) => {
    let settled = false;
    let port = null;
    let timer = null;

    const finish = (response) => {
      if (settled) return;
      settled = true;
      if (timer) { clearTimeout(timer); timer = null; }
      try { if (port) port.disconnect(); } catch (_) { /* already gone */ }
      resolve(response);
    };

    try {
      port = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    } catch (e) {
      finish({
        success: false,
        error: "Cannot start the DSC native host: " + ((e && e.message) || e) +
          ". Re-run the Giddh DSC Bridge installer and try again.",
        code: "NATIVE_START_FAILED",
      });
      return;
    }

    port.onMessage.addListener((response) => {
      finish(response || { success: false, error: "Empty response from native host", code: "EMPTY_RESPONSE" });
    });

    port.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError;
      finish({
        success: false,
        error: err && err.message
          ? "Native host disconnected: " + err.message
          : "The DSC native host closed unexpectedly. Ensure the token driver is installed and re-run the bridge installer.",
        code: "NATIVE_DISCONNECTED",
      });
    });

    // Safety net: never leave the page hanging if the host neither replies nor
    // disconnects (e.g. a wedged driver blocking on hardware).
    timer = setTimeout(() => {
      finish({
        success: false,
        error: "The DSC token did not respond in time. Unplug and replug the token, then try again.",
        code: "NATIVE_TIMEOUT",
      });
    }, NATIVE_OP_TIMEOUT_MS);

    try {
      port.postMessage(payload);
    } catch (e) {
      finish({
        success: false,
        error: "Failed to send the request to the native host: " + ((e && e.message) || e),
        code: "NATIVE_SEND_FAILED",
      });
    }
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!(msg && msg.type === "GIDDH_DSC" && msg.action)) {
    return; // not ours — let other listeners handle it
  }

  // Layer 2 gate: reject any page that isn't on the allowlist. sender.origin is
  // the trustworthy page origin; sender.url is a fallback for older Chrome.
  const origin = sender.origin || (sender.url ? (() => { try { return new URL(sender.url).origin; } catch (_) { return null; } })() : null);
  if (!_isAllowedOrigin(origin, sender.url)) {
    sendResponse({
      success: false,
      error: "This site is not authorized to use the Giddh DSC Bridge.",
      code: "ORIGIN_NOT_ALLOWED",
    });
    return false; // responded synchronously
  }

  // Strip the wrapper before sending to the native host.
  const payload = { action: msg.action };
  if (msg.hash !== undefined) payload.hash = msg.hash;
  if (msg.algorithm !== undefined) payload.algorithm = msg.algorithm;
  if (msg.certId !== undefined) payload.certId = msg.certId;
  if (msg.pin !== undefined) payload.pin = msg.pin;
  // Optional PKCS#11 driver path — lets the page restrict getCertificate to
  // one specific token (picked from listModules) instead of aggregating
  // across every plugged-in token. Read-only pass-through: the host only
  // ever accepts a path it itself already reported via listModules.
  if (msg.driver !== undefined) payload.driver = msg.driver;

  // Queue behind any in-flight native call so only one host process runs at a
  // time (avoids the driver's cross-process mutex contention).
  _enqueueNative(payload).then((response) => {
    try { sendResponse(response); } catch (_) { /* page navigated away */ }
  });

  return true; // async response
});
