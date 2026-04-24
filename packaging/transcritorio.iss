; Inno Setup script for Transcritorio
; Requires Inno Setup 6.2+
;
; Build with:
;   ISCC.exe /DBundleDir=C:\path\to\dist\Transcritorio packaging\transcritorio.iss
;
; Aceleracao NVIDIA (opcional):
;   - Bundle base e sempre instalado (variant=cpu, ~1.5 GB).
;   - Aceleracao CUDA e oferecida DENTRO do app no primeiro launch com
;     barra de progresso nativa, via transcribe_pipeline/cuda_installer.py.
;   - Uninstall pergunta se deve apagar tambem os ~13 GB de modelos em
;     %LOCALAPPDATA%\Transcritorio (default Nao, pra preservar upgrades).

#ifndef BundleDir
  #define BundleDir "..\dist\Transcritorio"
#endif

#define AppName      "Transcritorio"
#define AppVersion   "0.1.2"
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
ChangesAssociations=yes
; Single monolithic .exe (no DiskSpanning). Bundle ~600 MB fits comfortably
; under GitHub Release's 2 GB asset limit and avoids the two-files-in-same-folder
; gotcha for end users. If the bundle ever grows past ~1.8 GB, reenable
; DiskSpanning=yes and bump the site OsSwitcher to trigger both downloads.
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
; CLI no PATH e opt-out: default checado, usuario que nao quer desmarca.
; A maioria dos usuarios se beneficia (abrir terminal e digitar
; transcritorio-cli <comando> funciona direto), e quem nao usa CLI nao
; sofre com o item checado — so ignora. Reduz atrito inicial.
Name: "addtopath"; Description: "Adicionar CLI ao PATH do usuario"; GroupDescription: "Opcoes avancadas:"

[Files]
; Bundle PyInstaller (CPU-only; aceleracao NVIDIA vem pelo app no 1o launch)
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Launch apos a instalacao. Aceleracao NVIDIA e instalada DENTRO do app
; no 1o start (via transcribe_pipeline/cuda_installer.py), nao aqui.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Registry]
; Associate .transcritorio files with the application
Root: HKA; Subkey: "Software\Classes\.transcritorio\OpenWithProgids"; \
    ValueType: string; ValueName: "Transcritorio.ProjectFile"; ValueData: ""; \
    Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Transcritorio.ProjectFile"; \
    ValueType: string; ValueName: ""; ValueData: "Projeto Transcritorio"; \
    Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Transcritorio.ProjectFile\DefaultIcon"; \
    ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKA; Subkey: "Software\Classes\Transcritorio.ProjectFile\shell\open\command"; \
    ValueType: string; ValueName: ""; \
    ValueData: """{app}\{#AppExeName}"" ""%1"""
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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
    AppData: string;
    Response: Integer;
begin
    // Apos desinstalar os arquivos do {app}, pergunta se tambem deve apagar
    // os modelos de IA e cache em %LOCALAPPDATA%\Transcritorio (~13 GB).
    // Default e NAO, pra preservar modelos em upgrades.
    if CurUninstallStep = usPostUninstall then
    begin
        AppData := ExpandConstant('{localappdata}\Transcritorio');
        if DirExists(AppData) then
        begin
            Response := MsgBox(
                'Remover tambem os modelos de IA e o cache do Transcritorio em:' + #13#10 +
                AppData + #13#10 + #13#10 +
                'Sao ~13 GB de arquivos baixados (Whisper, pyannote, wav2vec2).' + #13#10 + #13#10 +
                'Escolha SIM se esta desinstalando em definitivo.' + #13#10 +
                'Escolha NAO se for reinstalar e quiser reaproveitar os modelos.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
            if Response = IDYES then
                DelTree(AppData, True, True, True);
        end;
    end;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
