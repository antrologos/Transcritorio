# Canal standalone (LEGADO — descontinuado em 2026-08)

Da v0.1.0 à v0.1.8 o Transcritório foi distribuído como aplicativo
standalone: instalador Windows (Inno Setup), `.dmg` para macOS e AppImage
para Linux, todos congelados com PyInstaller.

**Esse canal foi descontinuado.** Motivos:

- Sem assinatura digital (code signing) — inviável para o projeto —,
  antivírus e SmartScreen bloqueavam ou assustavam a maioria dos
  usuários; houve inúmeros relatos de instalação impossível ou quebrada.
- O SignPath Foundation recusou a assinatura gratuita.
- No macOS, sem Developer ID/notarização, o Gatekeeper sempre desconfia.
- Bundles de 0,6–1,6 GB com centenas de DLLs disparavam heurísticas de
  antivírus e corrompiam com facilidade.

O canal atual instala via `uv` + PyPI — apenas componentes assinados
pelos distribuidores oficiais. Ver [`INSTALL_WINDOWS.md`](INSTALL_WINDOWS.md)
e o [README](../README.md#instalação).

## Releases antigas

As releases v0.1.0–v0.1.8 permanecem publicadas no GitHub por
transparência e reprodutibilidade, marcadas como **LEGADO — sem
suporte**: https://github.com/antrologos/Transcritorio/releases

Avisos que valiam para o canal antigo (SmartScreen "Executar assim
mesmo", exceções de antivírus, barra em 99% na extração lzma2, cuda_pack
baixado pelo Setup.exe) estão nos README das próprias releases.

## Infraestrutura preservada no repositório

`packaging/` (spec do PyInstaller, Inno Setup, bundle filters) e o
workflow `release.yml` (agora só via execução manual) foram mantidos no
repositório caso o canal precise ser ressuscitado um dia — por exemplo,
se o projeto obtiver assinatura digital. O checklist correspondente está
em [`PACKAGING_CHECKLIST.md`](PACKAGING_CHECKLIST.md).
