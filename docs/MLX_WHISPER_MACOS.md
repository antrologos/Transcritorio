# Aceleracao Metal no Apple Silicon (mlx-whisper)

O Transcritorio usa a framework MLX da Apple para rodar Whisper direto
no GPU integrado dos chips M1/M2/M3/M4. Isso da tipicamente **3x a 5x
mais rapido** que CPU puro em Apple Silicon.

## Para usuarios finais (baixaram o .dmg)

**A integracao MLX esta no codigo mas ainda nao foi empacotada nos
binarios publicos** — a v0.1.1 distribuida em `.dmg` foi montada antes da
integracao. A proxima release macOS ja incluira `mlx-whisper` dentro do
`Transcritorio.app`.
Ao abrir o app num Mac com Apple Silicon, a aceleracao MPS e detectada
automaticamente e o caminho MLX e usado.

> **Confirmar que a aceleracao esta ativa**: abra o Transcritorio, va em
> *Preferencias → Sobre* (ou equivalente) e verifique que o backend
> aparece como `mlx-whisper`. Alternativamente, transcreva uma entrevista
> curta e observe no log: deve aparecer `Carregando modelo MLX
> mlx-community/whisper-large-v3-mlx...`.

### Requisitos

- macOS 13.5+ (Ventura ou superior)
- Chip Apple Silicon (M1/M2/M3/M4). Nao funciona em Macs Intel.
- ~5 GB livres para o modelo large-v3-turbo (download automatico no
  primeiro uso).

## Para desenvolvedores (rodando do source)

Se voce clonou o repositorio e quer usar MLX:

```bash
pip install -e ".[mac]"
# ou, equivalente:
pip install mlx-whisper
```

Valide que funciona:

```bash
python -c "
from transcribe_pipeline import mlx_whisper_runner
from transcribe_pipeline.runtime import detect_device
print('device:', detect_device())
print('mlx available:', mlx_whisper_runner.is_available())
"
```

Esperado em Mac M-series: `device: mps`, `mlx available: True`.

## Fallback automatico

Se, por qualquer motivo, `mlx-whisper` nao estiver presente:

- O Transcritorio **detecta** isso e cai automaticamente no caminho CPU
  via `faster-whisper`.
- Uma mensagem no log informa: *"MPS detectado mas mlx-whisper nao esta
  instalado. Usando CPU (~3x tempo real)."*
- Voce pode forcar o caminho CPU em qualquer situacao definindo
  `asr_device: cpu` no `run_config.yaml`.

## Desligar a aceleracao MLX

Edite `Transcricoes/00_config/run_config.yaml` no seu projeto:

```yaml
asr_use_mlx_on_mps: false
```

Ou mude o device para forcar CPU:

```yaml
asr_device: cpu
```

## Limitacoes

- **Word-level timestamps**: habilitado por padrao (`word_timestamps=True`).
  Em modelos muito pequenos (tiny/base) os timestamps podem ser
  aproximados. Para revisao manual, prefira large-v3 ou large-v3-turbo.
- **Modelos CTranslate2 (padrao no Windows)**: nao sao compativeis com
  MLX. O runner MLX usa modelos da HF org `mlx-community/` (mapeamento
  em `mlx_whisper_runner.MLX_MODEL_MAP`).
- **Diarizacao**: continua via pyannote.audio (CPU ou MPS parcial).
  Independente do caminho de ASR.

## Validação e limitações conhecidas

Esta integração foi desenvolvida **sem acesso a hardware Apple Silicon**.
Seguem os limites:

**O que foi validado:**
- 23 toy tests cobrindo: dispatch de backend, normalização de saída,
  formato SRT/VTT, propagação de config, compatibilidade retroativa de
  `run_config.yaml`, filtragem de segments/words malformados, captura de
  exceções (batch não é derrubado), popula MLX no combo da GUI só quando
  MPS detectado, fallback para CLI quando mlx-whisper ausente.
- Schema de saída do `mlx_whisper.transcribe()` conferido contra o
  código-fonte em `ml-explore/mlx-examples`: keys `text`, `segments`,
  `language`; words com `word`/`start`/`end`/`probability`.
- Bug real descoberto e corrigido durante testes: `_srt_ts`/`_vtt_ts`
  usavam `format_timestamp()` sem `millis=True`, emitindo SRT sem
  milissegundos (players recusariam). Fix: passar `millis=True`.
- Dependência `mlx` verificada no PyPI: wheels apenas para
  `macosx_13_0_arm64` / `macosx_14_0_arm64`. Windows/Linux sem mlx -> o
  caminho CPU é preservado.

**O que NÃO foi validado (requer Mac físico):**
- Transcrição real de áudio pelo caminho MLX.
- Comparação de performance MLX × CPU no mesmo áudio.
- Consumo de memória GPU / crashes em áudios longos.
- PyInstaller em `macos-14` realmente produzindo um `.app` com mlx-whisper
  embarcado (possível de validar só com `git push` + observar CI).

**Limitações conhecidas da integração:**
- **Cancelamento durante transcrição**: `mlx_whisper.transcribe()` é uma
  chamada síncrona de Python sem suporte a interrupção cooperativa. O botão
  "Cancelar" só tem efeito **entre arquivos** num lote — uma vez iniciada a
  transcrição de um áudio, ela vai até o fim (tipicamente 1-5 min por hora
  de áudio em MLX). O runner emite `asr_progress` no início e `asr_done` no
  fim, mas não durante.
- **Sem smoke test macOS no CI**: o workflow `.github/workflows/release.yml`
  tem `smoke-linux-appimage` mas não o equivalente `smoke-macos-dmg`. A
  primeira validação real do bundle macOS com mlx-whisper vai acontecer
  só com um usuário abrindo o `.dmg` — manter expectativas ajustadas.
- **Hook PyInstaller**: o spec usa `collect_submodules("mlx_whisper")` +
  `collect_submodules("mlx")` inline (try/except). Se a estrutura interna
  do MLX mudar, hoje não temos um `packaging/hooks/hook-mlx.py` dedicado.

A recomendação é fazer um teste guiado com 1-2 colegas com Mac antes de
anunciar o suporte MLX em público.

## Arquitetura

```
whisperx_runner.run_whisperx()
  │
  ├─ detect_device() == "mps" AND mlx_whisper_runner.is_available()
  │        AND config.asr_device != "cpu"
  │        AND config.asr_use_mlx_on_mps != false
  │  └→ mlx_whisper_runner.run_mlx_whisper()
  │     └→ mlx_whisper.transcribe() → normaliza JSON → escreve em 02_asr_raw/json/
  │
  └─ Caminho default:
     └→ whisperx CLI (subprocess) com CT2/faster-whisper
```

Ambos os caminhos produzem o mesmo formato de JSON; `render.py`,
`diarization.py` e o resto do pipeline sao agnosticos.
