; Inno Setup script — Giddh DSC Bridge (Windows).
; Installs the frozen native host, writes the native-messaging manifest, and
; registers it per-user (HKCU) for Chrome/Edge/Brave/Chromium. No admin needed.
;
; Build (values injected by build_windows.ps1):
;   iscc /DAppVersion=1.6.0 /DExtId=<published-extension-id> installer.iss

#ifndef AppVersion
  #define AppVersion "1.6.0"
#endif
#ifndef ExtId
  #define ExtId "klmgadogecbimgjkepdljfljajphemfl"
#endif
#define HostName "com.giddh.dsc.bridge"

[Setup]
AppId={{7F3B2C10-9E44-4A2B-9C1D-4B2A1F6E9D30}}
AppName=Giddh DSC Bridge
AppVersion={#AppVersion}
AppPublisher=Giddh
DefaultDirName={localappdata}\Giddh DSC Bridge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UninstallDisplayName=Giddh DSC Bridge
SetupIconFile=..\..\icons\app.ico
UninstallDisplayIcon={app}\status\giddh-dsc-status.exe
OutputDir=..\..\dist
OutputBaseFilename=GiddhDSCBridge-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Files]
; onedir output: ship the whole folder (giddh-dsc-host.exe + _internal\).
Source: "..\..\dist\giddh-dsc-host\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Visible status/companion app (windowed GUI) -> its own subfolder.
Source: "..\..\dist\giddh-dsc-status\*"; DestDir: "{app}\status"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start-menu + optional desktop shortcut so users can SEE and manage the bridge.
Name: "{userprograms}\Giddh DSC Bridge"; Filename: "{app}\status\giddh-dsc-status.exe"
Name: "{userdesktop}\Giddh DSC Bridge"; Filename: "{app}\status\giddh-dsc-status.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Registry]
; Point each Chromium-family browser at the manifest we drop in {app}.
Root: HKCU; Subkey: "SOFTWARE\Google\Chrome\NativeMessagingHosts\{#HostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#HostName}.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Edge\NativeMessagingHosts\{#HostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#HostName}.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "SOFTWARE\BraveSoftware\Brave-Browser\NativeMessagingHosts\{#HostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#HostName}.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "SOFTWARE\Chromium\NativeMessagingHosts\{#HostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#HostName}.json"; Flags: uninsdeletekey

[UninstallDelete]
Type: files; Name: "{app}\{#HostName}.json"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Manifest, ExePath, Json: string;
begin
  if CurStep = ssPostInstall then
  begin
    Manifest := ExpandConstant('{app}\{#HostName}.json');
    ExePath := ExpandConstant('{app}\giddh-dsc-host.exe');
    // JSON requires escaped backslashes in the Windows path.
    StringChangeEx(ExePath, '\', '\\', True);
    Json :=
      '{' + #13#10 +
      '  "name": "{#HostName}",' + #13#10 +
      '  "description": "Giddh DSC Bridge - PKCS#11 token signing",' + #13#10 +
      '  "path": "' + ExePath + '",' + #13#10 +
      '  "type": "stdio",' + #13#10 +
      '  "allowed_origins": ["chrome-extension://{#ExtId}/"]' + #13#10 +
      '}' + #13#10;
    SaveStringToFile(Manifest, Json, False);
  end;
end;
