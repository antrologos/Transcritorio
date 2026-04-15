; Inno Setup script for Transcritorio
; Requires Inno Setup 6.2+ (DiskSpanning support)
;
; Build with:
;   ISCC.exe /DBundleDir=C:\path\to\dist\Transcritorio packaging\transcritorio.iss

#ifndef BundleDir
  #define BundleDir "..\dist\Transcritorio"
#endif

#define AppName      "Transcritorio"
#define AppVersion   "0.1.0"
#define AppPublisher "Rogerio Jeronimo Barbosa"
#define AppURL       "https://github.com/antrologos/Transcritorio"
#define AppExeName   "Transcritorio.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-TRANSCRITORIO}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Large bundle: enable disk spanning and best compression
DiskSpanning=yes
Compression=lzma2/ultra64
SolidCompression=yes
; Output — override with ISCC /O flag or build.ps1 sets it automatically.
OutputDir={#BundleDir}\..\installer
OutputBaseFilename=Transcritorio-{#AppVersion}-Setup
; Icons
; SetupIconFile requires antivirus exclusion; use default icon for now
; SetupIconFile=..\assets\transcritorio_icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
; Windows 10 1809+ (October 2018) required
MinVersion=10.0.17763
; 64-bit only
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install (no admin needed), with option to elevate
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Modern wizard style
WizardStyle=modern

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Adicionar CLI ao PATH do usuario"; GroupDescription: "Opcoes avancadas:"; Flags: unchecked

[Files]
; Bundle the entire PyInstaller output directory
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Registry]
; Add CLI to user PATH if selected
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath('{app}')

[Code]
function NeedsAddPath(Param: string): boolean;
var
    OrigPath: string;
begin
    if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then
    begin
        Result := True;
        exit;
    end;
    Result := Pos(';' + UpperCase(Param) + ';', ';' + UpperCase(OrigPath) + ';') = 0;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
