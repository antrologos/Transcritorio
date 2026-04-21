# Experimentos e decisões de modelo

Este documento reúne testes A/B e decisões empíricas que motivaram os
defaults atuais do Transcritório. Pertence à documentação de
desenvolvimento (não precisa ser lido por usuário final).

## Piloto A01P_0608

Entrevista de referência usada para validar mudanças de modelo e de
pipeline. Resultados arquivados em `Transcricoes/06_qc/` do projeto
piloto.

### Conclusões do piloto

- **`float16`**: baseline operacional em GPU. Acurácia ≈ modelo de
  referência; velocidade boa.
- **`float16` + `initial_prompt`**: reprovado — produziu eco/alucinação
  (o prompt vazava para dentro da transcrição).
- **`float16` + `prompt_roteiro_curto`** e **`prompt_contexto_minimo`**:
  reprovados por eco e perda de conteúdo.
- **`large-v3-turbo_float16`**: mais rápido porém com erros qualitativos
  em entrevistas pt-BR (principalmente em sotaques regionais e trechos
  com sobreposição de fala).
- **`int8`**: usado como fallback quando a VRAM está apertada (<6 GB);
  acurácia cai ~1–2 pontos percentuais vs `float16`.

### Teste ampliado em 5 entrevistas

Comparação `float16` vs `int8_control` em 5 arquivos adicionais está
documentado em `06_qc/asr_float16_5interviews_report.md` do projeto
piloto.

### Decisão operacional atual

- **Modelo**: `large-v3` (default)
- **Compute type**: `float16` em GPU; fallback automático para `int8`
  quando a VRAM disponível é insuficiente.
- **Prompts**: `asr_initial_prompt: null` (não usar).
- **Hotwords**: `asr_hotwords: null` (não usar).
- **Beam size**: 5.
- **Batch size**: 4 (ajustável via `asr_batch_size` no `run_config.yaml`).

Documentada em `transcribe_pipeline/config.py` como `DEFAULT_CONFIG`.

## Testes A/B sem contaminar baseline

Para rodar um experimento sem sobrescrever as saídas consolidadas em
`02_asr_raw/`, use a flag `--variant <nome>`:

```powershell
.\scripts\transcribe.cmd transcribe `
  --ids A01P_0608 `
  --variant float16 `
  --compute-type float16 `
  --no-diarize

.\scripts\transcribe.cmd transcribe `
  --ids A01P_0608 `
  --variant large-v3-turbo_float16 `
  --model large-v3-turbo `
  --compute-type float16 `
  --no-diarize

.\scripts\transcribe.cmd diarize `
  --ids A01P_0608 `
  --dry-run `
  --num-speakers 2
```

Saídas ficam em `Transcricoes/02_asr_variants/<nome>/`. O `render`
continua usando `02_asr_raw/` como baseline, então um experimento mal
sucedido não contamina os outputs "oficiais" do projeto.

**Nunca sobrescreva `02_asr_raw/` sem decisão explícita** — é a camada
que alimenta o render atual.

## Reprodutibilidade de prompts

O CLI aceita `--initial-prompt-file <path>` para experimentos que
precisam de prompts versionados. Os arquivos atualmente em
`Transcricoes/00_config/prompts/` (do projeto piloto) foram **reprovados**
e **não devem** ser usados como padrão — estão preservados apenas para
documentação histórica.

## Dispatch MLX no Apple Silicon

Testes unitários cobrem o dispatch WhisperX → MLX sem exigir hardware
Apple:

- `tests/toy_mlx_whisper_runner.py` — runner MLX contra mocks.
- `tests/toy_whisperx_mlx_dispatch.py` — decisão do backend.
- `tests/toy_describe_backend.py` — badge do header da GUI.
- `tests/toy_mlx_whisper_edge_cases.py` — normalização de output,
  timestamps SRT/VTT, paridade com whisperx_runner.
- `tests/toy_mlx_whisper_round5.py` — inputs adversariais, sanitização
  de tokens, cancelamento, callbacks defensivos.

Total: 46 toy tests passando em Windows. **Transcrição real em hardware
Apple Silicon permanece não validada** — requer Mac M-series para
smoke test completo.

## Benchmarks

Dados brutos em `tests/benchmark_exhaustive_2026-04-19.csv` (gitignored
— arquivo grande). Resumo:

**ASR CUDA (segundos, audio de 68 min):**

| Modelo | 5m | 30m | 60m | 68m | VRAM |
|---|---|---|---|---|---|
| tiny | 15 | 38 | 76 | 81 | 7263 MB |
| medium | 20 | 60 | 116 | 132 | 7261 MB |
| large-v3 | 26 | 111 | 199 | 228 | 7929 MB |
| turbo | 19 | 56 | 110 | 122 | 7261 MB |

**ASR CPU (até 15 min):**

| Modelo | 5m | 10m | 15m |
|---|---|---|---|
| tiny | 50 | 97 | 143 |
| medium | 114 | 210 | 300 |
| large-v3 | 169 | 310 | 453 |
| turbo | 113 | 210 | 319 |

**Diarização CUDA:** 120 s em 68 min de áudio (RTX 4060 8 GB VRAM).
**Diarização CPU:** 212 s em 15 min (extrapolação linear p/ áudios
maiores).

Hardware de referência do baseline: **NVIDIA GeForce RTX 4060 Laptop,
8 GB VRAM, CUDA 12.8**.

## Contribuir com experimentos

Se você rodar experimentos sistematicamente e quiser publicar decisões
no repositório:

1. Crie um branch `experiment/<nome>`.
2. Grave a variant em `02_asr_variants/<nome>/` (não no baseline).
3. Documente resultados em `docs/experiments/<data>_<nome>.md`.
4. Abra PR — discussão segue por issue/PR.
