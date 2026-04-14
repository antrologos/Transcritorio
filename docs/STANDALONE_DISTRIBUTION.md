# Distribuicao standalone do Transcritorio

Este documento define o contrato de build para instaladores multiplataforma. O objetivo e que o usuario final nao precise instalar Python, FFmpeg, Git, VS Code, CUDA Toolkit ou usar terminal.

## Contrato do runtime

Cada pacote deve conter um runtime privado do Transcritorio:

- Python embutido da plataforma.
- Pacotes Python pinados: PySide6, torch, torchaudio, torchvision, torchcodec, whisperx, faster-whisper, pyannote.audio, huggingface_hub, transformers, python-docx e dependencias transitivas.
- FFmpeg 7.x com `ffmpeg` e `ffprobe` acessiveis por caminhos internos do pacote.
- Launchers que definem `TRANSCRITORIO_RUNTIME_DIR` para o diretorio do runtime privado.

O codigo resolve executaveis nesta ordem:

1. `TRANSCRITORIO_RUNTIME_DIR`
2. `runtime/<plataforma>` dentro da pasta do app
3. pasta do executavel Python
4. `PATH` apenas como fallback de desenvolvimento

## Modelos

Modelos nao entram no instalador padrao. O primeiro uso baixa, com o token Hugging Face do proprio usuario:

- `Systran/faster-whisper-large-v3`
- `jonatasgrosman/wav2vec2-large-xlsr-53-portuguese`
- `pyannote/speaker-diarization-community-1`

O cache padrao fica em:

- Windows: `%LOCALAPPDATA%\Transcritorio\models\huggingface`
- macOS: `~/Library/Application Support/Transcritorio/models/huggingface`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/Transcritorio/models/huggingface`

Para usar outro local, defina `TRANSCRITORIO_MODEL_CACHE`.

## Seguranca

- O token nao deve ser passado por argumento de linha de comando.
- O token nao deve ser gravado em arquivos de projeto, logs, jobs, manifestos, transcricoes ou configuracoes.
- O download usa o token apenas em memoria; a verificacao e a execucao usam modo offline.
- Variaveis padrao do app: `HF_HUB_DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`, `HF_HUB_DISABLE_IMPLICIT_TOKEN=1`, `PYANNOTE_METRICS_ENABLED=0`.
- Modelos remotos/cloud de diarizacao sao bloqueados: `precision-2`, nomes com `cloud` e modelos `pyannoteAI`.

## Validacao de release

Em uma maquina limpa por plataforma:

1. Instalar o pacote sem Python e sem FFmpeg no sistema.
2. Abrir a GUI e executar `Configurar modelos...`.
3. Usar um token descartavel de leitura de um usuario comum.
4. Confirmar erro claro se os termos do pyannote nao foram aceitos.
5. Baixar modelos e rodar `models verify`.
6. Bloquear rede e transcrever um audio curto com separacao de falantes.
7. Procurar padroes `hf_` em logs, configs, jobs, JSON, CSV e saidas.
8. Confirmar que os arquivos originais do projeto nao foram alterados.
