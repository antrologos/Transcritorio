# Instalar o Transcritório no Linux

> Canal atual (v0.2+): pacote Python instalado via [uv](https://docs.astral.sh/uv/).
> Suporte em **beta** — o suporte principal é Windows 10/11. O AppImage foi
> descontinuado; ver [`LEGACY_STANDALONE.md`](LEGACY_STANDALONE.md).

## Instalação (Ubuntu/Debian e similares)

1. Instale as ferramentas de base:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   sudo apt install ffmpeg
   ```

   (Em Fedora: `sudo dnf install ffmpeg`; em Arch: `sudo pacman -S ffmpeg`.
   O `uv` também está disponível em vários gerenciadores — ver
   [instruções oficiais](https://docs.astral.sh/uv/getting-started/installation/).)

2. Feche e abra o terminal de novo (para o `uv` entrar no PATH) e instale:

   ```sh
   uv tool install transcritorio
   ```

   > **Período beta (até a v0.2.0 sair no PyPI):** o comando acima ainda não
   > está ativo. Use o comando do release
   > [beta-0.2.0b1](https://github.com/antrologos/Transcritorio/releases/tag/beta-0.2.0b1),
   > que é idêntico trocando `transcritorio` pela URL do wheel.

3. Abra com:

   ```sh
   transcritorio
   ```

## Aceleração NVIDIA (opcional)

Com placa NVIDIA e driver instalado, o mesmo comando do Windows habilita a
aceleração CUDA (download de ~2,5 GB):

```sh
uv tool install --reinstall "transcritorio[cuda]" \
  --with torch==2.8.0+cu128 --with torchaudio==2.8.0+cu128 --with torchvision==0.23.0+cu128 \
  --index https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match
```

(No beta, troque `"transcritorio[cuda]"` por `"transcritorio[cuda] @ <URL do wheel>"`.)

## Manutenção

- **Atualizar:** `uv tool upgrade transcritorio`
- **Reparar:** menu **Ajuda → Reparar instalação** (não afeta projetos nem modelos)
- **Desinstalar:** `uv tool uninstall transcritorio` — projetos e transcrições ficam intactos

## Notas

- O token da Hugging Face (apenas para separação de falantes) é guardado no
  Secret Service do desktop (GNOME Keyring/KWallet); em servidores sem
  desktop, um fallback criptografado local é usado automaticamente.
- Primeira execução baixa os modelos (~5 GB só transcrição; ~7 GB com
  separação de falantes); depois tudo roda offline.
- Wayland/X11: a interface usa Qt 6 (PySide6); em caso de problema de
  renderização, tente `QT_QPA_PLATFORM=xcb transcritorio`.

## Canal legado (descontinuado)

O AppImage (v0.1.x) não recebe mais atualizações. Histórico e downloads
antigos: [`LEGACY_STANDALONE.md`](LEGACY_STANDALONE.md).
