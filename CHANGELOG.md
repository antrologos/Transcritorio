# Changelog

## Não lançado (v0.2 em preparação)

**Mudança de canal de distribuição**: o standalone (Setup.exe/.dmg/AppImage)
foi descontinuado — antivírus/SmartScreen bloqueavam a instalação sem
assinatura digital, e o SignPath recusou a assinatura gratuita. O canal
oficial passa a ser **PyPI + uv** (`uv tool install transcritorio`); ver
`docs/INSTALL_WINDOWS.md` e `docs/LEGACY_STANDALONE.md`.

- Verificação acústica de trocas de falante (novo passo pós-render,
  `check-boundaries`): compara a voz dos dois lados de cada troca com o
  modelo de embedding da diarização (benchmark: AUC 0,961) e marca com
  "Dúvida" os blocos em que a voz é a mesma dos dois lados (divisão
  provavelmente errada); marca com "Sobreposição" blocos majoritariamente
  cobertos por falas simultâneas. Banner "N trocas de falante com vozes
  parecidas" e tooltip explicativo na coluna Marcações do Estúdio.
  Configurável (`boundary_check`, limiar e janela em `run_config.yaml`).
- Captura de sinais da diarização (`diar_signals`, hook do pyannote): a
  diarização agora persiste derivados compactos em
  `03_diarization/signals/` — silêncios e sobreposições segundo o próprio
  modelo, e a margem de confiança da voz atribuída a cada trecho. A
  verificação pós-render usa esses sinais para marcar blocos de voz
  incerta (margem negativa = o modelo prefere outra voz) e melhorar a
  detecção de sobreposição; arquivos antigos caem no fallback por
  divergência regular×exclusive. Desligável
  (`diarization_capture_signals`); falha na captura nunca interrompe a
  diarização.
- Segurança: "Enviar para Lixeira" (Del) passa a mirar SOMENTE os
  arquivos selecionados na lista (destaque) — as caixas de marcação, que
  escolhem o que transcrever e vêm marcadas por padrão, não contam mais
  como alvo de deleção (um Del com tudo marcado mandava o projeto inteiro
  para a lixeira). Os diálogos de confirmação e de purga agora LISTAM os
  nomes dos arquivos afetados, não só a contagem.
- O fim de uma transcrição agora renova a mídia do player: quem abria o
  arquivo antes de transcrever ficava preso ao áudio original (MP3 com
  seek impreciso) no player e no diálogo de vozes — causa da
  dessincronia crescente relatada em uso real.
- Correção GRAVE de sincronia áudio×texto no Estúdio: o player abria o
  arquivo ORIGINAL (MP3/M4A, seek impreciso em VBR com desvio que cresce
  ao longo do arquivo) enquanto waveform e timestamps referem-se ao WAV
  preparado. Agora o player abre o WAV por padrão (vídeos continuam no
  original, pelo painel de imagem) e todos os seeks ganharam a
  confirmação anti-descarte do Windows (mesmo fix do diálogo de vozes).
  Probe: desvio ≤ 29 ms em posições até 38 min.
- Banner de trocas de falante integrado: navegação ‹ › pelos blocos
  marcados (no lugar de "Ver primeira"), contagem que atualiza ao
  desmarcar Dúvida (e o banner some ao zerar), e a explicação da
  marcação agora aparece no painel do editor — não só como tooltip.
- Busca nas transcrições (fase 2.3): **Ctrl+F** filtra os blocos do
  arquivo aberto em tempo real (barra estilo navegador, ‹ › navegam, Esc
  fecha); **Ctrl+Shift+F** (ou Editar → Buscar nas transcrições) abre a
  busca do projeto inteiro com duas seções — "Resultados exatos" e
  "Trechos com sentido parecido" (encoder multilíngue local pequeno, roda
  em CPU, sem GPU e sem conta) — e clique no resultado abre a entrevista
  no bloco certo com o áudio posicionado. Índice por arquivo em
  `07_index/`, atualizado sozinho quando a transcrição muda; preparo e
  download do modelo (~0,5 GB, uma vez) oferecidos no próprio diálogo.
  CLI: `transcritorio-cli search --query "..."`.
- Botão contextual "Gerar resumo com temas" na área do arquivo aberto
  (par com "Abrir resumo com temas": sem resumo → gerar; com resumo →
  abrir).
- Infra da análise local (fase 2.0 do plano-programa): módulo
  `research_context` (contexto de pesquisa por projeto — roteiro,
  codebook, nomes conhecidos — grounding das ferramentas de análise);
  módulo `llm_env` (ambiente dedicado criado sob demanda via uv para o
  Qwen3.5-4B + GLiNER — separado porque o transformers>=5.13 exigido pelo
  Qwen é irreconciliável com o pin de huggingface-hub do whisperx);
  categoria `_OPTIONAL_MODELS` no gerenciador de modelos (Qwen3.5-4B e
  GLiNER multi-PII com SHAs pinadas, fora dos obrigatórios e protegidos
  da limpeza de órfãos). Modelo escolhido por julgamento às cegas do
  usuário + benchmark com gabarito real (PoC 2026-08-25).
- Segurança de dados: "Melhorar falantes deste arquivo" (e o "Tentar
  novamente" do banner) agora guarda automaticamente uma cópia de
  segurança da revisão em `05_transcripts_review/edits/backups/` antes de
  recriá-la — antes, todas as edições manuais eram descartadas sem aviso
  fiel e sem backup. O diálogo de confirmação passou a dizer a verdade.
- Defaults seguros: `asr_device/compute_type/batch_size: auto` — CPU usa
  int8/batch 2 (float16 em CPU era convertido p/ float32 pelo CTranslate2,
  ~2x RAM: travava máquinas sem GPU); float16 explícito em CPU é coagido.
- Diarização opcional de ponta a ponta: quem só quer transcrever não cria
  conta HF nem token; escolha no wizard, persistida para projetos novos;
  falha de diarização não derruba mais o lote (render segue sem falantes).
- Diarização roda em subprocesso (`transcritorio-cli diarize
  --progress-json`): crash de pyannote/torch/CUDA não fecha mais a GUI.
- Aceleração NVIDIA como extra `[cuda]` (menu do app); reparo/atualização
  via uv no menu Ajuda; atalho de área de trabalho criado no 1º run;
  aviso discreto de versão nova via PyPI.
- Correções do diagnóstico 2026-08-23 (101 findings triplo-verificados):
  lixeira nunca mais apaga a única cópia de originais, guards de job
  ativos, globs por id sem colisão de prefixo, parser YAML preserva '#',
  token_vault nunca crasha, release não publica com build falho, e ~40
  outros fixes (ver mensagens dos commits de 2026-08-23).
- Qualidade da separação de falantes (4 correções, 2026-08-23/24): os
  metadados por arquivo ("Aplicar falantes", ex.: grupo focal com 6 vozes)
  passam a valer na rota CLI/GUI; o pós-processamento da camada exclusiva
  não atropela mais apartes curtos (padrão A-B-A); o corte palavra→falante
  usa critério acústico (palavra de borda segue o turno vizinho;
  interjeições reais são preservadas); hiperparâmetros de clustering
  aplicam-se por chave (`diarization_fa/fb` eram inertes).
- **"De quem é esta voz?"**: ao fim de cada transcrição (e via banner na
  revisão), o app toca amostras de cada voz — com timestamp e prévia do
  texto — e pede o nome, que vale para a transcrição inteira; confirmação
  registrada por arquivo; opção por projeto de não perguntar (lote).
- Reconhecimento local de vozes recorrentes: as vozes confirmadas em 2+
  arquivos viram âncoras do projeto (embeddings já calculados pela
  diarização, agora persistidos) e os arquivos seguintes abrem com
  "parece ser X" pré-preenchido — sempre confirmável, nunca automático.
- Grupo focal como caso de primeira classe: ao transcrever, o app pergunta
  "Quantas pessoas falam?" (presets entrevista/grupo focal/exato/auto, uma
  vez por lote); cores estáveis por voz na tabela de blocos; rótulos
  default "Moderador/Participante N".
- Partida com feedback e instância única: splash imediato ao abrir e o
  segundo clique no ícone traz a janela existente (nunca mais duas janelas);
  campo Falante editável com "Aplicar a todos desta voz".
- Player das amostras: toca o WAV preparado (timeline exata do pipeline) e
  contorna o descarte silencioso de seek do backend de mídia do Windows.
- Beta público do canal novo: release `beta-0.2.0b1` (wheel instalável por
  URL com `uv tool install`) para testes em máquinas reais antes da v0.2.0.

## 0.1.8 — 2026-04-27

UX baseada em feedback da Denise (1ª usuária externa, instalou v0.1.7 e
transcreveu sua primeira entrevista de 1h36 em ~10 min com aceleração CUDA).
Três pequenos atritos identificados, todos corrigidos:

### 1. Auto-retry no download dos modelos HF

`_manual_snapshot_download` em `transcribe_pipeline/model_manager.py` agora
faz retry automático per-blob com backoff exponencial (5 tentativas, esperas
0/2/4/8/16s). Em conexão flaky (caso reportado: erro a ~40% do download
recorrente), o sistema retenta sozinho até concluir; só levanta exception
para a UI se as 5 tentativas falharem. Antes, cada falha exigia clique
manual em "Voltar"+"Próximo" — Denise teve que repetir 4-5 vezes.

UI (`review_studio_qt.py`) reconhece o evento novo `model_download_retry`
e mostra na label do wizard: `"Reconectando em 4s (3/5) — falha: ConnectionError"`.

Toy test novo: `tests/toy_manual_snapshot_retry.py` (3 cenários:
3-falhas+sucesso, 5-falhas+desiste, caminho-feliz).

### 2. Label informativa durante extração lzma2

`packaging/transcritorio.iss` ganha `CurStepChanged(ssInstall)` que escreve
no `WizardForm.StatusLabel.Caption`:

> Extraindo arquivos do Transcritório (~1.6 GB) — pode levar 5 a 15 minutos.
> NÃO cancele se a barra parecer travada em 99% — é normal nesta fase.

Caso real: Denise cancelou achando que o install pendurou em 99% (o que
pareceu inativo na realidade era a fase final de descompressão lzma2/ultra64).
A label é estática (Inno não emite callback granular durante extração),
mas previne pânico.

### 3. README seção "Avisos de antivírus / SmartScreen"

Adicionada seção em `README.md` explicando que o Transcritório é unsigned
(code signing está em backlog em `docs/WINDOWS_CODE_SIGNING.md`) e que
avisos de AVAST/Norton/Kaspersky/SmartScreen são **falsos positivos
genéricos de unsigned binary**, não malware. Inclui instruções práticas:
"Mais informações → Executar assim mesmo", adicionar exceção AV pra
`Transcritorio.exe` e `whisperx.exe`, link pro doc de code signing.

Mantém também a nota sobre instalação ficar em "99%" por minutos.

## 0.1.7 — 2026-04-25

Duas mudancas combinadas: integracao do cuda_pack no instalador Windows
+ correcao de UI no header `Motor:`.

### Setup.exe baixa cuda_pack durante o install (request do user)

Antes: bundle base instalava em `C:\Program Files\Transcritorio\` (admin),
e o cuda_pack era baixado no first-launch via dialog Python. Mas se user
escolhia "Para todos", o dialog batia em "Permissao insuficiente" pra
escrever em Program Files (precisa elevar de novo). Pior UX.

Agora: o proprio Setup.exe (que ja tem admin se elevado) detecta NVIDIA
durante a instalacao via `nvidia-smi -L` e oferece um checkbox no wizard
("Acelerar transcricoes com minha placa NVIDIA, baixa ~890 MB"). Se
marcado, o installer baixa o `transcritorio-cuda-pack-{VERSION}-win64.zip`
da release do GitHub via `DownloadTemporaryFile` (Inno 6.4+) e extrai
com `tar.exe` (Win10 1803+).

Vantagens:
- Sem erro de permissao em "Para todos" (admin do install cobre).
- Auto-detect: pagina pula se nao tem NVIDIA (user CPU-only nao percebe).
- Default DESMARCADO: nao baixa 890 MB sem consent explicito.
- Fallback duplo: se download falha (offline, timeout), MsgBox amigavel
  e o app oferece de novo no first-launch via cuda_installer.py.
- `[InstallDelete]` apaga DLLs CUDA antigas antes do `[Files]` — evita
  ABI mismatch no upgrade v0.1.6 -> v0.1.7+.
- `[Setup] CloseApplications=force`: fecha app rodando sem prompt.

Gates CI novos em `.github/workflows/release.yml`:
- "Verify tag matches AppVersion": valida que push de tag v0.1.7 casa com
  `AppVersion="0.1.7"` no .iss. Sem isso, URL do cuda_pack apontaria pra
  release errada (404 silencioso).
- "Setup.exe smoke (silent install)": roda Setup.exe pos-compile com
  `/VERYSILENT /SKIPCUDA=1 /CURRENTUSER` em `$RUNNER_TEMP`. Pega erro
  Pascal silencioso. Runner do GitHub nao tem GPU, entao `/SKIPCUDA=1`
  forca o branch CPU; caminho do download real e validado manualmente
  antes de cada tag publica.

### Header `Motor:` respeita `asr_device=cpu` forcado

Bug: ao mudar Motor para CPU em "Configurar transcricao", header continuava
mostrando "Motor: CUDA (NVIDIA)" porque `runtime.describe_backend()` chamava
`detect_device()` que cacheia o resultado e ignora a config do user. O
pipeline real ja usava CPU corretamente (via `resolve_device`), so o header
mentia.

Fix cirurgico: `describe_backend()` aceita `configured_device` opcional.
Override SO quando `cfg == "cpu"` (user forcou). Outros valores (auto, cuda,
mps, None) caem em `detect_device()`, preservando branch MLX em Apple
Silicon (chamar `resolve_device("mps")` coage para cpu e mataria MLX).

`review_studio_qt::project_header_text` passa `config["asr_device"]` ao
chamar `describe_backend`.

Toy tests: 3 casos novos validando override CPU + preservacao auto-detect.

## 0.1.6 — 2026-04-25

Quarto bug pre-existente da serie, exposto agora que v0.1.5 chega ao
ponto de transcribe real do usuario:

```
LocalEntryNotFoundError: Cannot find an appropriate cached snapshot
folder for the specified revision on the local disk
```

### Root cause

`faster_whisper==1.2.1` em `utils.py:11` define o registro `_MODELS`:
```python
"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
```

Mas Transcritorio em `model_manager.ASR_VARIANTS["large-v3-turbo"]["repo"]`
usa `dropbox-dash/faster-whisper-large-v3-turbo` (mudanca em 2026-04-22
para eliminar redirect HTTP 307 que travava `snapshot_download` no bundle
frozen).

O wizard baixa em `models--dropbox-dash--faster-whisper-large-v3-turbo/`,
mas `whisperx --model large-v3-turbo --model_cache_only True` chama
`faster_whisper.WhisperModel(model_size_or_path="large-v3-turbo", ...)`
que resolve via `_MODELS` para `mobiuslabsgmbh/...` e procura no diretorio
errado.

### Fix

- `transcribe_pipeline/model_manager.py`: nova funcao `resolve_asr_repo()`
  que retorna o repo_id completo (e.g. `dropbox-dash/...`) em vez do
  shortcut. Faster_whisper aceita repo_id direto e procura no path certo
  do cache.
- `transcribe_pipeline/whisperx_runner.py`: passa `effective_repo` em
  `--model` em vez de `effective_model` (shortcut).

Ambas mudancas usam apenas o repo_id que o proprio wizard ja salvou no
cache, entao nao requer redownload e nao depende de internet em runtime.

### Bug latente analogo

`mlx_whisper_runner.py:45-46` tem seu proprio mapeamento:
```python
"large-v3-turbo": "mlx-community/whisper-large-v3-turbo"
```

Esse caminho e usado so em Mac com mlx-whisper instalado, e o repo
mlx-community e diferente (modelo MLX, nao faster-whisper). Mantido
como esta — bug nao se aplica ao path MLX.

## 0.1.5 — 2026-04-25

Fix do gate CI introduzido em v0.1.3. v0.1.4 falhou nos 3 SOs com
`LocalEntryNotFoundError` no whisperx — o gate pedia `--model_cache_only
True` mas o cache do tiny estava em tempdir do smoke-test (ja deletado).
Removido `--model_cache_only`: o whisperx baixa o tiny online (~10s) se
necessario, e em troca o gate fica independente de coordenacao de cache
entre steps. Bug do bundle nao mudou — torchvision e torchcodec ja foram
resolvidos em v0.1.3 e v0.1.4.

## 0.1.4 — 2026-04-24

Segundo bug latente pego pelo gate CI introduzido em v0.1.3: apos resolver
o `PackageNotFoundError: torchcodec`, o gate revelou que `whisperx` tambem
quebrava com `ModuleNotFoundError: No module named 'torchvision'` antes de
qualquer transcribe.

### Root cause

`torchmetrics.functional.image.arniqa.py:31` importa `torchvision` no topo
do modulo. Esse arquivo e carregado eagerly via:
```
whisperx -> asr -> vads/pyannote -> pyannote.audio -> lightning -> torchmetrics
```
O spec.py de v0.1.x explicitamente excluia `torchvision` do bundle por
ele "nao ser usado" — comentario incorreto, ele e usado transitivamente.

### Fix

- `packaging/transcritorio.spec`:
  - Removido `"torchvision"` do `excludes`.
  - Adicionado `torchvision` ao loop de `collect_submodules`.
- `.github/workflows/release.yml`:
  - Linux job instala `torchvision==0.23.0 --index-url whl/cpu`.
  - Mac job instala `torchvision==0.23.0` (default arm64+CPU/MPS wheel).
  - Windows ja tinha `torchvision==0.23.0` no install com `whl/cu128`.

### Como o bug nao apareceu antes

Mesmo padrao do bug torchcodec da v0.1.3: o CI nunca invocava `whisperx.exe`
contra audio real. v0.1.3 introduziu o gate que executa o binario contra
3s de silencio offline; foi ele que pegou os DOIS bugs em sequencia.

## 0.1.3 — 2026-04-24

Correcao de bug critico em **todas as plataformas**: `whisperx.exe` crashava
no primeiro transcribe com `PackageNotFoundError: torchcodec`. Bug existia
silenciosamente desde v0.1.1 porque o CI nunca exercitou o caminho
`whisperx.exe audio.wav` — so testava `Test-Path` do binario.

### Root cause

`transformers==5.5.1` em `audio_utils.py:55` faz:
```python
if is_torchcodec_available():
    TORCHCODEC_VERSION = version.parse(importlib.metadata.version("torchcodec"))
```

`is_torchcodec_available()` usa `find_spec("torchcodec")` que retorna
truthy pois o pacote Python esta no bundle. Mas o PyInstaller empacota
os arquivos `.py` sem empacotar o `torchcodec-0.7.0.dist-info/`, entao
`version("torchcodec")` levanta `PackageNotFoundError`. Esse caminho
dispara assim que `whisperx` importa `alignment` (toda invocacao do
whisperx.exe com audio real).

### Fix

- `packaging/transcritorio.spec` — `copy_metadata()` para 13 pacotes
  (torchcodec + torch/torchaudio/transformers/huggingface_hub/tokenizers/
  tqdm/regex/requests/packaging/filelock/pyyaml/numpy). Defensivo — qualquer
  outro pacote que chame `importlib.metadata.version("<self>")` em runtime
  estava sujeito ao mesmo bug.
- `packaging/transcritorio.spec` — `hidden_imports` ganhou `torchcodec` +
  `collect_submodules("torchcodec")`. Complementa o `copy_metadata`.
- `.github/workflows/release.yml` — novo gate "Frozen-bundle whisperx
  import chain" em Windows, Linux e Mac. Roda `whisperx` contra audio
  real (3s silencio, modelo tiny offline). Pega este tipo de bug antes
  da release publicar.

### Como o bug passou despercebido

- `transcritorio-cli.exe models smoke-test` usa `faster_whisper.WhisperModel`
  diretamente, nao carrega `whisperx.alignment` nem `transformers.audio_utils`.
- `whisperx.exe --help` passa porque argparse carrega antes dos imports lazy.
- CI sempre usou `Test-Path whisperx.exe` como validacao — existe o binario,
  mas nunca foi invocado.
- O usuario de v0.1.1 que tenha `torchcodec` instalado via pip no sistema
  (fora do bundle) nao via o bug, porque `find_spec` resolveria via PATH
  do Python externo. Apenas bundles PyInstaller frozen quebravam.

## 0.1.2 — 2026-04-24

Bundle Windows agora funciona **standalone** em PCs sem CUDA Toolkit.
Plataformas Mac e Linux inalteradas no comportamento (torch+cpu wheel
ja resolvia). Tamanho do Windows installer subiu de 596 MB para 1.63 GB
como trade-off pela robustez — v0.1.1 dependia silenciosamente do
CUDA Toolkit instalado pelo usuario e falhava em PCs sem ele.

### Split CPU/CUDA preciso

- `packaging/bundle_filter.py` — lista `CPU_EXTRA` agora mapeia
  **exatamente** as 14 DLLs CUDA que o torch cu128 carrega sob demanda
  via dlopen (cudnn_ops/adv/cnn/engines_*/graph/heuristic, nvrtc*,
  curand, cusolverMg, cufftw, caffe2_nvrtc). A lista `MINIMAL` e vazia
  — `variant=full` preserva todas as 25 CUDA DLLs para o split_bundle
  ter o que rotear ao `cuda_pack`.
- `packaging/transcritorio.spec` — coleta **explicita** das 14 DLLs
  lazy-load via `binaries`. O hook-torch do PyInstaller so pega
  imports IAT; sem essa adicao as lazy-load nunca chegam ao bundle.
- `transcribe_pipeline/runtime.py` — `cuda_libs_present()` usa
  `cudnn_ops64_9.dll` como canario do cuda_pack instalado (antes era
  `torch_cuda.dll`, que agora fica sempre no bundle base por ser IAT).
  `detect_device()` em Windows exige `cuda_libs_present()` alem de
  `torch.cuda.is_available()` — evita crash `cudnn_graph64_9.dll not
  found` em Conv/LSTM quando o usuario NVIDIA ainda nao baixou o
  cuda_pack.

### Por que o bundle Windows cresceu

Versoes 0.1.x anteriores strippavam `cufft64`, `cusparse64` e
`nvJitLink` do bundle — essas 3 sao **imports IAT** de
`torch_cpu.dll`/`torch.dll` em torch cu128. `import torch` falha com
`OSError [WinError 126]` sem elas. v0.1.1 "funcionava" so em PCs que
tinham CUDA Toolkit instalado em `C:\Program Files\NVIDIA GPU
Computing Toolkit` resolvendo via PATH. Em PCs sem CUDA Toolkit o
bundle crashava silenciosamente no primeiro `import torch`.

v0.1.2 preserva as 11 CUDA DLLs IAT obrigatorias no bundle base. As
14 lazy-load vao para o `cuda_pack` separado (download-on-demand a
partir do dialog "Detectamos placa NVIDIA" no primeiro launch).

### Artefatos

| Sistema | Arquivo | Tamanho |
|---|---|---|
| Windows 10/11 | `Transcritorio-0.1.2-Setup.exe` | ~1.63 GB |
| Windows CUDA pack | `transcritorio-cuda-pack-0.1.2-win64.zip` | ~890 MB |
| macOS arm64 | `Transcritorio.dmg` | ~600 MB |
| Linux x86_64 | `Transcritorio-x86_64.AppImage` | ~771 MB |

## 0.1.1 — 2026-04-20

Primeira versao com distribuicao cross-plataforma automatica via GitHub
Actions. Nenhuma mudanca de API ou UX no app em si; esta e uma release
de **infraestrutura** que torna o Transcritorio instalavel em macOS e
Linux alem do Windows.

### Novos artefatos de release

- **Linux AppImage** (`Transcritorio-x86_64.AppImage`, ~1.5 GB) — roda
  em qualquer distro com glibc 2.35+. Pre-requisitos: `ffmpeg` +
  libs xcb do sistema. Veja [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md).
- **macOS .dmg** (`Transcritorio.dmg`, ~500 MB, arm64) — nao assinado;
  primeira execucao requer "botao direito > Abrir". Veja
  [`docs/MAC_INSTALL.md`](docs/MAC_INSTALL.md). Icone e background
  customizados.
- **Windows Setup** (`Transcritorio-0.1.1-Setup.exe`) — mesmo formato
  de antes, agora buildado no CI em vez de localmente.

### Mudancas internas

- **CI multiplataforma** (`.github/workflows/ci.yml`): matriz Windows /
  Linux / macOS rodando toys + smokes a cada push e PR em `main`.
  Deps minimas (PySide6, numpy, keyring, cryptography — sem torch/
  whisperx/pyannote) rodam em ~2-3min por OS.
- **Release workflow** (`.github/workflows/release.yml`): gatilho
  `workflow_dispatch` (manual) ou tag `v*.*.*`. Builda 3 artefatos,
  automatiza smoke Linux via Xvfb offscreen + CLI.
- **Bundle variant infra** (`packaging/bundle_filter.py` novo): spec
  PyInstaller aceita `TRANSCRITORIO_BUNDLE_VARIANT=cpu|full` via env
  var. Em `cpu`, strippa ~3 GB de CUDA DLLs (torch_cuda, cudnn*,
  cublas*, etc.) cross-plataforma (.dll / .so / .dylib).
- **Helper `runtime.cuda_libs_present()`** — detecta se torch_cuda
  esta no bundle. Usado pela GUI para oferecer download do CUDA pack
  em primeira execucao (pipeline 2E).
- **CUDA download-on-demand** (Item 2B-E do backlog):
  - `build.ps1` produz bundle base CPU + `cuda_pack.zip` separado
  - Inno Setup `[Components]` com detecao de NVIDIA via `nvidia-smi`
  - Download sob demanda via Inno Download Plugin
  - Dialog GUI pos-instalacao oferece CUDA se NVIDIA detectada.

### Testes

De 9 toys na 0.1.0 para **17 toys + 5 smokes**. Cobertura nova:
edge cases de filtro cross-plataforma, cuda_libs_present com FS
inusual, detect_device com torch atipico, token_vault com backends
estranhos.

### Autoria

Commits de 0.1.1 sao de autoria exclusivamente humana; assistentes
LLM nao aparecem como Co-Authored-By, Signed-off-by ou similar
(conforme CLAUDE.md regra #9).

## 0.1.0 — 2026-04-14

Release inicial. Windows-only, instalador Inno Setup, ASR via WhisperX
+ diarizacao pyannote community-1. GUI em PySide6.
