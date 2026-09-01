Giddh DSC Bridge — macOS installer
==================================

1. Drag "Giddh DSC Bridge.app" to your Applications folder.

2. Open "Giddh DSC Bridge.app" once. It installs the signing helper and
   registers it with Chrome/Edge/Brave/Chromium automatically.

3. Install your DSC token vendor's macOS driver (WatchData/ProxKey, SafeNet
   eToken, Feitian ePass2003, etc.) and plug in the token.

4. Install the Giddh DSC Bridge browser extension from the Chrome Web Store.

5. Open Giddh, choose "sign with DSC token", and sign.

Uninstall:
  rm -rf /Applications/Giddh\ DSC\ Bridge.app
  rm -rf ~/Library/Application\ Support/Giddh\ DSC\ Bridge
  find ~/Library/Application\ Support -name 'com.giddh.dsc.bridge.json' -delete
