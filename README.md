# Transcritório

[![Release](https://img.shields.io/github/v/release/antrologos/Transcritorio)](https://github.com/antrologos/Transcritorio/releases/latest)
[![License](https://img.shields.io/github/license/antrologos/Transcritorio)](LICENSE)
![Platforms](https://img.shields.io/badge/plataformas-Windows%20%7C%20macOS%20%7C%20Linux-informational)
[![Site](https://img.shields.io/badge/site-antrologos.github.io%2FTranscritorio-44d7b6)](https://antrologos.github.io/Transcritorio/pt/)

**Transcreva entrevistas sem enviar seu áudio para a nuvem.**
Aplicativo desktop gratuito para transcrição automática e separação de falantes em português brasileiro.

- **100% local** — o áudio nunca sai da sua máquina; compatível com LGPD e com qualquer TCLE razoável.
- **Português brasileiro nativo** — baseado no Whisper (modelo de transcrição de fala da OpenAI) treinado com ampla variação dialetal.
- **Gratuito e código aberto** — licença MIT, desenvolvido no IESP-UERJ / CERES. Sem cadastro, sem assinatura, sem telemetria.

Site do projeto: **[antrologos.github.io/Transcritorio](https://antrologos.github.io/Transcritorio/pt/)** (passo a passo com imagens)
Baixar: **[Releases](https://github.com/antrologos/Transcritorio/releases/latest)**

| Sistema | Arquivo | Instrução rápida |
|---|---|---|
| **Windows 10/11** | `Transcritorio-0.1.7-Setup.exe` | Clique duas vezes no `.exe`. Se tiver placa NVIDIA, o app detecta e oferece a aceleração CUDA opcional (+1 GB). |
| **macOS** (Apple Silicon M1–M4) | `Transcritorio.dmg` | Arraste para Aplicativos. Primeira vez: botão direito no ícone → **Abrir** (Gatekeeper). Aceleração Metal automática. |
| **Linux** (Ubuntu 22.04+, Fedora 40+) | `Transcritorio-x86_64.AppImage` | `chmod +x` e execute. Requer apenas libs X11 do sistema (veja [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md)). |

---

## Para pesquisadores

### O que você consegue fazer

- **Importar** áudios e vídeos (MP3, WAV, M4A, MP4 e outros) — um arquivo, uma pasta, ou uma lista.
- **Transcrever** em português brasileiro com alta acurácia (90–96% em áudios limpos).
- **Separar falantes** automaticamente — identifica quem falou em cada trecho (entrevistador, entrevistado, etc.).
- **Revisar no Estúdio** com player sincronizado, forma de onda interativa e edição por bloco.
- **Exportar** em DOCX, MD, SRT, VTT, CSV, TSV e formato NVivo.
- **Tudo offline** depois do download inicial dos modelos (~3 GB, uma única vez).

### Requisitos mínimos

| | Mínimo | Recomendado | Ideal |
|---|---|---|---|
| CPU | 4 núcleos | 8 núcleos | 8+ núcleos |
| RAM | 8 GB | 16 GB | 16 GB+ |
| Disco | 5 GB livres | 10 GB | 10 GB |
| GPU | — | — | NVIDIA com 6 GB+ VRAM ou Apple Silicon |
| 1 h de áudio | ~40–60 min | ~20–30 min | ~5–10 min |

### Primeiros passos

**1. Baixe e instale.** Use o arquivo da tabela acima. No Windows, o Defender pode exibir um aviso azul na primeira execução — clique em "Mais informações" → "Executar assim mesmo"; isso acontece porque o instalador não é assinado digitalmente, não porque tenha algo errado. No macOS, o botão direito → **Abrir** só é necessário na primeira vez.

**2. Crie um projeto.** Abra o Transcritório e vá em **Projeto → Novo projeto…** Dê um nome (ex.: `tese-entrevistas-2026`) e escolha uma pasta. O app cria uma estrutura `.transcricao` com áudios, transcrições e metadados lado a lado — fácil de fazer backup e arquivar.

**3. Adicione os áudios ou vídeos.** Clique em **Adicionar mídia…** (ou arraste arquivos para a janela). Use **Editar propriedades…** para definir idioma (Português brasileiro), número aproximado de falantes e rótulos (ex.: Entrevistador, Informante).

**4. Clique em Transcrever e revise no Estúdio.** O botão **Transcrever** faz o fluxo completo: prepara o áudio, transcreve, separa os falantes e monta o texto editável. Tempos realistas para 1 hora de entrevista: **~5–10 min** em máquina com GPU NVIDIA ou Apple Silicon, **~20–30 min** em notebook recente sem GPU, **~40–60 min** em máquina modesta. Ao final, abra o **Estúdio de Revisão** para ouvir o áudio sincronizado com o texto, ajustar trechos com a forma de onda e exportar. Guia visual completo no [site do projeto](https://antrologos.github.io/Transcritorio/pt/#how).

> **Modelos de IA no primeiro uso:** o Transcritório baixa cerca de 3 GB de modelos de IA uma única vez; depois roda offline. No fluxo padrão isso acontece sem cadastro. Só é necessário um *token* da [Hugging Face](https://huggingface.co/) (plataforma que hospeda os modelos, gratuita) em cenários avançados. Wizard em português em **Configurações → Configurar modelos…**

### Privacidade e ética

- **Processamento 100% local:** o áudio da entrevista nunca é enviado a servidores externos.
- **Sem coleta de dados, sem telemetria:** nenhum cadastro ou login é exigido para usar o aplicativo.
- **Código-fonte aberto sob licença MIT:** auditável por qualquer pessoa, incluindo o setor de TI da sua instituição.
- **Compatível com LGPD e TCLE:** você mantém controle integral sobre o áudio do informante e pode demonstrar a cadeia de custódia dos dados.

**Texto pronto para submissão ao CEP** (copie e cole no seu projeto de pesquisa):

> A transcrição e a separação automática de falantes dos áudios coletados nesta pesquisa serão realizadas por meio do software Transcritório (Barbosa, 2026), uma aplicação de desktop gratuita e de código aberto (licença MIT), desenvolvida no IESP-UERJ/CERES. Todo o processamento ocorre localmente na máquina do pesquisador, sem envio do material a servidores externos, em conformidade com a Lei nº 13.709/2018 (LGPD) e com o TCLE assinado pelos participantes. O software utiliza os modelos Whisper (Radford et al., 2022) para transcrição e pyannote.audio (Bredin et al., 2020) para separação de falantes, ambos executados offline.

### Como citar

Barbosa, R. J. (2026). *Transcritório: transcrição local de entrevistas em português brasileiro* (v0.1.7) [Software]. IESP-UERJ/CERES. https://github.com/antrologos/Transcritorio

```bibtex
@software{barbosa2026transcritorio,
  author    = {Barbosa, Rog{\'e}rio Jer{\^o}nimo},
  title     = {Transcrit{\'o}rio: transcri{\c{c}}{\~a}o local de entrevistas em portugu{\^e}s brasileiro},
  year      = {2026},
  version   = {0.1.7},
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
- **Checklist pré-release (bundle completo):** [`docs/PACKAGING_CHECKLIST.md`](docs/PACKAGING_CHECKLIST.md)
- **Segurança de tokens:** [`docs/SEGURANCA_SEGREDOS.md`](docs/SEGURANCA_SEGREDOS.md)
- **Instalação a partir do source em macOS:** [`docs/MAC_INSTALL.md`](docs/MAC_INSTALL.md)
- **Instalação a partir do source em Linux:** [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md)
- **Aceleração MLX no Apple Silicon:** [`docs/MLX_WHISPER_MACOS.md`](docs/MLX_WHISPER_MACOS.md)
- **Troubleshooting macOS/Linux (referência consolidada):** [`docs/MAC_LINUX.md`](docs/MAC_LINUX.md)
- **Code signing no Windows (backlog, opções e passo a passo):** [`docs/WINDOWS_CODE_SIGNING.md`](docs/WINDOWS_CODE_SIGNING.md)

### Estrutura do repositório

```
transcribe_pipeline/    pacote Python principal (GUI, CLI, runners, render)
scripts/                wrappers CMD/PS1 para Windows
packaging/              spec do PyInstaller, Inno Setup, hooks, bundle filters
tests/                  toy tests (isolados) e smoke tests
docs/                   documentação completa
.github/workflows/      CI e release multi-plataforma
```

---

## Status

| Plataforma | Estado | Notas |
|---|---|---|
| Windows 10/11 | Estável | Aceleração CUDA opcional (pack separado, detecção automática). |
| Linux (AppImage) | Estável | CPU only no bundle distribuído. CUDA requer rodar do source (veja [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)). |
| macOS (Apple Silicon) | Em validação | Aceleração Metal (MLX) integrada e embutida no `.dmg`; teste em hardware real pendente. |

Roadmap e histórico em [`docs/STANDALONE_APP_ROADMAP.md`](docs/STANDALONE_APP_ROADMAP.md).

## Contribuir e reportar bugs

- Bugs e sugestões: [GitHub Issues](https://github.com/antrologos/Transcritorio/issues).
- Discussões de metodologia e uso em pesquisa qualitativa são bem-vindas no mesmo canal.
- Pull requests: siga o estilo do código existente; toy tests passando em Windows/Linux/macOS; sem refatoração além do escopo.

## Licença e autoria

Software distribuído sob **licença MIT** (veja [`LICENSE`](LICENSE)).
Autor: **Rogério Jerônimo Barbosa** — IESP-UERJ / CERES — [antrologos.github.io](https://antrologos.github.io/) — [ORCID 0000-0002-6796-4547](https://orcid.org/0000-0002-6796-4547).

Agradecimentos às bibliotecas sobre as quais este projeto se apoia: [WhisperX](https://github.com/m-bain/whisperX), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), [pyannote.audio](https://github.com/pyannote/pyannote-audio), [PySide6](https://pypi.org/project/PySide6/), [FFmpeg](https://ffmpeg.org/).

O ffmpeg/ffprobe embutidos nos binários distribuídos são builds GPL de terceiros (BtbN para Windows, evermeet.cx para macOS, johnvansickle.com para Linux). Veja [`NOTICE`](NOTICE) para a lista completa de componentes bundled e seus termos.
