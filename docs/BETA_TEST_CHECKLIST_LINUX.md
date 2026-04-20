# Checklist de teste em Linux (via VM VirtualBox)

Backlog 0.3+ Item 4. Para executar apos o Item 3 (AppImage) publicar um
artefato de release.

## Pre-requisitos (uma vez)

1. Instalar [VirtualBox](https://www.virtualbox.org/wiki/Downloads) no
   Windows. Gratis, opcao legal.
2. Baixar ISO:
   - **Ubuntu 22.04 LTS Desktop** (principal): https://releases.ubuntu.com/22.04/
   - **Ubuntu 24.04 LTS Desktop** (compat glibc recente): https://releases.ubuntu.com/24.04/
   - **Fedora 40 Workstation** (opcional, RPM world): https://fedoraproject.org/
3. Criar VM:
   - RAM: 4 GB minimo, 8 GB recomendado
   - Disco: 30 GB dinamico
   - CPU: 2+ cores
   - Video memoria: 128 MB (habilitar aceleracao 3D se a VM travar na UI)
4. Instalar o sistema (seguir o wizard padrao)
5. Instalar Guest Additions para ter full-screen e shared clipboard

## Obtencao do AppImage

Opcao A (preferida): baixar do GitHub Release
```bash
# Dentro da VM
wget https://github.com/antrologos/Transcritorio/releases/download/v0.3.0-rc1/Transcritorio-x86_64.AppImage
chmod +x Transcritorio-x86_64.AppImage
```

Opcao B (dev): baixar do workflow artifact
- Abrir https://github.com/antrologos/Transcritorio/actions/workflows/release.yml
- Click no run mais recente
- Baixar `Transcritorio-linux-AppImage.zip` → extrair o `.AppImage`
- Copiar para a VM via shared folder do VirtualBox ou `scp`

## Checklist de smoke

### Sistema

- [ ] `./Transcritorio-x86_64.AppImage --help` nao explode (imprime help ou abre GUI)
- [ ] Double-click no Files (Nautilus) executa o AppImage
- [ ] Icone e nome aparecem corretamente no menu de aplicativos apos
  integrar via `AppImageLauncher` (opcional)
- [ ] `ffmpeg -version` imprime algo (FFmpeg esta no PATH da VM)

### Primeira execucao

- [ ] GUI abre sem erro no terminal
- [ ] Tema escuro esta forcado (Window cinza escuro, nao branco)
- [ ] Menu superior mostra exatamente 4 opcoes:
      `Arquivo`, `Editar`, `Transcrever`, `Ajuda`
- [ ] `Arquivo > Novo projeto...` abre o dialogo de criacao

### Projeto com audio real

1. Criar um projeto novo em `~/Documents/TesteTranscritorio/`.
2. Adicionar 1 arquivo de audio curto (ex: 3 minutos em `.wav` ou `.m4a`).
3. Iniciar transcricao.

- [ ] `Transcrever selecionados` funciona sem erro
- [ ] Aparece dialog "Detectou placa grafica / nao detectou" adequado
  (na VM geralmente nao ha GPU; deve rodar em CPU sem perguntar)
- [ ] Modelos sao baixados no primeiro uso se nao ha cache
- [ ] Progress bar avanca durante a transcricao
- [ ] Resultado final aparece na lista com status "Transcrito"
- [ ] `Ctrl+E` (exportar) produz um DOCX em `Resultados/`

### Token HF + keyring

- [ ] Ao primeiro download de modelo, aparece dialogo pedindo token HF
- [ ] Apos inserir, fechar e reabrir o app: o token persiste
  (confirma que `keyring` com SecretService funciona no desktop Linux)
- [ ] Verificar em linha de comando:
  ```bash
  python3 -c "import keyring; print(keyring.get_password('Transcritorio', 'huggingface'))"
  ```
  Deve imprimir o token sem erros.

### Fallback Fernet (headless)

SSH sem `$DISPLAY` e com dbus indisponivel forca o fallback Fernet.

- [ ] Em uma sessao SSH para a VM:
  ```bash
  unset DISPLAY
  Transcritorio-x86_64.AppImage transcritorio-cli models verify
  ```
  Executa sem erro (o token e lido via Fernet em `~/.local/share/Transcritorio/hf_token.fallback`).
- [ ] Arquivo `hf_token.fallback` tem permissao `0600`:
  ```bash
  ls -la ~/.local/share/Transcritorio/hf_token.fallback
  # esperado: -rw------- 1 user user ...
  ```

### Edicao de turnos

- [ ] Abrir transcricao (`Enter` na tabela) — editor abre
- [ ] Editar um turno, esperar 2s — indicador no canto mostra "Salvo"
- [ ] `Ctrl+Z` desfaz no editor de turnos
- [ ] `Esc` fecha o arquivo aberto

### Encerramento limpo

- [ ] Fechar o app pelo X da janela — nao trava
- [ ] Fechar o app pelo `Arquivo > Sair` — nao trava
- [ ] `ps aux | grep Transcritorio` apos fechar: nao ha processos orfaos

## O que NAO e esperado testar aqui

- GPU / aceleracao NVIDIA: VM nao tem passthrough na configuracao basica
- MPS: irrelevante no Linux
- `.deb` / `.rpm`: fora do escopo — AppImage e o formato da 0.3
- Instalacao system-wide em `/opt` ou `/usr/local`: AppImage roda
  a partir de qualquer pasta

## Reportar problemas

Abrir issue em https://github.com/antrologos/Transcritorio/issues com
label `platform-linux` contendo:
- Distro + versao (`lsb_release -a`)
- Desktop environment (`echo $XDG_CURRENT_DESKTOP`)
- Python da VM (`python3 --version`)
- Traceback ou screenshot do erro
- Etapa do checklist onde travou

## Notas de implementacao do checklist

- Escrito em 2026-04-20 apos push do release workflow (commit 7ac8233)
- AppImage ainda nao testado em hardware real — este checklist sera
  refinado apos primeira rodada em VM Ubuntu 22.04
- Fedora e opcional ate que haja bug reportado especifico (evita
  triplicar esforco de setup)
