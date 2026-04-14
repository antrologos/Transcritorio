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
