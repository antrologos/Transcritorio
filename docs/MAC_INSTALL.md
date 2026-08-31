# Instalar o Transcritório no macOS

> Canal atual (v0.2+): pacote Python instalado via [uv](https://docs.astral.sh/uv/).
> Suporte em **beta** — o suporte principal é Windows 10/11. O `.dmg` foi
> descontinuado; ver [`LEGACY_STANDALONE.md`](LEGACY_STANDALONE.md).

## Instalação

1. No **Terminal**, com [Homebrew](https://brew.sh) instalado:

   ```sh
   brew install uv ffmpeg
   ```

2. Instale o Transcritório:

   ```sh
   uv tool install transcritorio
   ```

   Em **Apple Silicon** (M1/M2/M3/M4), prefira o extra `[mac]`, que habilita a
   transcrição com aceleração Metal (MLX) — um selo `Motor: MLX (Metal)`
   aparece no cabeçalho do projeto:

   ```sh
   uv tool install "transcritorio[mac]"
   ```

   > **Aviso honesto:** este canal ainda não teve teste de campo no macOS —
   > funcionou no CI e o desenho não depende de nada específico do Windows,
   > mas se algo falhar na sua máquina, [abra uma
   > issue](https://github.com/antrologos/Transcritorio/issues) com a saída
   > de `transcritorio-cli self-test`.

3. Abra com:

   ```sh
   transcritorio
   ```

**Sem Gatekeeper:** diferente do `.dmg` antigo, não há app para "autorizar" —
o programa roda no seu perfil de usuário, e era exatamente o bloqueio do
Gatekeeper (sem Apple Developer ID pago) que inviabilizava o canal anterior.

## Manutenção

- **Atualizar:** `uv tool upgrade transcritorio`
- **Reparar:** menu **Ajuda → Reparar instalação** (não afeta projetos nem modelos)
- **Desinstalar:** `uv tool uninstall transcritorio` — projetos e transcrições ficam intactos

## Notas

- O token da Hugging Face (apenas para separação de falantes) é guardado no
  Keychain do macOS.
- Primeira execução baixa os modelos (~5 GB só transcrição; ~7 GB com
  separação de falantes; +1,6 GB do modelo MLX na primeira transcrição em
  Apple Silicon); depois tudo roda offline.
- Detalhes da aceleração Metal: [`MLX_WHISPER_MACOS.md`](MLX_WHISPER_MACOS.md).

## Canal legado (descontinuado)

O `.dmg` (v0.1.x) não recebe mais atualizações. Histórico, downloads antigos e
o passo a passo do Gatekeeper: [`LEGACY_STANDALONE.md`](LEGACY_STANDALONE.md).
