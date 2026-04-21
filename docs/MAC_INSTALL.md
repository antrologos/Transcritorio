# Como instalar o Transcritorio no Mac

Guia passo a passo para pesquisadores instalando o Transcritorio pela
primeira vez em um Mac (M1, M2, M3 ou posterior).

## O que baixar

1. Va ate a pagina de Releases: https://github.com/antrologos/Transcritorio/releases/latest
2. Baixe o arquivo **`Transcritorio.dmg`**
3. Aguarde o download terminar (arquivo grande — pode levar alguns minutos)

## Primeira execucao — IMPORTANTE

> **Sobre a mensagem de seguranca do macOS**
>
> Apple exige que aplicativos sejam assinados com um certificado pago
> (US$ 99/ano). Por ora o Transcritorio **nao e assinado**, entao na
> primeira vez que voce abrir, o Mac vai mostrar uma mensagem de
> seguranca. Isso **nao significa que o programa e perigoso** — so
> significa que ele vem do GitHub diretamente, sem o carimbo da Apple.
>
> Siga os passos abaixo para abrir a primeira vez. Depois disso, o Mac
> lembra e nao pergunta mais.

### Passo 1 — Instalar

1. Clique duas vezes no `Transcritorio.dmg` baixado
2. Arraste o icone do **Transcritorio** para a pasta **Applications**
3. Ejete o disco (clique na seta ao lado do nome na barra lateral do Finder)

### Passo 2 — Abrir pela primeira vez (IMPORTANTE)

Nao clique duas vezes — **o Mac vai bloquear**. Em vez disso:

1. Abra a pasta **Applications** no Finder
2. Localize o icone do **Transcritorio**
3. **Clique com o botao direito** no icone (ou Ctrl+click)
4. Escolha **Abrir** no menu que aparece
5. Quando aparecer a caixa "macOS nao pode verificar...", clique em **Abrir**

A partir daqui o Mac lembra, e nas proximas vezes voce pode abrir
normalmente pelo Launchpad, Spotlight ou clicando duas vezes.

### Alternativa 1 — se nao aparecer o botao "Abrir"

Em alguns macOS mais novos a opcao "Abrir" no menu direito vem
desabilitada. Nesse caso:

1. Abra **Configuracoes do Sistema** → **Privacidade e Seguranca**
2. Role ate embaixo, onde aparece uma mensagem:
   *"Transcritorio foi bloqueado porque nao e de um desenvolvedor
   identificado."*
3. Clique em **Abrir Mesmo Assim**
4. Digite sua senha do Mac se pedido

### Alternativa 2 — macOS 15 Sequoia (se depois do "Open Anyway" o app **ainda nao abre**)

O macOS 15.1+ endureceu o Gatekeeper e em alguns cenarios bloqueia o
lancamento mesmo depois do "Abrir Mesmo Assim". Felizmente, **incluimos
um script de ajuda dentro do proprio `.dmg`**.

**IMPORTANTE**: em macOS 15.1+, o proprio duplo-clique no script tambem
pode ser bloqueado. O caminho confiavel e abrir o Terminal primeiro e
depois **arrastar** o script pra dentro:

1. Monte o `Transcritorio.dmg` novamente (duplo-clique no arquivo
   baixado).
2. Abra o **Terminal** (Spotlight → digite "Terminal" → Enter; ou
   Applications → Utilities → Terminal).
3. Com o Terminal aberto, volte pro Finder. No conteudo do DMG voce
   vai ver um arquivo chamado **`Habilitar Transcritorio.command`**.
4. **Arraste** esse arquivo de dentro do DMG para a janela do Terminal.
   Um caminho aparece na linha de comando do Terminal.
5. **Aperte Enter**. O script roda, mostra "pronto" e fecha.

Depois disso, abra o Transcritorio normalmente (Launchpad, Spotlight ou
duplo-clique em Applications).

O script apenas remove o flag de "quarentena" que o navegador colocou
no arquivo quando voce baixou — procedimento oficial da Apple
documentado em [support.apple.com/guide/security/gatekeeper](https://support.apple.com/guide/security/gatekeeper-and-runtime-protection-sec5599b66df/web).
Nao desliga nenhuma protecao do seu Mac.

### Alternativa 3 — fazer manualmente no Terminal (se nada acima funcionar)

Abra o **Terminal** (Applications → Utilities → Terminal, ou Spotlight
"Terminal") e cole **uma linha**:

```sh
xattr -dr com.apple.quarantine /Applications/Transcritorio.app
```

Pressione **Enter**. Se pedir senha, digite a do seu Mac. Depois disso,
o Transcritorio abre normalmente.

### Se mesmo assim nao abrir: me envie o diagnostico

O script `Habilitar Transcritorio.command` (dentro do DMG) faz mais do
que so tentar o fix: ele coleta informacoes do seu sistema (versao do
macOS, chip, estado da assinatura do app, logs do Gatekeeper) e salva
um arquivo de texto no seu Desktop chamado
`transcritorio-diagnostico-AAAAMMDD_HHMMSS.txt`.

Se o Transcritorio ainda nao abrir depois de rodar o script, por favor
me envie esse arquivo por email ou WhatsApp. Com ele eu consigo ver
exatamente o que o seu Mac esta bloqueando — bem melhor do que tentar
adivinhar por screenshots de mensagens genericas do sistema.

Voce nao precisa ler o arquivo antes de enviar; nenhuma informacao
pessoal vai nele (so detalhes tecnicos do sistema e do app).

## FFmpeg vem embutido

A partir da v0.1.1, o `.dmg` ja inclui `ffmpeg` e `ffprobe` compilados
para Apple Silicon. **Voce nao precisa instalar nada pelo Terminal** —
nem Homebrew, nem Xcode Command Line Tools, nem brew install.

Se voce ja instalou o FFmpeg antes por outros motivos, tudo bem — o
Transcritorio usa o que vem dentro do `.app`, sem conflito com o que
estiver no seu sistema.

## Desempenho no Mac

No Apple Silicon (M1/M2/M3/M4), a partir da v0.1.1 o Transcritorio usa
**MLX/Metal** — o acelerador grafico da Apple — para transcrever. Um
selo **Motor: MLX (Metal)** aparece no cabecalho do projeto quando a
aceleracao esta ativa.

- **Audio de 1 hora** leva aproximadamente **10 a 15 minutos** num Mac
  M2/M3. Em M1 um pouco mais, em M4 um pouco menos.
- Na **primeira transcricao**, o modelo otimizado pra Metal (~1,6 GB)
  e baixado em segundo plano. A barra de progresso sobe lentamente
  (1% → 89%) durante o download, depois salta pra 100% ao finalizar.
  Isso e esperado; o app nao travou.
- Se por algum motivo o MLX nao estiver disponivel (instalacao
  incompleta, etc.), o Transcritorio cai automaticamente para CPU. O
  selo do cabecalho mostra **Motor: CPU** e o tempo sobe para 45-60
  min por hora de audio.

## Desinstalar

Basta arrastar o icone do **Transcritorio** da pasta Applications para
a **Lixeira**.

Opcionalmente, apague os dados do app:

```sh
rm -rf ~/Library/Application\ Support/Transcritorio
```

Seus arquivos de projeto (`.transcritorio`) ficam onde voce os salvou
e **nao sao apagados** pela desinstalacao.

## Problemas comuns

### "Transcritorio.app nao pode ser aberto porque o desenvolvedor..."

Sim, e a mensagem do Gatekeeper. Siga o **Passo 2** acima.

### "ffmpeg: command not found" ao transcrever

Volte na secao "Pre-requisito: FFmpeg" e rode `brew install ffmpeg`.

### Apareceu uma caixa pedindo acesso ao microfone

O Transcritorio **nao usa o microfone** — so trabalha com arquivos ja
gravados. A caixa aparece porque o framework de multimedia do macOS
exige essa declaracao mesmo que o app nao use. Pode clicar em
**Nao Permitir** sem problema.

### O programa esta muito lento

Normal no Mac (ver secao Desempenho acima). Para audio longo,
deixe rodando e va fazer outra coisa.

### Erro no primeiro download de modelos

- Verifique sua conexao com internet
- Se voce usa VPN corporativa, desligue temporariamente (proxy pode
  bloquear o download do Hugging Face)
- Certifique-se de que aceitou os termos do modelo `pyannote` em
  https://huggingface.co/pyannote/speaker-diarization-community-1
  (requer login gratuito)

## Reportar bugs

Via GitHub Issues: https://github.com/antrologos/Transcritorio/issues

Inclua:
- Versao do macOS (menu Apple → Sobre este Mac)
- Chip (M1, M2, etc.)
- Passo onde travou
- Screenshot ou texto do erro
- Log em `~/Library/Application Support/Transcritorio/logs/gui.log`

Label `platform-macos` ajuda a triagem.
