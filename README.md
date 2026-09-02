# Transcritório

[![PyPI](https://img.shields.io/pypi/v/transcritorio)](https://pypi.org/project/transcritorio/)
[![License](https://img.shields.io/github/license/antrologos/Transcritorio)](LICENSE)
![Platforms](https://img.shields.io/badge/plataformas-Windows%20%7C%20macOS%20%7C%20Linux-informational)
[![Site](https://img.shields.io/badge/site-antrologos.github.io%2FTranscritorio-44d7b6)](https://antrologos.github.io/Transcritorio/pt/)

**Transcreva entrevistas sem enviar seu áudio para a nuvem.**
Aplicativo desktop gratuito para transcrição automática e separação de falantes em português brasileiro.

- **100% local** — o áudio nunca sai da sua máquina; compatível com LGPD e com qualquer TCLE razoável.
- **Português brasileiro nativo** — baseado no Whisper (modelo de transcrição de fala da OpenAI) treinado com ampla variação dialetal.
- **Gratuito e código aberto** — licença MIT, desenvolvido no IESP-UERJ / CERES. Sem assinatura, sem telemetria (o primeiro uso pede apenas uma conta gratuita da Hugging Face para baixar o modelo de separação de falantes).

Site do projeto: **[antrologos.github.io/Transcritorio](https://antrologos.github.io/Transcritorio/pt/)** (passo a passo com imagens)

## Instalação

O Transcritório é instalado pelo [uv](https://docs.astral.sh/uv/), que baixa o Python e todas as dependências **das fontes oficiais** (Microsoft, PyPI, PyTorch). É uma vez só; no dia a dia você abre pelo atalho da área de trabalho.

**Windows 10/11, sem terminal (recomendado)** — baixe o instalador de um clique e clique duas vezes nele:

**[⬇ Instalar-Transcritorio.bat](https://github.com/antrologos/Transcritorio/releases/latest/download/Instalar-Transcritorio.bat)**

Ele faz sozinho os três comandos abaixo, mostra o progresso e abre o programa no final (se o Windows perguntar "Deseja executar este arquivo?", confirme — o script é [auditável](scripts/Instalar-Transcritorio.bat) e só instala de fontes oficiais assinadas). Para atualizar depois: [Atualizar-Transcritorio.bat](https://github.com/antrologos/Transcritorio/releases/latest/download/Atualizar-Transcritorio.bat).

**Windows 10/11, pelo terminal** — abra o *Prompt de Comando* (menu Iniciar → digite `cmd` → Enter) e cole os três comandos, um por vez:

```bat
winget install astral-sh.uv
winget install Gyan.FFmpeg
uv tool install --python 3.12 transcritorio
```

Feche e reabra o Prompt, digite `transcritorio` e pressione Enter. O programa abre e cria o atalho **Transcritório** na área de trabalho — a partir daí, é só clicar nele.

Guia detalhado com solução de problemas: [`docs/INSTALL_WINDOWS.md`](docs/INSTALL_WINDOWS.md)

**macOS (beta)** — no Terminal, com [Homebrew](https://brew.sh):

```sh
brew install uv ffmpeg
uv tool install --python 3.12 transcritorio
```

Em Apple Silicon (M1/M2/M3/M4), use `uv tool install --python 3.12 "transcritorio[mac]"` para transcrever com aceleração Metal. Sem Gatekeeper: não há app para "autorizar". Guia: [`docs/MAC_INSTALL.md`](docs/MAC_INSTALL.md)

**Linux (beta)** — no terminal:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install ffmpeg   # ou o gerenciador da sua distribuição
uv tool install --python 3.12 transcritorio
```

Guia: [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md)

- **Atualizar:** `uv tool upgrade transcritorio` (o app avisa quando há versão nova).
- **Reparar:** menu **Ajuda → Reparar instalação** (não afeta projetos, áudios nem modelos).
- **Aceleração NVIDIA (opcional, 3–9× mais rápido):** menu **Ferramentas → Instalar aceleração NVIDIA (CUDA)…**.

> **Por que não tem mais instalador `.exe`?** As versões em instalador (.exe/.dmg/AppImage) foram descontinuadas: sem assinatura digital paga, antivírus e SmartScreen bloqueavam a instalação para boa parte dos usuários. O formato atual usa apenas componentes assinados pelos distribuidores oficiais e elimina esses bloqueios. Histórico e downloads antigos: [`docs/LEGACY_STANDALONE.md`](docs/LEGACY_STANDALONE.md).

---

## Para pesquisadores

### O que você consegue fazer

- **Importar** áudios e vídeos (MP3, WAV, M4A, MP4 e outros) — arraste para a janela, um arquivo ou uma pasta inteira.
- **Transcrever** em português brasileiro com alta acurácia (90–96% em áudios limpos), com **dois motores locais**: o **Parakeet pt-BR "TAGARELA"** (**o padrão em todas as máquinas** — treinado para o português falado, 1 h de áudio em poucos minutos mesmo só no processador) e o Whisper (nas variantes small a large-v3-turbo; a reserva para outros idiomas). Ao transcrever, o app pergunta quantas pessoas falam — entrevista a dois ou **grupo focal** (funciona bem até 6–8 participantes). Gravações em **15 outros idiomas** também transcrevem com tempos por palavra (16 pacotes de alinhamento, incluindo o português).
- **Separar falantes** automaticamente — e nomeá-los ouvindo amostras: o diálogo **"De quem é esta voz?"** toca trechos de cada voz para você dizer quem é. Vozes recorrentes do projeto (ex.: a sua) passam a ser **reconhecidas automaticamente**. Uma **verificação acústica** confere cada troca de falante e marca com 🔍 as trocas duvidosas, no ponto exato do áudio.
- **Revisar no Estúdio** com player sincronizado, forma de onda interativa, cores por falante e edição por bloco — o duplo clique numa palavra leva o áudio até ela. Painéis ajustáveis: recolha o vídeo, amplie os blocos, trabalhe do seu jeito.
- **Analisar com AI local** (✨ nada sai do seu computador): **resumo com índice temático** de cada entrevista, **glossário de nomes** do projeto com **revisão de grafias** (a AI encontra "Joao/João/Jono" e você decide, ocorrência por ocorrência, com a grafia certa editável), e **"Perguntar às entrevistas"** — uma pergunta em português, respondida com citações dos trechos.
- **Exportar** em DOCX, MD, SRT, VTT, CSV, TSV e formato NVivo (importa direto no NVivo, Atlas.ti, MAXQDA ou num script R/Python). A aba **Documentos** reúne tudo que o app produz, com data e botão de abrir.
- **Tudo offline** depois do download inicial dos modelos (uma única vez; o tamanho depende do perfil de instalação — ver abaixo).

### Requisitos por tipo de instalação

O assistente de primeiro uso examina a sua máquina e sugere o perfil
adequado — nada é imposto, e dá para mudar depois em **Ferramentas →
Gerenciar modelos…**. Números medidos (disco = aplicativo + modelos;
tempos da separação de falantes medidos na máquina de referência, de 24
threads — num notebook de 8 threads conte ~3×, com 4 threads ~5×):

| Perfil | O que faz | Máquina | Disco | 1 h de áudio |
|---|---|---|---|---|
| **Essencial** | Só transcrever (TAGARELA + `small`) | 2+ núcleos, 4 GB RAM | ~6 GB | poucos minutos (TAGARELA) |
| **Padrão** | + separar falantes + tempos por palavra | 4+ núcleos, 8 GB RAM | ~7,5 GB | transcrição em minutos; separação de falantes de ~4 min (24 threads) a ~22 min (4 threads) por hora de áudio — pode ficar para depois |
| **Padrão + GPU** | idem, com aceleração NVIDIA (TAGARELA + `large-v3-turbo` de reserva) | GPU NVIDIA 4 GB+ VRAM | ~10 GB | poucos minutos (separação de falantes ≈ 1 min) |
| **Completo** | + análise com AI local (resumo, glossário, perguntar) | GPU NVIDIA 6 GB+ VRAM, 16 GB RAM | ~20 GB | poucos minutos |

> **Motor padrão: TAGARELA.** Desde a v0.2.5 o **Parakeet pt-BR
> (TAGARELA)** é o padrão em todas as máquinas (antes, só nas sem placa
> de vídeo): treinado para o português falado — segundo os autores do
> modelo, em fala espontânea erra menos palavras que o Whisper large-v3
> (cerca de 14% contra 23%) —, tem pontuação e tempos por palavra
> nativos e transcreve de **13× a 25× mais rápido que o tempo real só no
> processador** (1 h de áudio em poucos minutos). Só transcreve
> português — para outros idiomas o app avisa e oferece um Whisper de
> reserva (`large-v3-turbo` com GPU, `small` sem). Quem instalou antes
> recebe a oferta de troca na própria lista de arquivos; também dá para
> trocar clicando no selo **Modelo** da barra inferior. E a **separação
> de falantes** ficou ~8× mais rápida desde a v0.2.3 (de ~4 min por hora
> de áudio numa máquina de 24 threads a ~22 min com 4 threads; ≈ 1 min
> com GPU): ao transcrever, a caixa
> **"Separar falantes agora"** permite deixá-la para depois e já revisar
> o texto.

### Primeiros passos

**1. Instale e abra.** Siga a seção **Instalação** acima (três comandos, uma vez). Depois, abra pelo atalho **Transcritório** da área de trabalho. No primeiro uso, um assistente em português prepara os modelos de IA — e pergunta se você quer a identificação de falantes (opcional).

**2. Crie um projeto.** Abra o Transcritório e vá em **Projeto → Novo projeto…** Dê um nome (ex.: `tese-entrevistas-2026`) e escolha uma pasta. O app cria uma estrutura `.transcricao` com áudios, transcrições e metadados lado a lado — fácil de fazer backup e arquivar.

**3. Adicione os áudios ou vídeos.** Clique em **Adicionar mídia…** (ou arraste arquivos para a janela). Ao transcrever, o app pergunta quantas pessoas falam (entrevista, grupo focal, número exato ou automático) — e **Editar propriedades…** permite ajustar por arquivo depois (idioma, falantes, rótulos).

**4. Clique em Transcrever e revise.** O botão **Transcrever** faz o fluxo completo: prepara o áudio, transcreve, separa os falantes e monta o texto editável. Ao final, o diálogo **"De quem é esta voz?"** toca uma amostra de cada voz para você nomeá-las — os nomes valem para a transcrição inteira. Tempos realistas para 1 hora de entrevista (ver a tabela de perfis acima): **poucos minutos** com o motor TAGARELA (o padrão), com ou sem placa de vídeo — a separação de falantes soma de ~4 min (24 threads) a ~22 min (4 threads) em CPU, ≈ 1 min com GPU NVIDIA; ou **~1–1,5 h** se optar pelo Whisper em CPU (o dobro em máquina de 4 núcleos). Depois, dê duplo clique na entrevista para abrir a transcrição, ouvir o áudio sincronizado com o texto, ajustar trechos com a forma de onda e exportar. Guia visual completo no [site do projeto](https://antrologos.github.io/Transcritorio/pt/#how).

> **Modelos de IA no primeiro uso:** o Transcritório baixa os modelos do perfil escolhido uma única vez (~3,5 GB só para transcrever — TAGARELA + small; ~5 GB somando identificação de falantes e tempos por palavra; com GPU, o `large-v3-turbo` de reserva soma ~3 GB); depois roda offline. A **identificação de falantes é opcional**: quem quer apenas transcrever não precisa de cadastro algum. Quem a ativa é orientado pelo assistente a criar uma conta gratuita na [Hugging Face](https://huggingface.co/), aceitar os termos do modelo pyannote e colar um *token* de leitura — tudo em português, e dá para ativar depois sem repetir as transcrições.

### Privacidade e ética

- **Processamento 100% local:** o áudio da entrevista nunca é enviado a servidores externos.
- **Sem coleta de dados, sem telemetria:** nenhum dado sai do seu computador. O único cadastro externo é a conta gratuita da Hugging Face, usada apenas uma vez para baixar o modelo de separação de falantes.
- **Código-fonte aberto sob licença MIT:** auditável por qualquer pessoa, incluindo o setor de TI da sua instituição.
- **Compatível com LGPD e TCLE:** você mantém controle integral sobre o áudio do informante e pode demonstrar a cadeia de custódia dos dados.

**Texto pronto para submissão ao CEP** (copie e cole no seu projeto de pesquisa):

> A transcrição e a separação automática de falantes dos áudios coletados nesta pesquisa serão realizadas por meio do software Transcritório (Barbosa, 2026), uma aplicação de desktop gratuita e de código aberto (licença MIT), desenvolvida no IESP-UERJ/CERES. Todo o processamento ocorre localmente na máquina do pesquisador, sem envio do material a servidores externos, em conformidade com a Lei nº 13.709/2018 (LGPD) e com o TCLE assinado pelos participantes. O software utiliza os modelos Whisper (Radford et al., 2022) para transcrição e pyannote.audio (Bredin et al., 2020) para separação de falantes, ambos executados offline.

### Como citar

Barbosa, R. J. (2026). *Transcritório: transcrição local de entrevistas em português brasileiro* (v0.2.0) [Software]. IESP-UERJ/CERES. https://github.com/antrologos/Transcritorio

```bibtex
@software{barbosa2026transcritorio,
  author    = {Barbosa, Rog{\'e}rio Jer{\^o}nimo},
  title     = {Transcrit{\'o}rio: transcri{\c{c}}{\~a}o local de entrevistas em portugu{\^e}s brasileiro},
  year      = {2026},
  version   = {0.2.0},
  publisher = {IESP-UERJ/CERES},
  license   = {MIT},
  url       = {https://github.com/antrologos/Transcritorio}
}
```

O GitHub também exibe o botão **"Cite this repository"** no menu lateral, com os mesmos dados em formato APA e BibTeX, lendo o arquivo [`CITATION.cff`](CITATION.cff).

Modelos de IA utilizados:
- Radford, A., Kim, J. W., Xu, T., Brockman, G., McLeavey, C., & Sutskever, I. (2022). *Robust speech recognition via large-scale weak supervision*. arXiv. https://arxiv.org/abs/2212.04356
- Bredin, H., Yin, R., Coria, J. M., Gelly, G., Korshunov, P., Lavechin, M., Fustes, D., Titeux, H., Bouaziz, W., & Gill, M.-P. (2020). *pyannote.audio: neural building blocks for speaker diarization*. ICASSP 2020. https://arxiv.org/abs/1911.01255

---

## Para desenvolvedores

Se você quer rodar do código-fonte, contribuir com pull requests ou auditar o pipeline:

- **Setup de ambiente, CLI e primeiros commits:** [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- **Arquitetura do pipeline e estrutura de arquivos:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Histórico de experimentos e decisões de modelo (testes A/B, variants):** [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md)
- **Checklist pré-release (canal standalone legado):** [`docs/PACKAGING_CHECKLIST.md`](docs/PACKAGING_CHECKLIST.md)
- **Segurança de tokens:** [`docs/SEGURANCA_SEGREDOS.md`](docs/SEGURANCA_SEGREDOS.md)
- **Instalação no macOS:** [`docs/MAC_INSTALL.md`](docs/MAC_INSTALL.md)
- **Instalação no Linux:** [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md)
- **Aceleração MLX no Apple Silicon:** [`docs/MLX_WHISPER_MACOS.md`](docs/MLX_WHISPER_MACOS.md)
- **Troubleshooting macOS/Linux:** [`docs/MAC_LINUX.md`](docs/MAC_LINUX.md)
- **Code signing no Windows (encerrado — canal standalone aposentado):** [`docs/WINDOWS_CODE_SIGNING.md`](docs/WINDOWS_CODE_SIGNING.md)

### Estrutura do repositório

```
transcribe_pipeline/    pacote Python principal (GUI, CLI, runners, render)
scripts/                instaladores .bat de duplo clique + wrappers CMD/PS1 (dev)
packaging/              (LEGADO) spec do PyInstaller, Inno Setup, hooks
tests/                  toy tests (isolados) e smoke tests
docs/                   documentação completa
.github/workflows/      ci.yml (testes) e publish.yml (tag v* → PyPI);
                        release.yml é o build standalone LEGADO (manual)
```

---

## Status

| Plataforma | Estado | Notas |
|---|---|---|
| Windows 10/11 | Suportada | CPU por padrão; aceleração NVIDIA opcional pelo extra `[cuda]` (menu do app). |
| Linux x64 | Beta | Mesmo canal `uv tool install --python 3.12 transcritorio`; CPU. |
| macOS (Apple Silicon) | Beta | `uv tool install --python 3.12 "transcritorio[mac]"` habilita a aceleração Metal (MLX). |

As versões em instalador (.exe/.dmg/AppImage) foram descontinuadas — ver [`docs/LEGACY_STANDALONE.md`](docs/LEGACY_STANDALONE.md).

Histórico do desenvolvimento (era do app standalone): [`docs/STANDALONE_APP_ROADMAP.md`](docs/STANDALONE_APP_ROADMAP.md).

## Contribuir e reportar bugs

- Bugs e sugestões: [GitHub Issues](https://github.com/antrologos/Transcritorio/issues).
- Discussões de metodologia e uso em pesquisa qualitativa são bem-vindas no mesmo canal.
- Pull requests: siga o estilo do código existente; toy tests passando em Windows/Linux/macOS; sem refatoração além do escopo.

## Licença e autoria

Software distribuído sob **licença MIT** (veja [`LICENSE`](LICENSE)).
Autor: **Rogério Jerônimo Barbosa** — IESP-UERJ / CERES — [antrologos.github.io](https://antrologos.github.io/) — [ORCID 0000-0002-6796-4547](https://orcid.org/0000-0002-6796-4547).

Agradecimentos às bibliotecas e modelos sobre os quais este projeto se apoia: [Whisper](https://github.com/openai/whisper) (OpenAI), [WhisperX](https://github.com/m-bain/whisperX), [faster-whisper](https://github.com/SYSTRAN/faster-whisper)/CTranslate2, [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), [pyannote.audio](https://github.com/pyannote/pyannote-audio), [Parakeet pt-BR](https://huggingface.co/nvidia) (NVIDIA) via [onnx-asr](https://github.com/istupakov/onnx-asr), [Qwen](https://github.com/QwenLM) (análise local), [GLiNER](https://github.com/urchade/GLiNER) (nomes), [PyTorch](https://pytorch.org/), [PySide6/Qt](https://pypi.org/project/PySide6/), [FFmpeg](https://ffmpeg.org/) e [uv](https://docs.astral.sh/uv/) (Astral).

No canal atual, o ffmpeg/ffprobe vêm do gerenciador de pacotes do sistema (winget/brew/apt) — nada é embutido. Nas releases legadas em instalador, o ffmpeg/ffprobe embutidos eram builds GPL de terceiros (BtbN para Windows, evermeet.cx para macOS, johnvansickle.com para Linux); veja [`NOTICE`](NOTICE) para a lista de componentes dessas versões e seus termos.
