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

### Alternativa (se o item 4 acima nao aparecer)

Em alguns macOS mais novos a opcao "Abrir" no menu direito vem
desabilitada. Nesse caso:

1. Abra **Configuracoes do Sistema** → **Privacidade e Seguranca**
2. Role ate embaixo, onde aparece uma mensagem:
   *"Transcritorio foi bloqueado porque nao e de um desenvolvedor
   identificado."*
3. Clique em **Abrir Mesmo Assim**
4. Digite sua senha do Mac se pedido

## Pre-requisito: FFmpeg

O Transcritorio usa o **FFmpeg** para ler arquivos de audio e video.
Instale uma vez pelo Terminal:

```sh
brew install ffmpeg
```

Se voce nao tem o Homebrew, instale primeiro:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## Desempenho no Mac

No Apple Silicon (M1/M2/M3/M4), o Transcritorio roda a transcricao em
**CPU**, nao no acelerador grafico (MPS). Isso acontece porque o motor
de transcricao que usamos (`faster-whisper`) ainda nao suporta o GPU
da Apple.

- **Audio de 1 hora** leva aproximadamente **3 a 5 horas** para
  transcrever, dependendo do modelo e da geracao do chip.
- **Recomendado** deixar rodando enquanto voce faz outra coisa
  (almoco, reuniao, etc.)
- Em computadores com placa NVIDIA (Windows), o mesmo audio leva ~10-15
  minutos. Mac nao tem essa opcao hoje.

No backlog ha um suporte experimental a `mlx-whisper` que traria
aceleracao real em Apple Silicon (3-5x mais rapido). Ainda nao publicado.

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
