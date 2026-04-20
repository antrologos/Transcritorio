# Como instalar o Transcritorio no Linux

Guia rapido para pesquisadores em Ubuntu, Fedora ou outras distribuicoes
com base no AppImage. Testado em Ubuntu 22.04 e 24.04.

## O que baixar

1. Va ate a pagina de Releases: https://github.com/antrologos/Transcritorio/releases/latest
2. Baixe o arquivo **`Transcritorio-x86_64.AppImage`**
3. Tamanho esperado: ~1.5 GB (inclui tudo que o programa precisa)

## Instalar pre-requisitos do sistema

O AppImage **nao inclui o FFmpeg nem as libs graficas do Qt** —
instale uma vez via gerenciador de pacotes.

### Ubuntu/Debian

```sh
sudo apt update
sudo apt install -y ffmpeg \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-sync1 \
    libxcb-util1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
    libxkbcommon-x11-0 libxkbcommon0 libegl1 \
    libfuse2 libglib2.0-0 libasound2
```

(Lista completa — Qt 6.5+ exige varias libs xcb para o plugin da
plataforma X11 carregar corretamente. Se esquecer alguma, o app mostra
"Could not load the Qt platform plugin 'xcb'" no terminal.)

### Fedora

```sh
sudo dnf install -y ffmpeg libxcb libxkbcommon
```

(Para FFmpeg no Fedora pode ser necessario habilitar o RPM Fusion;
veja https://rpmfusion.org/Configuration)

## Executar

No terminal, no diretorio onde baixou o AppImage:

```sh
chmod +x Transcritorio-x86_64.AppImage
./Transcritorio-x86_64.AppImage
```

Ou clique duas vezes no arquivo pelo gerenciador de arquivos (precisa
que o arquivo tenha permissao de execucao — alguns gerenciadores
fazem isso automaticamente, outros pedem pra marcar).

## Integrar com o menu de aplicativos (opcional)

Para aparecer no menu "Aplicativos" do seu desktop ao lado de outros
programas instalados:

```sh
# Instala o AppImageLauncher (Ubuntu)
sudo apt install appimagelauncher

# Primeira vez que voce rodar o AppImage ele pergunta se quer integrar
# Resposta: "Integrate and run"
```

## Desempenho no Linux

**Em maquina com NVIDIA + drivers CUDA 12.x instalados**: a
transcricao acelera 3-9x. O Transcritorio detecta a placa
automaticamente.

**Em maquina sem NVIDIA** (Intel/AMD integrado): roda em CPU. Audio de
1 hora leva ~3-5 horas.

Verificar se o CUDA esta disponivel:

```sh
nvidia-smi    # se imprime tabela: OK, CUDA disponivel
```

## Desinstalar

Simples — apague o arquivo `.AppImage`. Opcionalmente, apague dados
do app:

```sh
rm -rf ~/.local/share/Transcritorio
```

Seus projetos de transcricao (`.transcritorio`) ficam onde voce os
salvou e nao sao apagados.

## Token HF (Hugging Face)

O Transcritorio pede um token gratis do Hugging Face uma vez para
baixar os modelos de transcricao. O token e armazenado:

- **Com desktop grafico** (GNOME, KDE, etc.): via `keyring`, usando
  o SecretService/KWallet do sistema. Seguro, sincronizado com o
  gerenciador de credenciais do desktop.
- **Headless / SSH**: via Fernet criptografado em
  `~/.local/share/Transcritorio/hf_token.fallback` com permissao 0600.

## Problemas comuns

### "Error: libxcb-cursor.so.0: cannot open shared object file"

Faltou instalar as libs xcb. Rode o comando de pre-requisitos de novo.

### "ffmpeg: not found"

Rode `sudo apt install ffmpeg` (ou `dnf`). Confirme com `ffmpeg -version`.

### "Permission denied" ao executar o AppImage

```sh
chmod +x Transcritorio-x86_64.AppImage
```

### FUSE error no AppImage

Alguns sistemas minimais nao tem FUSE habilitado:

```sh
sudo apt install libfuse2
```

### Integracao com desktop nao funciona

`AppImageLauncher` e opcional. Sem ele, o AppImage roda mas nao
aparece no menu de aplicativos automaticamente. Voce pode criar um
`.desktop` file manualmente em `~/.local/share/applications/`.

## Rodar pela linha de comando

O AppImage inclui o CLI. Para usar:

```sh
./Transcritorio-x86_64.AppImage --help                      # abre GUI
# TODO: documentar como invocar a CLI do subprocess
```

## Reportar bugs

Via GitHub Issues: https://github.com/antrologos/Transcritorio/issues

Inclua:
- Distro + versao (`lsb_release -a` ou `/etc/os-release`)
- Desktop environment (`echo $XDG_CURRENT_DESKTOP`)
- Traceback ou erro do terminal
- Label `platform-linux`
