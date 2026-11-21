; Inno Setup script for the Windows installer.
; Build the program first (python tools/build.py), then either let tools/build.py call ISCC
; or run:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\netemu.iss
; Result: release\NetEmu-<version>-setup.exe

#define AppName "NetEmu"
#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif

[Setup]
AppId={{6A0D2C1E-4E5B-4C2A-9B7E-2F1D6C3A9E10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=NetEmu
DefaultDirName={autopf}\NetEmu
DefaultGroupName=NetEmu
UninstallDisplayIcon={app}\NetEmu.exe
SetupIconFile=..\netemu\resources\netemu.ico
OutputDir=..\release
OutputBaseFilename=NetEmu-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc"; Description: "Open .netemu projects with NetEmu"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\NetEmu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NetEmu"; Filename: "{app}\NetEmu.exe"
Name: "{group}\{cm:UninstallProgram,NetEmu}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\NetEmu"; Filename: "{app}\NetEmu.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.netemu"; ValueType: string; ValueName: ""; ValueData: "NetEmu.Project"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\NetEmu.Project"; ValueType: string; ValueName: ""; ValueData: "NetEmu project"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\NetEmu.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\NetEmu.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\NetEmu.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\NetEmu.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\NetEmu.exe"; Description: "{cm:LaunchProgram,NetEmu}"; Flags: nowait postinstall skipifsilent
