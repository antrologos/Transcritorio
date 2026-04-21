# Arquitetura

Este documento descreve a arquitetura interna do Transcritório para quem
precisa entender o pipeline, debugar ou contribuir com código. Para
instalar e usar, veja o [README](../README.md) ou o
[site do projeto](https://antrologos.github.io/Transcritorio/pt/).

## Visão geral do pipeline

O fluxo principal transforma arquivos de mídia em transcrições revisáveis
em seis etapas, encadeadas:

```
manifest  →  prepare-audio  →  transcribe (ASR)  →  diarize  →  render  →  qc
```

1. **manifest**: varre uma pasta de entrada, descobre arquivos de áudio
   e vídeo, extrai metadados técnicos (duração, sample rate, canais,
   codec) via `ffprobe` e grava `Transcricoes/00_manifest/manifest.csv`.
2. **prepare-audio**: converte cada arquivo selecionado para WAV 16 kHz
   mono (`Transcricoes/01_audio_wav16k_mono/*.wav`) — formato uniforme
   que Whisper espera.
3. **transcribe** (ASR): roteia para um de dois caminhos:
   - **WhisperX CLI** (padrão, Windows/Linux): subprocess via
     `transcribe_pipeline/whisperx_runner.py` que invoca o CLI
     `whisperx` embutido no bundle.
   - **MLX Runner** (automático em Apple Silicon): chamada direta a
     `mlx_whisper.transcribe()` via
     `transcribe_pipeline/mlx_whisper_runner.py`. Ativa quando
     `runtime.detect_device() == "mps"` e `mlx_whisper` está
     instalado. Produz JSON compatível com o resto do pipeline.
   Saída: `Transcricoes/02_asr_raw/{id}.json` (mais SRT, VTT, TXT, TSV).
4. **diarize**: separação de falantes via `pyannote.audio`. Lê o WAV
   preparado, produz `Transcricoes/03_diarization/json/{id}.exclusive.json`
   marcando quem falou em cada trecho.
5. **render**: combina o JSON de ASR com o de diarização, aplica mapa de
   falantes (`speakers_map.csv`), gera a transcrição canônica
   (`04_canonical/json/*.canonical.json`) e as saídas para leitura humana
   (`05_transcripts_review/md/*.md`, `docx/*.docx`).
6. **qc**: métricas básicas (cobertura, duração, gaps, sinais de
   alucinação) em `Transcricoes/06_qc/qc_metrics.csv`.

A GUI (`transcribe_pipeline/review_studio_qt.py`) expõe o mesmo fluxo
sem precisar de terminal; CLI (`transcribe_pipeline/cli.py`) é útil para
scripts e automação.

## Estrutura de pastas por projeto

Cada projeto de pesquisa vive numa pasta `.transcricao` com este formato:

```
meu-projeto.transcricao/
├─ projeto.transcricao.json     descritor do projeto (nome, versão do app)
├─ metadados.csv                tabela editável/auditável por arquivo
└─ Transcricoes/
   ├─ 00_config/run_config.yaml    parâmetros (modelo, falantes, idioma)
   ├─ 00_manifest/
   │  ├─ manifest.csv              lista de arquivos + metadados técnicos
   │  └─ speakers_map.csv          override manual de rótulos
   ├─ 00_project/
   │  ├─ jobs.json                 fila, estado e progresso por arquivo
   │  └─ waveforms/                caches de forma de onda pro player
   ├─ 01_audio_wav16k_mono/*.wav   áudios preparados (16 kHz mono)
   ├─ 02_asr_raw/                  saída bruta do ASR (JSON + SRT/VTT/TXT/TSV)
   ├─ 02_asr_variants/<nome>/      saída de testes A/B sem contaminar baseline
   ├─ 03_diarization/
   │  ├─ json/{id}.regular.json    diarização com sobreposições
   │  ├─ json/{id}.exclusive.json  diarização forçada sem sobreposição (default)
   │  ├─ rttm/{id}.regular.rttm
   │  └─ rttm/{id}.exclusive.rttm
   ├─ 04_canonical/
   │  ├─ json/*.canonical.json     representação única auditável
   │  └─ jsonl/*.jsonl
   ├─ 05_transcripts_review/
   │  ├─ md/*.md                   camada de leitura (blocos por falante)
   │  ├─ docx/*.docx               idem em Word
   │  ├─ edits/*.review.json       edições do Estúdio (diff contra canonical)
   │  └─ final/                    exportações revisadas (MD/DOCX/SRT/VTT/CSV/TSV/NVivo)
   └─ 06_qc/
      ├─ qc_metrics.csv
      └─ samples/
```

**Pasta principal pro usuário:** `05_transcripts_review/md/` tem as
transcrições em Markdown prontas para ler, com blocos agrupados por
falante e timestamps no início de cada bloco.

## Camadas de leitura vs auditoria

| Camada | Formato | Uso |
|---|---|---|
| **Leitura** | MD, DOCX | Revisão humana. Blocos por falante (`Entrevistador:`/`Entrevistado:` em negrito), timestamp só no início de cada bloco. |
| **Granular** | JSON, SRT, VTT | Timestamps por segmento e por palavra, preserva todos os metadados. Use para auditoria, sincronização com vídeo, importação em ferramentas de análise. |
| **Tabular** | CSV, TSV | Uma linha por turno. Útil para análise quantitativa e estatística. |
| **NVivo** | TSV especial | Formato de importação nativo do NVivo. |
| **Canonical** | JSON em `04_canonical/` | Representação interna única que une ASR + diarização + edições. Toda exportação deriva deste arquivo. |

## Arquivos descritores

- **`projeto.transcricao.json`**: metadados de nível alto (nome do
  projeto, data de criação, versão do app que gerou). Atualizado pelo
  Estúdio ao abrir/editar.
- **`metadados.csv`**: uma linha por arquivo selecionado, com colunas
  para origem, idioma, número de falantes, rótulos (Entrevistador,
  Informante), contexto opcional. Editável à mão ou pela GUI.
- **`Transcricoes/00_project/jobs.json`**: fila e estado por arquivo —
  permite acompanhar progresso e retomar lotes interrompidos. Atualizado
  pelo pipeline.

## Dispatch ASR: WhisperX vs MLX

O módulo `transcribe_pipeline/whisperx_runner.py` contém a função
`run_whisperx()` que decide qual backend usar no início:

```python
wanted_device = (config.get("asr_device") or "").lower()
mlx_opt_in = bool(config.get("asr_use_mlx_on_mps", True))
if mlx_opt_in and wanted_device != "cpu":
    if runtime.detect_device() == "mps" and mlx_whisper_runner.is_available():
        return mlx_whisper_runner.run_mlx_whisper(...)   # caminho MLX
# caminho default: subprocess whisperx CLI
```

Os dois caminhos produzem o **mesmo contrato de saída** (JSON com
`segments[i].start/end/text/words[]`), então `render.py`, `diarization.py`
e os demais consumidores são agnósticos de backend. Campos extras de
cada backend são preservados mas ignorados pelos consumidores comuns.

Para detalhes do caminho MLX, veja
[`MLX_WHISPER_MACOS.md`](MLX_WHISPER_MACOS.md).

## Cache de modelos

- Default: `%LOCALAPPDATA%/Transcritorio/models/huggingface` (Windows),
  `~/.local/share/Transcritorio/models/huggingface` (Linux/Mac).
- `runtime.apply_secure_hf_environment()` aponta `HF_HOME` e
  `HF_HUB_CACHE` para esse diretório. Ambos os backends (WhisperX e
  MLX) compartilham o mesmo cache via env vars.
- Override via `run_config.yaml`: `model_cache_dir: /caminho/alternativo`.
- Offline mode: `asr_model_cache_only: true` (default) usa só o cache
  local, nunca tenta baixar durante a transcrição. A primeira execução
  precisa estar online; depois roda offline.

## Segurança de tokens

Tokens Hugging Face são armazenados via `keyring` (Credential Manager no
Windows, Keychain no Mac, SecretService no Linux). Nunca gravados no
repo, em logs ou em `jobs.jsonl`. `sanitize_message()` redige strings
`hf_*` antes de gravar qualquer traceback. Detalhes em
[`SEGURANCA_SEGREDOS.md`](SEGURANCA_SEGREDOS.md).

## Referências cruzadas

- Setup e comandos do source: [`DEVELOPMENT.md`](DEVELOPMENT.md)
- Testes A/B e decisões de modelo: [`EXPERIMENTS.md`](EXPERIMENTS.md)
- Checklist pré-release: [`PACKAGING_CHECKLIST.md`](PACKAGING_CHECKLIST.md)
- Instalação macOS/Linux: [`MAC_INSTALL.md`](MAC_INSTALL.md),
  [`LINUX_INSTALL.md`](LINUX_INSTALL.md)
