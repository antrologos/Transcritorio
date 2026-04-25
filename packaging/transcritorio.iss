; Inno Setup script for Transcritorio
; Requires Inno Setup 6.4+ (DownloadTemporaryFile)
;
; Build with:
;   ISCC.exe /DBundleDir=C:\path\to\dist\Transcritorio packaging\transcritorio.iss
;
; Aceleracao NVIDIA (opcional):
;   - Bundle base e sempre instalado (variant=cpu, ~1.5 GB).
;   - Setup detecta NVIDIA via nvidia-smi durante o install e oferece checkbox
;     "Acelerar com placa NVIDIA". Se marcado, baixa o cuda_pack zip da release
;     do GitHub e extrai com tar.exe (Win10 1803+) — tudo dentro do escopo do
;     installer (admin OK se "Para todos"). Default DESMARCADO (consent explicito).
;   - Se download falhar, install completa sem CUDA e o app oferece baixar no
;     primeiro launch via transcribe_pipeline/cuda_installer.py (fallback).
;   - Smoke CI: /SKIPCUDA=1 pula a oferta (runner do GitHub nao tem GPU).
;   - Uninstall pergunta se deve apagar tambem os ~13 GB de modelos em
;     %LOCALAPPDATA%\Transcritorio (default Nao, pra preservar upgrades).

#ifndef BundleDir
  #define BundleDir "..\dist\Transcritorio"
#endif

#define AppName      "Transcritorio"
#define AppVersion   "0.1.6"
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
; Fecha automaticamente o app rodando (default era 'yes' que pergunta).
; Necessario porque o /VERYSILENT do CI gate ficaria pendurado em prompt.
CloseApplications=force
RestartApplications=no

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

[InstallDelete]
; Antes de copiar arquivos novos, apaga DLLs CUDA "lazy-load" (do cuda_pack)
; de instalacoes anteriores. Importante em upgrade v0.1.6 -> v0.1.7+ pra
; evitar mistura ABI: torch_cuda v0.1.7 com cudnn_ops.dll v0.1.6 pode dar
; cudnn error. Se o user marcar "Acelerar com NVIDIA" novamente, o Setup
; baixa cuda_pack atual; senao o app oferece no primeiro launch.
; Lista espelha CUDA_DLL_EXCLUDES_CPU_EXTRA em packaging/bundle_filter.py.
Type: files; Name: "{app}\_internal\torch\lib\cudnn_adv*"
Type: files; Name: "{app}\_internal\torch\lib\cudnn_cnn*"
Type: files; Name: "{app}\_internal\torch\lib\cudnn_engines_*"
Type: files; Name: "{app}\_internal\torch\lib\cudnn_graph*"
Type: files; Name: "{app}\_internal\torch\lib\cudnn_heuristic*"
Type: files; Name: "{app}\_internal\torch\lib\cudnn_ops*"
Type: files; Name: "{app}\_internal\torch\lib\caffe2_nvrtc*"
Type: files; Name: "{app}\_internal\torch\lib\cufftw*"
Type: files; Name: "{app}\_internal\torch\lib\curand*"
Type: files; Name: "{app}\_internal\torch\lib\cusolverMg*"
Type: files; Name: "{app}\_internal\torch\lib\nvrtc*"

[Files]
; Bundle PyInstaller base (~1.6 GB). Aceleracao NVIDIA e baixada pelo
; [Code] em ssPostInstall via DownloadTemporaryFile, se user marcar.
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
// ============================================================================
// Aceleracao NVIDIA on-demand (v0.1.7+)
// ============================================================================
//
// Fluxo:
//  1) HasNvidiaGPU() roda nvidia-smi -L durante init (cacheado).
//  2) Se True, InitializeWizard cria pagina com checkbox DESMARCADO por default.
//  3) ShouldSkipPage pula a pagina se sem NVIDIA (user nao percebe).
//  4) CurStepChanged(ssPostInstall) baixa+extrai se checkbox marcado.
//  5) Falha de download = log + MsgBox amigavel; install continua sem CUDA.
//
// Flag /SKIPCUDA=1: pula download mesmo com NVIDIA (usado pelo CI smoke).

var
  CudaPage: TInputOptionWizardPage;
  GpuChecked: Boolean;
  HasNvidia: Boolean;

function HasNvidiaGPU(): Boolean;
var
  ResultCode: Integer;
begin
  if GpuChecked then
  begin
    Result := HasNvidia;
    Exit;
  end;
  GpuChecked := True;
  // SW_HIDE evita flash de console preto. Exec retorna False se nvidia-smi.exe
  // nao existe no PATH (driver NVIDIA nao instalado). ResultCode=0 = OK.
  HasNvidia := Exec('nvidia-smi.exe', '-L', '', SW_HIDE,
                    ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  Result := HasNvidia;
end;

procedure InitializeWizard();
begin
  CudaPage := CreateInputOptionPage(wpSelectDir,
    'Aceleracao NVIDIA (opcional)',
    'Suporte para placas de video NVIDIA (GeForce/RTX)',
    'Detectamos uma placa de video NVIDIA. Com ela, as transcricoes ficam' + #13#10 +
    '5 a 10 vezes mais rapidas.' + #13#10 + #13#10 +
    'Para ativar, e preciso baixar um componente adicional de cerca de' + #13#10 +
    '890 MB. Voce pode fazer isso agora ou depois, pelo proprio Transcritorio.' + #13#10 + #13#10 +
    'Se voce nao marcar, o aplicativo perguntara novamente na primeira abertura.',
    True, False);
  CudaPage.Add('Acelerar transcricoes com minha placa NVIDIA (baixa ~890 MB)');
  // Default DESMARCADO: 890 MB sem consent explicito viola principio de
  // "no surprises". Quem quer marca; quem nao sabe, app oferece depois.
  CudaPage.Values[0] := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // Pula a pagina inteira se nao tem NVIDIA — nao confundir user CPU-only.
  Result := (PageID = CudaPage.ID) and (not HasNvidiaGPU());
end;

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

procedure DoCudaInstall();
var
  Url, ZipPath, TarExe, ExtractDir, ErrMsg, LogFile, LogLine: String;
  ResultCode: Integer;
begin
  Url := 'https://github.com/antrologos/Transcritorio/releases/download/v' +
         '{#AppVersion}' + '/transcritorio-cuda-pack-' + '{#AppVersion}' + '-win64.zip';
  ZipPath := ExpandConstant('{tmp}\cuda_pack.zip');
  ExtractDir := ExpandConstant('{app}');
  TarExe := ExpandConstant('{sys}\tar.exe');
  LogFile := ExpandConstant('{app}\cuda_install.log');

  WizardForm.StatusLabel.Caption := 'Baixando suporte NVIDIA (~890 MB)...';
  try
    // DownloadTemporaryFile (Inno 6.4+) usa a barra de progresso nativa do
    // wizard. Segue redirects HTTP 302 (GitHub Release -> S3). Sem SHA-256
    // (param 3 vazio): aceitamos risco de truncamento — fallback do app cobre.
    DownloadTemporaryFile(Url, 'cuda_pack.zip', '', nil);
    // Arquivo agora em {tmp}\cuda_pack.zip.

    WizardForm.StatusLabel.Caption := 'Preparando aceleracao NVIDIA...';
    // tar.exe nativo do Win10 1803+ extrai .zip via libarchive. -C muda dir
    // destino. Path com aspas pra suportar espacos ("C:\Program Files\...").
    if not Exec(TarExe, '-xf "' + ZipPath + '" -C "' + ExtractDir + '"',
                '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      RaiseException('Falha ao iniciar tar.exe (extracao do zip)');
    if ResultCode <> 0 then
      RaiseException('tar.exe retornou codigo ' + IntToStr(ResultCode));
    // Sucesso silencioso: as 14 DLLs do cuda_pack agora em _internal\torch\lib\.
  except
    ErrMsg := GetExceptionMessage();
    LogLine := '[' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '] ' +
               'CUDA install falhou: ' + ErrMsg + #13#10;
    SaveStringToFile(LogFile, LogLine, True);
    if not WizardSilent() then
      MsgBox(
        'Nao foi possivel baixar o componente de aceleracao NVIDIA agora.' + #13#10#13#10 +
        'A conexao falhou ou foi interrompida. O Transcritorio vai funcionar' + #13#10 +
        'normalmente. Quando voce abrir o aplicativo pela primeira vez, ele' + #13#10 +
        'vai oferecer baixar de novo.',
        mbInformation, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  SkipCuda: String;
begin
  // ssPostInstall = depois do [Files] copiar bundle base. cuda_pack mescla
  // suas 14 DLLs em {app}\_internal\torch\lib\ que ja existe.
  if CurStep = ssPostInstall then
  begin
    // Flag /SKIPCUDA=1 do CI smoke: pula download (runner sem GPU).
    SkipCuda := ExpandConstant('{param:skipcuda|0}');
    if (SkipCuda = '0') and HasNvidiaGPU() and (CudaPage <> nil)
       and CudaPage.Values[0] then
      DoCudaInstall();
  end;
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
