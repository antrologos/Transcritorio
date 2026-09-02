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

## Instalação em um clique (recomendada)

1. Baixe o instalador:
   **[Instalar-Transcritorio.bat](https://github.com/antrologos/Transcritorio/releases/latest/download/Instalar-Transcritorio.bat)**
2. Clique duas vezes no arquivo baixado. Se o Windows perguntar
   *"Deseja executar este arquivo?"*, confirme — o script é público e
   [auditável](../scripts/Instalar-Transcritorio.bat): ele só instala
   componentes assinados das fontes oficiais (Microsoft, Astral, PyPI),
   não pede senha de administrador e não grava nada fora do seu perfil
   de usuário.
3. Espere a janela terminar (alguns minutos). O Transcritório abre
   sozinho no final e cria o atalho na área de trabalho.

Para atualizar depois, o mesmo gesto:
[Atualizar-Transcritorio.bat](https://github.com/antrologos/Transcritorio/releases/latest/download/Atualizar-Transcritorio.bat).

## Passo a passo pelo terminal (equivalente)

**1. Abra o Prompt de Comando.** Menu Iniciar → digite `cmd` → Enter.
Vai abrir uma janela preta de texto — é normal, você só vai colar três
comandos nela.

**2. Cole os comandos, um por vez** (botão direito cola no Prompt), e
pressione Enter após cada um, aguardando terminar:

```bat
winget install astral-sh.uv
winget install Gyan.FFmpeg
uv tool install --python 3.12 transcritorio
```

- O 1º instala o `uv` (gerenciador). O 2º instala o FFmpeg (leitura de
  áudio/vídeo). O 3º baixa o Transcritório e suas dependências (~2,5 GB —
  pode levar alguns minutos).
- Se o winget pedir para aceitar termos de origem, digite `Y` e Enter.

**3. Feche o Prompt, abra um novo, digite `transcritorio` e Enter.**
O programa abre pela primeira vez e cria o atalho **Transcritório** na
área de trabalho. Das próximas vezes, é só clicar no atalho.

**4. Siga o assistente de primeiro uso.** Ele baixa os modelos de IA
(uma vez só) e pergunta se você quer a **identificação de falantes**:

- **Não (apenas transcrever):** nenhum cadastro é necessário. ~3,5 GB
  de modelos (perfil Essencial: em máquina sem placa de vídeo, o motor
  rápido TAGARELA + o Whisper small de reserva).
- **Sim (separar falantes):** o assistente orienta a criar uma conta
  gratuita na Hugging Face e colar um token. ~5 GB de modelos
  (perfil Padrão em CPU; ~3 GB só o modelo grande para GPU). Dá para
  ativar depois, sem repetir as transcrições.

## Aceleração NVIDIA (opcional)

Se o computador tem placa NVIDIA, a transcrição pode ficar 3–9× mais
rápida. Dentro do programa: **Ferramentas → Instalar aceleração NVIDIA
(CUDA)…** — ele confere a placa e mostra o comando a executar (com o
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

**A barra parece parada na "separação de falantes"** — sem placa de
vídeo, essa é a etapa demorada: de 25 min a ~1 h por hora de áudio,
conforme o processador (a transcrição em si leva minutos). Desde a
v0.2.3 a barra mostra o progresso real e uma estimativa; e ao clicar em
Transcrever dá para desmarcar **"Separar falantes agora"** — o texto fica
pronto em minutos e a lista oferece completar as vozes depois, em lote.

**"transcritorio não é reconhecido" logo após instalar** — mesmo motivo,
com uma pegadinha: se o Windows Terminal já estava aberto, *abas novas
herdam o ambiente antigo* — é preciso fechar TODAS as janelas do
terminal e abrir uma nova. O aplicativo em si já está instalado: dá
para abri-lo direto pelo caminho completo, sem esperar o PATH:

```bat
%APPDATA%\uv\tools\transcritorio\Scripts\transcritorio.exe
```

(no primeiro uso ele cria o atalho "Transcritório" na área de trabalho,
e daí em diante é só clicar nele).

**"O download ou a instalação do Transcritório falhou"** — o instalador
tenta duas vezes sozinho; se falhar de novo, ele mostra as últimas
linhas do registro e grava o arquivo completo em
`%TEMP%\Transcritorio-instalador.log` (cole esse caminho na barra do
Explorer). Mande esse arquivo (ou uma foto da tela) para quem lhe passou
o instalador. Causas comuns: rede institucional bloqueando downloads
(abaixo), conexão lenta (rode de novo — continua de onde parou) e pouco
espaço em disco (são ~4 GB livres).

**Rede institucional com proxy** — o download pode ser bloqueado. Peça ao
setor de TI para liberar `pypi.org`, `files.pythonhosted.org`,
`github.com` (de onde o `uv` baixa o Python quando o computador não tem
nenhum), `astral.sh` e `huggingface.co` — ou instale em casa ou no
hotspot do celular.

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

**"No solution found... torchcodec... cp314"** — o `uv` escolheu o
Python mais novo do computador (3.14) e uma dependência ainda não tem
pacote para ele — o teto de versão do Transcritório não faz o `uv`
trocar de interpretador sozinho. Por isso todos os comandos deste guia
(e o instalador de um clique) fixam `--python 3.12`: o `uv` baixa o
Python 3.12 oficial se não houver nenhum. Se você rodou sem esse
trecho, repita o comando com ele.

**Diagnóstico para pedir ajuda** — Prompt de Comando:
`transcritorio-cli self-test` — copie a saída e cole em uma
[issue no GitHub](https://github.com/antrologos/Transcritorio/issues).
