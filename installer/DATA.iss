; DATA - Windows installer wizard (Inno Setup 6)
; ----------------------------------------------------------------------------
; Produces DATA-Setup-v<ver>.exe: a click-through wizard that installs DATA with
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
; Example: ISCC.exe /DAppVersion=1.0.21 /DStagingDir=...\build\staging\DATA installer\DATA.iss

#ifndef AppVersion
  #error You must pass /DAppVersion=x.y.z   (the build script does this)
#endif
#ifndef StagingDir
  #error You must pass /DStagingDir=<staged product tree>   (the build script does this)
#endif

#define AppName       "DATA Daemon"
#define AppDir        "DATA"
#define AppPublisher  "Magimatix"
#define AppURL        "https://magimatix.com"
#define AppExeVbs     "start_data.vbs"

[Setup]
; A stable AppId ties upgrades + uninstall together across versions. Do not change.
AppId={{8F3C2A14-9D7B-4E55-B2A1-DA7A0C0FEED5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppDir}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install: no UAC prompt, and DATA can freely write its own state
; (.env, logs, users\, conversation files) inside its folder.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#StagingDir}\..\..\..\dist
OutputBaseFilename=DATA-Setup-v{#AppVersion}
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
; AI provider CLIs — all install via npm, so Node.js is installed once and
; shared. Claude Code is the recommended default (ticked); the others are
; optional. Pick any combination; each still needs a one-time sign-in.
Name: "provider";        Description: "Claude Code — Anthropic (recommended)";  GroupDescription: "AI provider CLIs (installs Node.js once, ~1-2 min, needs internet):"
Name: "provider_codex";  Description: "Codex CLI — OpenAI";                     GroupDescription: "AI provider CLIs (installs Node.js once, ~1-2 min, needs internet):"; Flags: unchecked
Name: "provider_gemini"; Description: "Gemini CLI — Google";                    GroupDescription: "AI provider CLIs (installs Node.js once, ~1-2 min, needs internet):"; Flags: unchecked

[Files]
; The staged product tree (clean tracked files + the embedded runtime).
Source: "{#StagingDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Launchers (location-independent via %~dp0; point at the bundled runtime).
Source: "launchers\start_data.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "launchers\start_data.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "launchers\stop_data.bat";  DestDir: "{app}"; Flags: ignoreversion
; Provider bootstrap — extracted to {tmp} only during install, not shipped.
Source: "setup_provider.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeVbs}"; WorkingDir: "{app}"; IconFilename: "{app}\dashboard\favicon.ico"; Comment: "Start DATA (runs in the background)"
Name: "{group}\Stop DATA"; Filename: "{app}\stop_data.bat"; WorkingDir: "{app}"; IconFilename: "{app}\dashboard\favicon.ico"; Comment: "Stop DATA"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeVbs}"; WorkingDir: "{app}"; IconFilename: "{app}\dashboard\favicon.ico"; Comment: "Start DATA (runs in the background)"; Tasks: desktopicon

[Run]
; Install the bundled DATA-core skills using the embedded runtime (idempotent).
Filename: "{app}\runtime\python\python.exe"; Parameters: """{app}\dashboard\install_skills.py"""; WorkingDir: "{app}"; StatusMsg: "Installing DATA-core skills..."; Flags: runhidden skipifdoesntexist
; Optional: install the selected AI provider CLIs (Node.js + any of Claude Code
; / Codex / Gemini). Runs once with the chosen set, only if at least one ticked.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{tmp}\setup_provider.ps1"" -Clis ""{code:GetSelectedClis}"""; StatusMsg: "Installing the selected AI provider CLIs (Node.js + CLIs)... this can take a minute."; Flags: waituntilterminated; Check: AnyProviderSelected
; Offer to launch DATA at the end.
Filename: "{app}\{#AppExeVbs}"; Description: "Launch {#AppName} now"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Make sure DATA isn't running (frees its ports) before files are removed.
Filename: "{app}\stop_data.bat"; Flags: runhidden; RunOnceId: "StopData"

[UninstallDelete]
; Remove runtime state the app created at runtime (not tracked by [Files]).
Type: filesandordirs; Name: "{app}\runtime\python\Lib\site-packages\__pycache__"
Type: files; Name: "{app}\bridge.log"
Type: files; Name: "{app}\start_data.bat"
Type: files; Name: "{app}\start_data.vbs"
Type: files; Name: "{app}\stop_data.bat"

[Code]
{ Seed .env and .mcp.json from their .example templates on first install only —
  never clobber an existing one. .mcp.json registers the clawdcursor desktop-
  takeover MCP server (which stays disarmed until armed in Settings). }
procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvPath, ExamplePath, McpPath, McpExamplePath: string;
begin
  if CurStep = ssPostInstall then
  begin
    EnvPath     := ExpandConstant('{app}\.env');
    ExamplePath := ExpandConstant('{app}\.env.example');
    if (not FileExists(EnvPath)) and FileExists(ExamplePath) then
      CopyFile(ExamplePath, EnvPath, False);

    McpPath        := ExpandConstant('{app}\.mcp.json');
    McpExamplePath := ExpandConstant('{app}\.mcp.json.example');
    if (not FileExists(McpPath)) and FileExists(McpExamplePath) then
      CopyFile(McpExamplePath, McpPath, False);
  end;
end;

{ Build the -Clis argument for setup_provider.ps1 from the ticked provider tasks.
  Returns e.g. "claude,codex" — empty string if none (the [Run] Check gates that). }
function GetSelectedClis(Param: string): string;
var
  s: string;
begin
  s := '';
  if WizardIsTaskSelected('provider')        then s := s + 'claude,';
  if WizardIsTaskSelected('provider_codex')  then s := s + 'codex,';
  if WizardIsTaskSelected('provider_gemini') then s := s + 'gemini,';
  if (Length(s) > 0) and (s[Length(s)] = ',') then s := Copy(s, 1, Length(s) - 1);
  Result := s;
end;

{ True when at least one provider CLI task is ticked — gates the install [Run]. }
function AnyProviderSelected(): Boolean;
begin
  Result := WizardIsTaskSelected('provider')
         or WizardIsTaskSelected('provider_codex')
         or WizardIsTaskSelected('provider_gemini');
end;

procedure InitializeWizard();
begin
  { setup_provider.ps1 is flagged dontcopy; extract it so the [Run] entry can call it. }
  ExtractTemporaryFile('setup_provider.ps1');
end;
