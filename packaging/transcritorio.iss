; Inno Setup script for Transcritorio
; Requires Inno Setup 6.2+ (DiskSpanning support)
;
; Build with:
;   ISCC.exe /DBundleDir=C:\path\to\dist\Transcritorio packaging\transcritorio.iss
;
; CUDA opcional (0.1.1+):
;   - Bundle base e sempre instalado (variant=cpu, ~1.5 GB)
;   - Componente "cuda" baixa transcritorio-cuda-pack-{version}-win64.zip
;     do GitHub Release v{version} via curl.exe + extrai via PowerShell
;     Expand-Archive. Default: checked se nvidia-smi detecta NVIDIA;
;     unchecked caso contrario.

#ifndef BundleDir
  #define BundleDir "..\dist\Transcritorio"
#endif

#define AppName      "Transcritorio"
#define AppVersion   "0.1.1"
#define AppPublisher "Rogerio Jeronimo Barbosa"
#define AppURL       "https://github.com/antrologos/Transcritorio"
#define AppExeName   "Transcritorio.exe"
#define CudaPackFile "transcritorio-cuda-pack-" + AppVersion + "-win64.zip"
#define CudaPackUrl  "https://github.com/antrologos/Transcritorio/releases/download/v" + AppVersion + "/" + CudaPackFile

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

[Types]
Name: "full"; Description: "Completa (com aceleracao se o computador tem placa NVIDIA)"
Name: "compact"; Description: "Basica (so CPU, menor, sem download extra)"
Name: "custom"; Description: "Personalizada"; Flags: iscustom

[Components]
Name: "core"; Description: "Transcritorio (obrigatorio, ~1.5 GB)"; Types: full compact custom; Flags: fixed
Name: "cuda"; Description: "Aceleracao para placas graficas NVIDIA (baixa ~1 GB no final da instalacao)"; Types: full

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Adicionar CLI ao PATH do usuario"; GroupDescription: "Opcoes avancadas:"; Flags: unchecked

[Files]
; Bundle PyInstaller (ja sem CUDA apos split_bundle.py)
Source: "{#BundleDir}\*"; DestDir: "{app}"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Baixar e extrair cuda_pack se o componente cuda foi selecionado.
; curl.exe vem com Windows 10 1803+; PowerShell 5.1+ ships com Expand-Archive.
Filename: "{cmd}"; Parameters: "/c curl.exe -L --fail -o ""{tmp}\{#CudaPackFile}"" ""{#CudaPackUrl}"""; \
    StatusMsg: "Baixando aceleracao NVIDIA (~1 GB)..."; \
    Components: cuda; Flags: runhidden
Filename: "{cmd}"; Parameters: "/c powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ""Expand-Archive -Path '{tmp}\{#CudaPackFile}' -DestinationPath '{app}' -Force"""; \
    StatusMsg: "Instalando aceleracao NVIDIA..."; \
    Components: cuda; Flags: runhidden
; Launch apos a instalacao (ultimo)
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

function HasNvidiaGpu(): Boolean;
var
    ResultCode: Integer;
begin
    // nvidia-smi.exe vem com o driver NVIDIA. Retorna 0 se ha placa NVIDIA.
    // Redireciona stdout/stderr pra NUL via cmd /c pra nao mostrar janela.
    Result := Exec(ExpandConstant('{cmd}'), '/c nvidia-smi >NUL 2>&1', '',
                   SW_HIDE, ewWaitUntilTerminated, ResultCode)
              and (ResultCode = 0);
end;

procedure InitializeWizard();
var
    CudaIndex: Integer;
begin
    // Ajusta default do componente 'cuda' conforme presenca de NVIDIA.
    // Se nao ha NVIDIA: desmarca (user pode marcar manualmente se quiser)
    // Se ha NVIDIA: deixa marcado (comportamento 'full' type)
    CudaIndex := WizardForm.ComponentsList.Items.IndexOf('Aceleracao para placas graficas NVIDIA (baixa ~1 GB no final da instalacao)');
    if CudaIndex >= 0 then
    begin
        WizardForm.ComponentsList.Checked[CudaIndex] := HasNvidiaGpu();
    end;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
