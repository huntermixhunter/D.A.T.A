; DAITA - Windows installer wizard (Inno Setup 6)
; ----------------------------------------------------------------------------
; Produces DAITA-Setup-v<ver>.exe: a click-through wizard that installs DAITA with
; a bundled embedded Python (no system Python required) and optionally installs
; the AI provider (Node.js + Claude Code). Per-user install, no admin needed.
;
; Do NOT compile this by hand. The build pipeline (tools\build_installer.ps1)
; stages a clean fileset + the embedded runtime, then passes the two required
; defines below and runs ISCC:
;
;   AppVersion  - e.g. 1.0.21
;   StagingDir  - the staged product tree (contains dashboard\, runtime\, etc.)
;
; Example: ISCC.exe /DAppVersion=1.0.21 /DStagingDir=...\build\staging\DAITA installer\DAITA.iss

#ifndef AppVersion
  #error You must pass /DAppVersion=x.y.z   (the build script does this)
#endif
#ifndef StagingDir
  #error You must pass /DStagingDir=<staged product tree>   (the build script does this)
#endif

#define AppName       "DAITA"
#define AppPublisher  "Magimatix"
#define AppURL        "https://magimatix.com"
#define AppExeVbs     "start_daita.vbs"

[Setup]
; A stable AppId ties upgrades + uninstall together across versions. Do not change.
AppId={{8F3C2A14-9D7B-4E55-B2A1-DA7A0C0FEED5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install: no UAC prompt, and DAITA can freely write its own state
; (.env, logs, users\, conversation files) inside its folder.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#StagingDir}\..\..\..\dist
OutputBaseFilename=DAITA-Setup-v{#AppVersion}
SetupIconFile={#StagingDir}\dashboard\favicon.ico
UninstallDisplayIcon={app}\dashboard\favicon.ico
UninstallDisplayName={#AppName} {#AppVersion}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LicenseFile={#StagingDir}\LICENSE
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "provider"; Description: "Install the AI provider so chat works (Claude Code + Node.js, ~1-2 min, needs internet)"; GroupDescription: "AI provider:"

[Files]
; The staged product tree (clean tracked files + the embedded runtime).
Source: "{#StagingDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Launchers (location-independent via %~dp0; point at the bundled runtime).
Source: "launchers\start_daita.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "launchers\start_daita.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "launchers\stop_daita.bat";  DestDir: "{app}"; Flags: ignoreversion
; Provider bootstrap — extracted to {tmp} only during install, not shipped.
Source: "setup_provider.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeVbs}"; WorkingDir: "{app}"; IconFilename: "{app}\dashboard\favicon.ico"; Comment: "Start DAITA (runs in the background)"
Name: "{group}\Stop DAITA"; Filename: "{app}\stop_daita.bat"; WorkingDir: "{app}"; IconFilename: "{app}\dashboard\favicon.ico"; Comment: "Stop DAITA"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeVbs}"; WorkingDir: "{app}"; IconFilename: "{app}\dashboard\favicon.ico"; Comment: "Start DAITA (runs in the background)"; Tasks: desktopicon

[Run]
; Install the bundled DAITA-core skills using the embedded runtime (idempotent).
Filename: "{app}\runtime\python\python.exe"; Parameters: """{app}\dashboard\install_skills.py"""; WorkingDir: "{app}"; StatusMsg: "Installing DAITA-core skills..."; Flags: runhidden skipifdoesntexist
; Optional: install the AI provider (Node.js + Claude Code). Shown only if ticked.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{tmp}\setup_provider.ps1"""; StatusMsg: "Installing the AI provider (Claude Code + Node.js)... this can take a minute."; Flags: waituntilterminated; Tasks: provider
; Offer to launch DAITA at the end.
Filename: "{app}\{#AppExeVbs}"; Description: "Launch {#AppName} now"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Make sure DAITA isn't running (frees its ports) before files are removed.
Filename: "{app}\stop_daita.bat"; Flags: runhidden; RunOnceId: "StopData"

[UninstallDelete]
; Remove runtime state the app created at runtime (not tracked by [Files]).
Type: filesandordirs; Name: "{app}\runtime\python\Lib\site-packages\__pycache__"
Type: files; Name: "{app}\bridge.log"
Type: files; Name: "{app}\start_daita.bat"
Type: files; Name: "{app}\start_daita.vbs"
Type: files; Name: "{app}\stop_daita.bat"

[Code]
{ Seed .env from .env.example on first install only — never clobber an existing one. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvPath, ExamplePath: string;
begin
  if CurStep = ssPostInstall then
  begin
    EnvPath     := ExpandConstant('{app}\.env');
    ExamplePath := ExpandConstant('{app}\.env.example');
    if (not FileExists(EnvPath)) and FileExists(ExamplePath) then
      CopyFile(ExamplePath, EnvPath, False);
  end;
end;

procedure InitializeWizard();
begin
  { setup_provider.ps1 is flagged dontcopy; extract it so the [Run] entry can call it. }
  ExtractTemporaryFile('setup_provider.ps1');
end;
