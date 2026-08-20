/*
 * Giddh DSC Bridge — Page Context Script
 * ==========================================
 * This file is injected into the page's main world by the content script.
 * It creates window.GiddhBridge and relays calls back to the content script
 * via postMessage.
 *
 * This is a separate file (not inline) to comply with page CSP policies.
 */
(function () {
  "use strict";

  var _reqId = 0;
  var _pending = {};

  function _send(action, extra) {
    return new Promise(function (resolve, reject) {
      var id = ++_reqId;
      _pending[id] = { resolve: resolve, reject: reject };
      window.postMessage({
        __giddhDsc: true,
        id: id,
        action: action,
        data: extra || {}
      }, "*");
    });
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window || !event.data || !event.data.__giddhDscResp) return;
    var entry = _pending[event.data.id];
    if (!entry) return;
    delete _pending[event.data.id];
    if (event.data.error) {
      var err = new Error(event.data.error);
      if (event.data.code) err.code = event.data.code;
      entry.reject(err);
    } else {
      entry.resolve(event.data.result);
    }
  });

  window.GiddhBridge = {
    isAvailable: function () { return true; },

    // Bump together with manifest.json. Lets a page tell whether the browser
    // is still running a cached copy of this script after an extension update
    // (a stale copy is missing whatever was added in the newer version).
    version: "1.6.0",

    getCertificate: function () {
      return _send("getCertificate");
    },

    diagnose: function () {
      return _send("diagnose");
    },

    // Read-only PKCS#11 module inventory. Attaching or pinning a module is
    // deliberately NOT exposed to the page — that happens only in the desktop
    // companion app, so a website can never make the host load a library.
    listModules: function () {
      return _send("listModules");
    },

    signHash: function (hashB64, algorithm, certId, pin) {
      return _send("signHash", {
        hash: hashB64,
        algorithm: algorithm || "SHA256",
        certId: certId || "",
        pin: pin || ""
      });
    }
  };

  // Signal that the bridge is ready.
  window.dispatchEvent(new CustomEvent("giddh-dsc-bridge-ready"));
})();
