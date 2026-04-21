# Transcritorio

App desktop para transcricao automatica local de entrevistas com separacao de falantes.

Autor: Rogerio Jeronimo Barbosa - https://antrologos.github.io/

## Downloads (usuario final)

Baixe a versao para sua plataforma em
[Releases](https://github.com/antrologos/Transcritorio/releases/latest):

| Plataforma | Arquivo | Instrucoes |
|------------|---------|------------|
| **Windows 10/11** | `Transcritorio-0.1.1-Setup.exe` | Clique duas vezes. O instalador detecta placa NVIDIA e oferece aceleracao opcional (+1 GB download). |
| **macOS** (Apple Silicon) | `Transcritorio.dmg` | Veja [`docs/MAC_INSTALL.md`](docs/MAC_INSTALL.md) — primeira execucao pede "botao direito > Abrir" por causa do Gatekeeper. |
| **Linux** (Ubuntu, Fedora, etc.) | `Transcritorio-x86_64.AppImage` | Veja [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md) — precisa instalar `ffmpeg` + libs xcb do sistema. |

**Status por plataforma (v0.1.1):**
- **Windows**: suportado. Aceleracao NVIDIA opcional no instalador.
- **Linux**: suportado. Testado em Ubuntu 22.04+; CUDA opcional no primeiro uso.
- **macOS**: experimental. Compila e passa testes automatizados no CI; teste manual de campo pendente. Aceleracao por GPU Apple (MPS) via `mlx-whisper` esta integrada no codigo e empacotada no `.dmg` a partir da v0.1.2, mas ainda nao validada em hardware real — veja [`docs/MLX_WHISPER_MACOS.md`](docs/MLX_WHISPER_MACOS.md).

## Desenvolvimento

O software vive nesta pasta e abre projetos de transcricao de qualquer lugar. Use `--project` para apontar para a pasta do projeto, ou rode de dentro da pasta do projeto (fallback CWD).

Para segredos locais, leia `docs/SEGURANCA_SEGREDOS.md`.

Fluxo principal (CLI):

```cmd
scripts\transcribe.cmd --project "C:\caminho\do\projeto" manifest
scripts\transcribe.cmd --project "C:\caminho\do\projeto" prepare-audio --ids ID
scripts\transcribe.cmd --project "C:\caminho\do\projeto" transcribe --ids ID
scripts\transcribe.cmd --project "C:\caminho\do\projeto" diarize --ids ID
scripts\transcribe.cmd --project "C:\caminho\do\projeto" render --ids ID
scripts\transcribe.cmd --project "C:\caminho\do\projeto" qc --ids ID
```

Tambem ha um wrapper PowerShell em `scripts/Invoke-TranscriptionPipeline.ps1`, mas ele depende da politica local de execucao de scripts.

Para abrir o prototipo inicial de interface grafica:

```powershell
.\scripts\transcription_gui.cmd
```

Para abrir o novo Estudio de Revisao com player, waveform com zoom, transcricao sincronizada e edicao por bloco:

```powershell
.\scripts\review_studio.cmd
```

Para preparar os modelos locais no primeiro uso, cada usuario deve usar o proprio token Hugging Face:

```powershell
$env:TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN="COLE_O_TOKEN_DE_LEITURA_AQUI"
.\scripts\transcribe.cmd models download
.\scripts\transcribe.cmd models verify
```

Na GUI, use `Configuracoes > Configurar modelos...` para seguir o passo a passo em portugues. O token serve apenas para baixar os modelos; audios, videos e transcricoes continuam no computador local. Depois da verificacao, a execucao usa cache local/offline.

Na interface, a lista `Arquivos do projeto` aceita selecao multipla. Use `Adicionar midia...` para escolher arquivos individuais ou uma pasta, e `Editar propriedades...` junto da lista para definir lingua, quantidade de falantes, rotulos e contexto opcional nos arquivos selecionados. O botao `Transcrever` executa o fluxo completo da selecao: preparar audio, transcrever, identificar falantes, montar a transcricao editavel e verificar arquivos gerados. A barra de progresso acompanha percentuais reais emitidos pelo WhisperX quando disponiveis e usa progresso por etapa como fallback, sem exibir texto bruto da transcricao no status. A barra some quando nao ha processamento ativo. Use `Salvar transcricao` para gravar a transcricao editavel e `Exportar...` para gerar `DOCX`, `MD`, `SRT`, `VTT`, `CSV/TSV` e `NVivo`. `Fila de processamento` e `Configurar transcricao...` ficam no menu `Ferramentas`.

O menu `Projeto` tambem tem `Novo projeto...` e `Abrir projeto...`. Projetos novos sao criados como uma pasta `*.transcricao`; para portabilidade real, em etapa posterior ainda falta implementar a opcao de copiar midias para dentro dessa pasta em vez de apenas referenciar arquivos externos.

O projeto agora tambem possui arquivos de organizacao de alto nivel:

- `projeto.transcricao.json`: descritor do projeto de transcricoes.
- `metadados.csv`: tabela editavel/auditavel com uma linha por arquivo selecionado, incluindo metadados de origem/audio/video, lingua, falantes, rotulos e contexto opcional.
- `Transcricoes/00_project/jobs.json`: fila, estado e progresso por arquivo para acompanhamento e retomada basica.

Antes de transcrever em ambiente de desenvolvimento:

- Instale FFmpeg shared e garanta que `ffmpeg` e `ffprobe` estejam no PATH.
- Crie o ambiente Python local executando `.\scripts\setup_transcription_env.cmd`. Por padrao, ele fica fora do Dropbox em `%LOCALAPPDATA%\Transcritorio\transcricao-venv`; os wrappers reutilizam o caminho local legado se ele ja existir.
- Instale `whisperx==3.8.5`, `PySide6` e dependencias CUDA/PyTorch. `scripts\setup_transcription_env.cmd` ja inclui esses pacotes.
- Aceite o modelo `pyannote/speaker-diarization-community-1` no Hugging Face.
- Use `models download` com o token do proprio usuario para baixar os modelos. Os wrappers nao carregam tokens persistidos automaticamente.

## macOS e Linux (MVP 0.2)

A versao 0.2 roda nativamente em macOS e Linux via scripts `.sh`.
Instaladores (`.dmg` / AppImage) ficam para 0.3+.

**macOS**: `brew install ffmpeg python@3.11` + `./scripts/setup_transcription_env.sh`.
Em Apple Silicon, `pip install -e ".[mac]"` ativa `mlx-whisper` para aceleracao Metal
(detectada automaticamente em runtime). Sem `mlx-whisper`, ASR cai em CPU
(~3-5x tempos do CUDA). Veja [`docs/MLX_WHISPER_MACOS.md`](docs/MLX_WHISPER_MACOS.md).

**Linux (Ubuntu/Debian)**: `sudo apt install ffmpeg python3.11 python3.11-venv libxcb-cursor0 libxcb-shape0` + `./scripts/setup_transcription_env.sh`. CUDA opcional (ver `docs/MAC_LINUX.md`).

**Armazenamento de tokens**: `keyring` usa Keychain (Mac), SecretService (Linux) ou Credential Manager (Windows, com migracao automatica do DPAPI legado). Linux headless cai em Fernet+machine-id.

Detalhes e troubleshooting em [`docs/MAC_LINUX.md`](docs/MAC_LINUX.md).

Saidas principais:

- `Transcricoes/00_manifest/manifest.csv`
- `projeto.transcricao.json`
- `metadados.csv`
- `Transcricoes/00_project/jobs.json`
- `Transcricoes/00_manifest/speakers_map.csv`
- `Transcricoes/01_audio_wav16k_mono/*.wav`
- `Transcricoes/02_asr_raw/`
- `Transcricoes/02_asr_variants/<nome>/` para testes A/B que nao sobrescrevem o baseline
- `Transcricoes/03_diarization/json/*.regular.json` e `*.exclusive.json`
- `Transcricoes/03_diarization/rttm/*.regular.rttm` e `*.exclusive.rttm`
- `Transcricoes/04_canonical/json/*.canonical.json`
- `Transcricoes/05_transcripts_review/md/*.md`
- `Transcricoes/05_transcripts_review/docx/*.docx`
- `Transcricoes/05_transcripts_review/edits/*.review.json`
- `Transcricoes/05_transcripts_review/final/` para exportacoes revisadas em `MD`, `DOCX`, `SRT`, `VTT`, `CSV`, `TSV` e `NVivo`
- `Transcricoes/06_qc/qc_metrics.csv`

As versoes `md` e `docx` sao a camada de leitura: agrupam falas consecutivas do mesmo falante, mostram timestamp apenas no inicio de cada bloco e usam `Entrevistador:`/`Entrevistado:` em negrito. As camadas `json`, `srt`, `vtt` e `tsv` preservam a granularidade para auditoria e importacao.

Testes A/B de ASR ja previstos no CLI:

```powershell
.\scripts\transcribe.cmd transcribe --ids A01P_0608 --variant float16 --compute-type float16 --no-diarize
.\scripts\transcribe.cmd transcribe --ids A01P_0608 --variant large-v3-turbo_float16 --model large-v3-turbo --compute-type float16 --no-diarize
.\scripts\transcribe.cmd diarize --ids A01P_0608 --dry-run --num-speakers 2
```

`manifest` agora preenche metadados de audio, video e formato via `ffprobe`, e `qc` usa esses dados para checar cobertura, duracao, gaps e sinais basicos do JSON bruto do WhisperX.

No piloto `A01P_0608`, `float16` ficou proximo do baseline; `float16_prompt`, `prompt_roteiro_curto` e `prompt_contexto_minimo` foram reprovados por eco/alucinacao/perda de conteudo; `large-v3-turbo_float16` mostrou erros qualitativos. Um teste adicional `float16` vs `int8_control` em 5 entrevistas esta em `Transcricoes/06_qc/asr_float16_5interviews_report.md`. Decisao atual: usar `float16` como padrao, sem prompt/hotwords, com fallback `int8` se a VRAM estiver apertada.

Para testes A/B de ASR, use `--variant <nome>` para gravar em `Transcricoes/02_asr_variants/<nome>/`. Nao sobrescreva `Transcricoes/02_asr_raw` sem decisao explicita, porque o render atual usa esses arquivos como baseline.

O CLI tambem aceita `--initial-prompt-file` para reprodutibilidade de testes, mas os prompts atualmente em `Transcricoes/00_config/prompts/` foram reprovados no piloto e nao devem ser usados como padrao.
