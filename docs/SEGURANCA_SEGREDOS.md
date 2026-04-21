# Seguranca de segredos

Este repositorio nao deve conter tokens, chaves, senhas, hashes reversiveis, caminhos de segredos pessoais ou trechos de comando que imprimam segredos.

## Hugging Face

- Cada usuario deve usar seu proprio token de leitura apenas para baixar modelos no primeiro uso.
- O token nunca deve ser gravado no repositorio, em arquivos de projeto, logs, `jobs.json`, `run_config.yaml`, CSV/JSON de saida ou mensagens de erro.
- Os wrappers do app nao carregam tokens persistidos automaticamente.
- Para uso por CLI, informe o token apenas em variavel de ambiente temporaria da sessao:

```powershell
$env:TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN="COLE_O_TOKEN_DE_LEITURA_AQUI"
.\scripts\transcribe.cmd models download
Remove-Item Env:\TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN
```

- Depois de `models verify`, transcricao, alinhamento e diarizacao devem rodar com cache local/offline.
- Se `models download` falhar por token ausente, confirme que o usuario informou um token de leitura proprio e aceitou os termos do modelo no Hugging Face.
- Se houver suspeita de exposicao, rotacione o token na conta Hugging Face correspondente.

## MLX-Whisper (Apple Silicon)

O caminho acelerado em Mac usa `mlx-whisper`, que baixa modelos de repos
`mlx-community/*` no Hugging Face (por exemplo `mlx-community/whisper-large-v3-mlx`).

- Os repos `mlx-community/*` sao publicos — **nao exigem token**. O token HF
  so continua necessario para `pyannote/*` (separacao de falantes).
- O cache e unificado: `runtime.apply_secure_hf_environment()` aponta
  `HF_HOME` / `HF_HUB_CACHE` para o mesmo `runtime.model_cache_dir()` usado
  pelo faster-whisper, evitando cache duplicado.
- `mlx_whisper_runner.py` sanitiza mensagens de excecao antes de gravar em
  `jobs.jsonl` (via `utils.sanitize_message`), redigindo tokens `hf_*` que
  pudessem aparecer em traces de erro da biblioteca `huggingface_hub`.
- `verbose=False` e passado ao `mlx_whisper.transcribe()` para evitar que a
  biblioteca imprima env vars ou tokens no stdout.
