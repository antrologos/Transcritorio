# Roadmap do app standalone de transcricao

Este documento registra o plano do software standalone para usuarios nao tecnicos. O objetivo e manter o processamento local, auditavel e seguro, reaproveitando o motor atual em `transcribe_pipeline/`.

## Decisao atual

- Caminho recomendado para o produto final: app desktop Windows em PySide6/Qt sobre um motor Python.
- Implementacao inicial feita antes: camada de servico Python + prototipo operacional em Tkinter.
- Launcher do prototipo operacional: `scripts\transcription_gui.cmd`.
- Implementacao atual do editor: `transcribe_pipeline/review_studio_qt.py`, com launcher `scripts\review_studio.cmd`.
- O prototipo Tkinter existe apenas como painel operacional temporario. O caminho recomendado para usuarios finais e o Estudio de Revisao em PySide6/Qt.
- O app final deve continuar local/offline por padrao e nao deve enviar audios, imagens, TCLEs ou transcricoes para cloud sem autorizacao explicita.

## Arquitetura alvo

```text
UI desktop
  PySide6/Qt
  player audio/video
  editor sincronizado

Motor local
  transcribe_pipeline/app_service.py
  transcribe_pipeline/status.py
  transcribe_pipeline/review_store.py
  WhisperX / faster-whisper
  pyannote community-1
  FFmpeg

Armazenamento de projeto
  Transcricoes/00_manifest
  Transcricoes/02_asr_raw
  Transcricoes/03_diarization
  Transcricoes/04_canonical
  Transcricoes/05_transcripts_review/edits
  Transcricoes/06_qc
```

## MVP recomendado

- Criar/abrir projeto.
- Importar gravações por arquivo ou pasta.
- Atualizar manifesto automaticamente.
- Mostrar lista de entrevistas com status: audio, WAV, ASR, diarizacao, canonico, revisao, exportacao e QC.
- Rodar fila de processamento com botoes simples.
- Player de audio/video com velocidade de reproducao e salto por trecho.
- Transcricao sincronizada com o player: clicar no texto vai para o audio; tocar o audio destaca o trecho atual.
- Corrigir texto por turno ou segmento.
- Corrigir falante com botoes `Entrevistador` / `Entrevistado`.
- Juntar e dividir turnos.
- Marcar `[inaudivel]`, `[duvida]`, `[sobreposicao]`.
- `Salvar transcricao` explicito para a transcricao editavel e `Exportar...` para `DOCX`, `MD`, `SRT`, `VTT`, `CSV/TSV`, `NVivo`.
- Mostrar QC basico: cobertura, gaps, falantes, segmentos longos, trechos sem falante e warnings de qualidade.
- Autosave da revisao humana em JSON separado do ASR bruto.

## Funcionalidades Futuras Boas

- Waveform com regioes de fala e marcadores de trechos incertos.
- Atalhos de teclado para revisao rapida: play/pause, voltar 5 s, avancar 5 s, repetir trecho, trocar falante, marcar duvida.
- Busca dentro de uma entrevista e busca global em todas as entrevistas do projeto.
- Tags tematicas por trecho, com cores e exportacao para analise qualitativa.
- Comentarios por trecho.
- Historico de revisoes e comparacao entre versoes.
- Comparacao lado a lado entre variantes ASR, por exemplo `int8`, `float16`, `large-v3-turbo`.
- Relatorio de QC exportavel.
- Filtro de trechos com alerta: baixa confianca, gap grande, falante desconhecido, repeticao suspeita, texto curto demais.
- Importacao de roteiro e glossario como referencia de revisao, nao como prompt automatico.
- Realce de termos do glossario e lista de possiveis inconsistencias.
- Painel de hardware: GPU, VRAM livre, recomendacao `float16` vs `int8`, aviso para fechar LM Studio/outros apps de GPU.
- Gerenciamento de cache de modelos.
- Modo lote e modo entrevista unica.
- Modo apresentacao para tocar audio/video com legenda sincronizada.
- Exportacao de pacote de auditoria com config, modelo, datas, logs e hashes.
- Instalador Windows com FFmpeg embutido, cache de modelos em `%LOCALAPPDATA%`, token Hugging Face protegido por DPAPI e sem exigir Python/VS Code/terminal do usuario final.

## Fora do MVP

- Colaboracao em tempo real.
- Sincronizacao em nuvem.
- Recursos de LLM por padrao.
- Uso automatico de roteiros/glossarios como prompt de ASR.
- Multiplos modelos editaveis por usuario leigo.
- Instalador totalmente autocontido com todos os modelos embutidos no primeiro pacote.

## Proximos passos tecnicos

1. Instalar/validar `PySide6` no venv de transcricao.
2. Abrir `scripts\review_studio.cmd` e testar o piloto `A01P_0608` com player, clique no turno, destaque de reproducao, edicao e exportacao.
3. Melhorar ainda mais o progresso do pipeline com eventos estruturados internos; estado atual: o Estudio ja interpreta percentuais emitidos pelo WhisperX e usa progresso por etapa como fallback.
4. Melhorar cancelamento em etapas sem subprocesso direto, como diarizacao via API.
5. Adicionar atalhos de teclado.
6. Testar empacotamento com `pyside6-deploy`/Nuitka e instalador simples.

## Implementado no Estudio de Revisao

- Lista de entrevistas com estados em linguagem simples.
- A lista passou a ser tratada como `Arquivos do projeto`, com selecao multipla e colunas de metadados: arquivo, transcricao, duracao, lingua, falantes, rotulos, contexto e avisos.
- Barra principal com acoes de usuario final: `Adicionar midia...`, `Transcrever`, `Salvar transcricao` e `Exportar...`. `Editar propriedades...` fica junto da lista de arquivos para deixar claro que pode atuar sobre um ou varios selecionados.
- Base do modelo `Projeto de Transcricoes`: `projeto.transcricao.json` na raiz do projeto, `metadados.csv` com uma linha por arquivo selecionado e `Transcricoes/00_project/jobs.json` com estado/progresso por arquivo.
- Comandos `Novo projeto...` e `Abrir projeto...` no menu `Projeto`. Projetos novos sao criados em pasta `*.transcricao`; `project_root: .` agora e resolvido relativo ao `run_config.yaml` do proprio projeto.
- `Editar propriedades...` permite definir lingua, modo de falantes, rotulos e contexto opcional para varios arquivos selecionados. O contexto fica vazio por padrao e so e usado como prompt quando explicitamente marcado.
- Menu `Ferramentas` para fila de processamento, configuracao do motor (`GPU/CPU`, modelo, precisao, batch), operacoes tecnicas e reprocessamento pontual.
- Barra de progresso ponderada por etapa para tarefas longas, progresso real do WhisperX quando a CLI emite percentuais, fila persistente por arquivo e cancelamento que tenta interromper o WhisperX; outras etapas param no proximo ponto seguro.
- Player Qt de audio/video com play/pause, barra de tempo e velocidade de reproducao.
- Tabela de turnos sincronizada: mostra texto completo; clicar no texto seleciona para edicao sem mover o audio; clicar no tempo ou dar duplo clique na linha leva ao ponto da midia; a reproducao destaca o turno atual.
- Edicao por bloco em JSON separado, preservando ASR bruto e canonico.
- Troca de falante `Entrevistador` / `Entrevistado`.
- Marcacoes estruturadas: `inaudivel`, `duvida`, `sobreposicao`.
- Juntar turno com o proximo e dividir turno pelo cursor de edicao na onda sonora, pelo tempo atual do audio quando aplicavel, ou pelo cursor do texto como estimativa.
- `Exportar...` para gerar copias da transcricao em `MD`, `DOCX`, `SRT`, `VTT`, `CSV`, `TSV` e `NVivo`.
- Correcao pos-verificacao: `MD`/`DOCX` agrupam por falante editorial editado; merge entre falantes diferentes e bloqueado; o player mostra erro e tenta WAV de fallback; o app bloqueia fechamento durante job longo; a verificacao de arquivos gerados confirma antes de rodar todas as entrevistas.
- Ajustes posteriores: tempos de inicio/fim editaveis por bloco com botoes de ajuste junto aos campos, botoes inferiores restritos a edicao do bloco, destaque legivel, botoes de player com icones padrao, painel de video oculto para audios puros, waveform de maior resolucao baseada no WAV preparado com pan por arraste, zoom ancorado no cursor, regua de tempo, cursor de edicao, destaque do bloco selecionado/atual, paineis verticais ajustaveis na area de revisao e importacao de pastas adicionais com varredura recursiva e deduplicacao por ID/nome.
- Revisao UI/UX de 2026-04-13: menus centralizados em `QAction`, `Salvar transcricao` separado de `Exportar...`, `Salvar bloco` nomeado explicitamente no editor inferior, estados persistentes de salvamento, `Desfazer`/`Refazer` para edicoes estruturais, barra de progresso oculta quando ociosa, status de progresso sem trechos de transcricao/log bruto, cancelamento do WhisperX, abertura de midia mesmo sem transcricao, e termos internos como `QC`, `manifesto`, `fundir` e `Salvar como...` removidos da superficie principal.
- Implementado em 2026-04-13: tela inicial de projeto, acao `Fila de processamento`, atualizacao persistente de `jobs.json`, metadados ampliados de audio/video/formato e rotulos de falantes usados ponta a ponta no render/exportacao.
- Proximo passo pendente: implementar copia opcional de midias para dentro do projeto, projetos recentes, cancelamento mais profundo da diarizacao via API e eventos estruturados internos alem do parsing textual do WhisperX.
