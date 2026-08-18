Giddh DSC Bridge — macOS installer
==================================

1. Double-click "GiddhDSCBridge.pkg" and follow the prompts (admin password
   required — it registers the signing helper with Chrome/Edge/Brave/Chromium).

2. Install your DSC token vendor's macOS driver (WatchData/ProxKey, SafeNet
   eToken, Feitian ePass2003, etc.) and plug in the token.

3. Install the Giddh DSC Bridge browser extension from the Chrome Web Store.

4. Open Giddh, choose "sign with DSC token", and sign.

Uninstall:
  sudo rm -rf /usr/local/giddh-dsc-bridge
  sudo find /Library -name 'com.giddh.dsc.bridge.json' -delete
