# Instalar o Transcritório no Windows

Guia para pesquisadores, sem conhecimento técnico necessário. A instalação
é feita **uma única vez** com três comandos; no dia a dia você abre o
programa pelo atalho da área de trabalho, como qualquer outro aplicativo.

## Por que assim, e não um instalador `.exe`?

As versões antigas em instalador eram bloqueadas por antivírus e pelo
SmartScreen do Windows ("fornecedor desconhecido"), porque softwares sem
assinatura digital paga são tratados com desconfiança. O formato atual
usa apenas componentes assinados pelos distribuidores oficiais:

- o **winget** é da própria Microsoft (já vem no Windows 10/11);
- o **uv** é assinado pela Astral e baixa o Python oficial;
- o Transcritório e as dependências vêm do **PyPI**, o repositório
  público de pacotes Python — com versões travadas e auditáveis.

## Passo a passo

**1. Abra o Prompt de Comando.** Menu Iniciar → digite `cmd` → Enter.
Vai abrir uma janela preta de texto — é normal, você só vai colar três
comandos nela.

**2. Cole os comandos, um por vez** (botão direito cola no Prompt), e
pressione Enter após cada um, aguardando terminar:

```bat
winget install astral-sh.uv
winget install Gyan.FFmpeg
uv tool install transcritorio
```

- O 1º instala o `uv` (gerenciador). O 2º instala o FFmpeg (leitura de
  áudio/vídeo). O 3º baixa o Transcritório e suas dependências (~2 GB —
  pode levar alguns minutos).
- Se o winget pedir para aceitar termos de origem, digite `Y` e Enter.

**3. Feche o Prompt, abra um novo, digite `transcritorio` e Enter.**
O programa abre pela primeira vez e cria o atalho **Transcritório** na
área de trabalho. Das próximas vezes, é só clicar no atalho.

**4. Siga o assistente de primeiro uso.** Ele baixa os modelos de IA
(uma vez só) e pergunta se você quer a **identificação de falantes**:

- **Não (apenas transcrever):** nenhum cadastro é necessário. ~5 GB.
- **Sim (separar falantes):** o assistente orienta a criar uma conta
  gratuita na Hugging Face e colar um token. ~7 GB. Dá para ativar
  depois, sem repetir as transcrições.

## Aceleração NVIDIA (opcional)

Se o computador tem placa NVIDIA, a transcrição pode ficar 3–9× mais
rápida. Dentro do programa: **Transcrever → Instalar aceleração NVIDIA
(CUDA)...** — ele confere a placa e mostra o comando a executar (com o
programa fechado). O download é grande (~2,5 GB) e é opcional: sem ele,
tudo funciona normalmente no processador.

## Atualizar, reparar, desinstalar

| Ação | Como |
|---|---|
| Atualizar | Prompt de Comando: `uv tool upgrade transcritorio` (o app avisa quando há versão nova) |
| Reparar | Menu **Ajuda → Reparar instalação...** (reconstrói só o ambiente técnico; projetos, áudios e modelos ficam intactos) |
| Desinstalar | `uv tool uninstall transcritorio` (os projetos ficam onde você os salvou) |

## Solução de problemas

**"winget não é reconhecido"** — Windows sem o *App Installer*. Instale
pela Microsoft Store (procure "App Installer") ou baixe o uv direto:
https://docs.astral.sh/uv/getting-started/installation/ — e o FFmpeg em
https://www.gyan.dev/ffmpeg/builds/ (adicione a pasta `bin` ao PATH).

**"uv não é reconhecido" no passo 3** — feche e reabra o Prompt de
Comando (o PATH só atualiza em janelas novas).

**Rede institucional com proxy** — o download do PyPI pode ser bloqueado.
Peça ao setor de TI para liberar `pypi.org`, `files.pythonhosted.org`,
`astral.sh` e `huggingface.co`, ou instale em uma rede doméstica.

**Antivírus sinaliza algo** — a instalação usa só componentes oficiais;
alarmes sobre `python.exe` em `%LOCALAPPDATA%` são falsos positivos. O
código-fonte é auditável em https://github.com/antrologos/Transcritorio.

**"foi bloqueado pela política do Device Guard da sua organização"** — a
máquina (pessoal ou corporativa) tem uma política de integridade de código
que só executa binários com assinatura digital, e o atalho `.exe` criado
pelo `uv` não é assinado. Solução: usar um Python oficial assinado
(Microsoft/PSF) como base e abrir sem o atalho:

```bat
winget install Python.Python.3.12
uv tool uninstall transcritorio
uv tool install --python 3.12 --python-preference only-system transcritorio
%APPDATA%\uv\tools\transcritorio\Scripts\pythonw.exe -m transcribe_pipeline.gui_launcher
```

Em máquina gerenciada pela instituição, pode ser preciso pedir uma exceção
à TI (cite a licença MIT e o repositório público).

**"No solution found... torchcodec... cp314"** — versão antiga do pacote
sem teto de Python (corrigido a partir do beta 0.2.0b2); se aparecer,
acrescente `--python 3.12` logo após `uv tool install`.

**Diagnóstico para pedir ajuda** — Prompt de Comando:
`transcritorio-cli self-test` — copie a saída e cole em uma
[issue no GitHub](https://github.com/antrologos/Transcritorio/issues).
