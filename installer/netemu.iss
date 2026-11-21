; Inno Setup script for the Windows installer.
; Build the program first (python tools/build.py), then either let tools/build.py call ISCC
; or run:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\netemu.iss
; Result: release\NetEmu-<version>-setup.exe

#define AppName "NetEmu"
#ifndef AppVersion
  #define AppVersion "0.4.0"
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
LicenseFile=..\LICENSE
OutputDir=..\release
OutputBaseFilename=NetEmu-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
; Qt 6 needs Windows 10 or later; older systems get Inno Setup's own explanation
MinVersion=10.0
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

; ---------------------------------------------------------------------------------------------
; Prerequisite: Microsoft Visual C++ 2015-2022 Redistributable (x64).
; NetEmu ships the runtime DLLs it needs, but some systems (fresh or trimmed Windows images) lack
; the system-wide runtime that Qt plugins and drivers may load. When it is missing, setup tells the
; user, downloads it from Microsoft and installs it after the program files.
; Test switch: /FORCEVC=1 behaves as if the runtime were missing.
; ---------------------------------------------------------------------------------------------
[CustomMessages]
russian.VCNeeded=Для нормальной работы NetEmu нужен компонент Microsoft Visual C++ Redistributable 2015–2022 (x64), а в системе его нет.%n%nУстановщик скачает его с сайта Microsoft (около 25 МБ) и установит вместе с программой.%n%nСкачать и установить сейчас?
english.VCNeeded=NetEmu needs Microsoft Visual C++ Redistributable 2015–2022 (x64), which is not installed on this computer.%n%nSetup will download it from Microsoft (about 25 MB) and install it together with the program.%n%nDownload and install it now?
russian.VCSkipped=Компонент Microsoft Visual C++ Redistributable не будет установлен. Если NetEmu не запустится, установите его вручную:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe
english.VCSkipped=Microsoft Visual C++ Redistributable will not be installed. If NetEmu does not start, install it manually:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe
russian.VCFailed=Не удалось скачать или установить Microsoft Visual C++ Redistributable (%1).%n%nУстановите его вручную: https://aka.ms/vs/17/release/vc_redist.x64.exe
english.VCFailed=Could not download or install Microsoft Visual C++ Redistributable (%1).%n%nPlease install it manually: https://aka.ms/vs/17/release/vc_redist.x64.exe
russian.VCInstalling=Установка Microsoft Visual C++ Redistributable…
english.VCInstalling=Installing Microsoft Visual C++ Redistributable…

[Code]
const
  VCUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe';
var
  DownloadPage: TDownloadWizardPage;
  NeedVC, HaveVC: Boolean;

function VCRedistInstalled: Boolean;
var
  Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
                               'Installed', Installed) and (Installed = 1);
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc),
                                     @OnDownloadProgress);
  NeedVC := (ExpandConstant('{param:FORCEVC|0}') = '1') or not VCRedistInstalled;
  HaveVC := False;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID <> wpReady) or not NeedVC or HaveVC then
    exit;
  if SuppressibleMsgBox(CustomMessage('VCNeeded'), mbInformation, MB_YESNO, IDYES) <> IDYES then
  begin
    SuppressibleMsgBox(CustomMessage('VCSkipped'), mbInformation, MB_OK, IDOK);
    NeedVC := False;
    exit;
  end;
  DownloadPage.Clear;
  DownloadPage.Add(VCUrl, 'vc_redist.x64.exe', '');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      HaveVC := True;
    except
      SuppressibleMsgBox(FmtMessage(CustomMessage('VCFailed'), [GetExceptionMessage]), mbError, MB_OK, IDOK);
      NeedVC := False;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Ok: Boolean;
  Exe: String;
begin
  if (CurStep <> ssPostInstall) or not NeedVC or not HaveVC then
    exit;
  Exe := ExpandConstant('{tmp}\vc_redist.x64.exe');
  WizardForm.StatusLabel.Caption := CustomMessage('VCInstalling');
  if IsAdminInstallMode then
    Ok := Exec(Exe, '/install /quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else
    { per-user install: the runtime is machine-wide, so ask for elevation just for it }
    Ok := ShellExec('runas', Exe, '/install /passive /norestart', '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
  { 0 = done, 1638 = a newer version is already there, 3010 = done, reboot later }
  if not Ok or not ((ResultCode = 0) or (ResultCode = 1638) or (ResultCode = 3010)) then
    SuppressibleMsgBox(FmtMessage(CustomMessage('VCFailed'), ['code ' + IntToStr(ResultCode)]), mbError, MB_OK, IDOK);
end;
