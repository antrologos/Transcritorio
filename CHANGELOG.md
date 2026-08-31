# Changelog

## Não lançado (v0.2 em preparação)

**Mudança de canal de distribuição**: o standalone (Setup.exe/.dmg/AppImage)
foi descontinuado — antivírus/SmartScreen bloqueavam a instalação sem
assinatura digital, e o SignPath recusou a assinatura gratuita. O canal
oficial passa a ser **PyPI + uv** (`uv tool install transcritorio`); ver
`docs/INSTALL_WINDOWS.md` e `docs/LEGACY_STANDALONE.md`.

- **Reforma de interface — fundação (R0)**: primeira etapa do
  redesenho aprovado. A janela adota a paleta nova (fundo mais
  profundo, cores semânticas centralizadas — nenhuma cor solta no
  código, verificado por teste); ~140 textos ganharam acentuação
  correta e reticências tipográficas ("Abrir transcrição", "Exportar…",
  "Verificar exportações"); o splash segue a identidade nova. Três
  guardas automáticas passam a proteger a interface: nenhuma ação pode
  ficar sem lugar (menu/botão/atalho), atalhos não podem colidir, e
  textos novos precisam seguir o guia de escrita. Nada de comportamento
  muda nesta etapa — as barras e menus novos vêm na próxima (R1).
- **Separação de falantes automática quando instalada**: a opção
  "Separar falantes" ganhou o modo automático (novo padrão) — separa
  quem fala sempre que o recurso estiver instalado no computador, no
  momento da transcrição. Antes, um projeto criado pelo perfil
  Essencial gravava "sem falantes" para sempre, mesmo que o modelo
  fosse instalado depois — e a transcrição saía sem falantes em
  silêncio. Agora: instalou, aplica; desligado só se você desmarcar a
  caixa de propósito; e quando o recurso falta, o app avisa com todas
  as letras antes de transcrever (e ensina o caminho do Reprocessar
  falantes para separar depois sem transcrever de novo).
- **Idioma como capacidade** (etapa 4 do programa multilíngue): 16
  idiomas ganham pacote de alinhamento dedicado (tempos por palavra) com
  download avisado ANTES de transcrever — antes, inglês/espanhol/francês
  baixavam ~360 MB sem avisar no meio do job e ~59 idiomas estouravam
  erro DEPOIS de transcrever a entrevista inteira. O combo de idioma do
  Motor e das propriedades do arquivo agora é honesto ("baixa ~1,2 GB" /
  "Automático — sem tempos por palavra"); o gate considera os idiomas de
  TODOS os arquivos do lote (metadado por arquivo); o gerenciador lista
  os pacotes de idioma; o assistente de instalação pergunta os idiomas
  das gravações. Um pacote multilíngue coringa (MMS, ~1,2 GB, uso
  não-comercial) cobre mais de 1.100 idiomas sem pacote dedicado —
  suaíli incluso.
- **Motor experimental Parakeet pt-BR (TAGARELA)**: novo motor de
  transcrição só para português (2,5 GB, baixável pelo gerenciador),
  com pontuação, capitalização e tempos por palavra nativos (dispensa o
  alinhador). Fala espontânea é o forte do modelo (treinado em corpora
  de entrevista). Roda inteiramente na CPU a ~13× tempo real. Marcado
  experimental até a comparação lado a lado com o Whisper large-v3 nos
  áudios de gabarito; só transcreve português (outros idiomas no lote
  são bloqueados com aviso antes do job).
- **Aceleração do Parakeet na GPU (opcional)**: com placa NVIDIA, o
  motor Parakeet pode usar a GPU e ficar ~4x mais rápido (uma hora de
  gravação em ~1 minuto; medido: 62x tempo real com ~4,7 GB de memória
  de vídeo). É um pacote opcional de ~300 MB — oferecido uma vez na
  primeira transcrição com o motor e sempre disponível em Gerenciar
  modelos (instalar/remover). O seletor de Dispositivo do Motor passa a
  valer também para o Parakeet (CPU força o processador). Se a GPU
  falhar por qualquer motivo, a transcrição continua no processador
  sozinha, com aviso — o trabalho nunca é perdido.
- **Checagem geral pós-lote** (três varreduras exaustivas; ~50 achados,
  criticos corrigidos): "Perguntar" agora oferece o modelo de análise
  ANTES do preparo do encoder (ordem de gates invertida escondia a
  oferta) e nenhum clique fica mudo (pergunta vazia, Enter em botão
  desabilitado); o gate de modelos propaga o escopo ao preparador (fim
  do beco "não há nada para baixar" do Melhorar falantes) e a ação
  original é RETOMADA sozinha quando o download termina; VRAM abaixo do
  mínimo virou aviso "por sua conta e risco" (não veto — placa de 4 GB
  com o modelo baixado volta a funcionar); gerenciador baixa também o
  alinhador e a separação de falantes pendentes e mostra downloads
  parciais como "Incompleto" com retomada; cancelar um job não marca
  mais "Falha" nem abre diálogos de resultado; a causa real de uma
  falha aparece no aviso e a mensagem final não é mais apagada pelo
  refresh; "Atualizar transcrição editável" e "Reprocessar falantes"
  passam a atualizar o que o usuário vê; ações destrutivas habilitam
  pela mesma régua que executam (e o botão direito age na linha
  clicada); disco removido/config sumida/arquivo travado não quebram
  mais em silêncio; editor congelado durante retranscrição do arquivo
  aberto (fim da corrida que perdia texto digitado); drop zone lista só
  os formatos realmente aceitos; "Mostrar no Explorer" seleciona o
  arquivo de verdade.
- **Coerência de funcionalidades** (lote pós-teste real do primeiro uso):
  - Todos os gates de clique de IA passam pelo registro de capacidades:
    a memória de vídeo mínima entra na decisão (uma GPU de 2 GB não
    recebe mais oferta de 8,7 GB), e toda oferta de download declara
    tamanho, requisito de hardware com a placa detectada, espaço em
    disco e o ambiente de análise (~3 GB) preparado na primeira
    utilização. O "Perguntar" da janela de AI ganhou o mesmo baixador
    das demais ações (antes o erro virava texto morto no rodapé).
  - Botões da barra passam a seguir o estado das ações (o botão
    "Perguntar às entrevistas com AI" contornava os bloqueios).
  - **Transcrever novamente...**: botão contextual e item de menu para
    refazer a transcrição do arquivo aberto, escolhendo entre os
    modelos instalados. Retranscrever agora TEM efeito: a transcrição
    editável é recriada do novo resultado (com confirmação explícita e
    cópia de segurança das edições em `edits/backups/`); o painel
    mostra o modelo que produziu a transcrição aberta.
  - "Limpar transcricao gerada...": preserva as cópias de segurança,
    faz backup prévio de revisões editadas, remove o índice de busca
    órfão e mira apenas a seleção visual (caixas de marcação escolhem o
    que transcrever, nunca o que apagar).
  - Lote com skip-and-continue: falha num arquivo não aborta mais os
    seguintes; o estado "Falha" aparece na coluna Transcrição (tooltip
    com a causa) e o filtro "Pendentes" o inclui.
  - Gerenciar modelos lista também os modelos de IA (Qwen, nomes,
    busca) com estado por máquina, baixar por item e remover; variantes
    Whisper não instaladas também ganham "Baixar". "Instalar
    modelos..." do diálogo do Motor agora abre o gerenciador; o
    preparador de modelos com tudo em cache diz "não há nada para
    baixar" e desabilita o botão.
  - Assistente: o perfil Completo pergunta se baixa os modelos de IA
    agora (~10 GB) ou na primeira utilização, com o recomendado
    assinalado pela máquina.
  - Marcar "Separar falantes" sem o modelo presente oferece preparar na
    hora, explicando a conta gratuita do Hugging Face (a exigência não
    fica mais para a hora de transcrever); remontagens decidem a fonte
    de falantes por arquivo (perfil Essencial não falha mais).
  - O selo "Motor: CUDA/CPU" do cabeçalho virou link para a
    configuração (alternância CUDA↔CPU descobrível); sem placa NVIDIA
    o item CUDA fica desabilitado com o motivo.
  - Diálogos de aceleração NVIDIA citam a memória de vídeo detectada e
    avisam quando ela é pequena demais para valer a pena.

- Verificação acústica de trocas de falante (novo passo pós-render,
  `check-boundaries`): compara a voz dos dois lados de cada troca com o
  modelo de embedding da diarização (benchmark: AUC 0,961) e marca com
  "Dúvida" os blocos em que a voz é a mesma dos dois lados (divisão
  provavelmente errada); marca com "Sobreposição" blocos majoritariamente
  cobertos por falas simultâneas. Banner "N trocas de falante com vozes
  parecidas" e tooltip explicativo na coluna Marcações do Estúdio.
  Configurável (`boundary_check`, limiar e janela em `run_config.yaml`).
- Captura de sinais da diarização (`diar_signals`, hook do pyannote): a
  diarização agora persiste derivados compactos em
  `03_diarization/signals/` — silêncios e sobreposições segundo o próprio
  modelo, e a margem de confiança da voz atribuída a cada trecho. A
  verificação pós-render usa esses sinais para marcar blocos de voz
  incerta (margem negativa = o modelo prefere outra voz) e melhorar a
  detecção de sobreposição; arquivos antigos caem no fallback por
  divergência regular×exclusive. Desligável
  (`diarization_capture_signals`); falha na captura nunca interrompe a
  diarização.
- Segurança: "Enviar para Lixeira" (Del) passa a mirar SOMENTE os
  arquivos selecionados na lista (destaque) — as caixas de marcação, que
  escolhem o que transcrever e vêm marcadas por padrão, não contam mais
  como alvo de deleção (um Del com tudo marcado mandava o projeto inteiro
  para a lixeira). Os diálogos de confirmação e de purga agora LISTAM os
  nomes dos arquivos afetados, não só a contagem.
- O fim de uma transcrição agora renova a mídia do player: quem abria o
  arquivo antes de transcrever ficava preso ao áudio original (MP3 com
  seek impreciso) no player e no diálogo de vozes — causa da
  dessincronia crescente relatada em uso real.
- Correção GRAVE de sincronia áudio×texto no Estúdio: o player abria o
  arquivo ORIGINAL (MP3/M4A, seek impreciso em VBR com desvio que cresce
  ao longo do arquivo) enquanto waveform e timestamps referem-se ao WAV
  preparado. Agora o player abre o WAV por padrão (vídeos continuam no
  original, pelo painel de imagem) e todos os seeks ganharam a
  confirmação anti-descarte do Windows (mesmo fix do diálogo de vozes).
  Probe: desvio ≤ 29 ms em posições até 38 min.
- Banner de trocas de falante integrado: navegação ‹ › pelos blocos
  marcados (no lugar de "Ver primeira"), contagem que atualiza ao
  desmarcar Dúvida (e o banner some ao zerar), e a explicação da
  marcação agora aparece no painel do editor — não só como tooltip.
- **✨ Perguntar às entrevistas com AI** (fase 2.7): faça uma pergunta e
  receba uma resposta composta APENAS a partir dos trechos das
  transcrições, com citações [n] clicáveis que abrem cada trecho no
  áudio; sem base suficiente, a resposta é a recusa honesta ("isso não
  aparece nas entrevistas") — resposta sem citação válida é descartada
  por construção. Janela própria (botão na barra principal) que também
  encontra trechos por significado sem compor resposta. Requer GPU
  NVIDIA; AI 100% local. CLI: `transcritorio-cli ask --question "..."`.
- Identidade de AI: ações assistivas ganham ✨ e o selo "AI local — nada
  sai do seu computador"; resumo renomeado "✨ Resumir a entrevista com
  AI"; busca de palavras separada em janela própria e minimalista.
- Escopo visível e escolhível nas buscas e no Perguntar: as janelas
  ganham o seletor "Onde:" — todas as transcritas / somente a entrevista
  aberta / escolher quais (lista interna com caixas de marcação, própria
  da janela — sem relação com as marcações ☑ do painel, que significam
  "o que transcrever") — e uma linha que diz sempre quantos arquivos
  entram e que a leitura é das TRANSCRIÇÕES, não do áudio. Arquivos sem
  transcrição nunca somem em silêncio; projeto sem transcrição alguma
  explica em vez de não fazer nada; o seletor se atualiza ao voltar para
  a janela. Tooltips de buscar/perguntar/resumir declaram o escopo.
- Primeiro contato com projeto reformado (achados do primeiro teste real):
  o modelo escolhido no assistente agora vale de verdade — projetos novos
  nascem pedindo ele (antes, quem instalava o `tiny` criava projetos
  exigindo o turbo de fábrica e caía num pedido de download de 3,1 GB);
  "Preparar modelos locais" mostra só o que a SUA instalação precisa e só
  exige token quando há modelo de acesso restrito pendente; "Novo projeto"
  ganhou diálogo próprio com o modelo mental à vista e o preview exato da
  pasta que será criada; sem projeto aberto, a tela mostra "Comece criando
  um projeto" com os botões que resolvem (nada de tabela vazia nem de modal
  que manda ao menu); cada projeto novo traz um LEIA-ME na raiz (o que é
  seu, o que é gerado, o que dá para levar a outro computador) e outro
  dentro de Transcricoes/ explicando que as onze pastas técnicas não
  precisam ser abertas; abrir uma pasta qualquer não cria mais um projeto
  dentro dela em silêncio; e o descritor deixou de sair com o nome
  mutilado (`Meu Projeto_transcricao.transcritorio`).
- **✨ Revisar grafias de nomes**: a partir do glossário, mostra cada
  ocorrência de um nome escrito de forma diferente — com a entrevista, o
  tempo e o trecho à vista — e corrige só as que você marcar. A decisão
  é por ocorrência, nunca por palavra, porque a mesma grafia pode ser um
  nome legítimo em outro trecho (num teste real, um grupo apontava para
  "Méier" e não para a sigla sugerida: aplicar em massa teria escrito um
  erro por cima de outro). Clicar num trecho abre a entrevista naquele
  ponto para ouvir antes de decidir. O casamento é exato, sensível a
  maiúsculas e respeita fronteira de palavra ("Meia" não altera "meias").
  Só a transcrição revisada muda: a transcrição original da máquina fica
  intacta, cada arquivo alterado ganha cópia de segurança em
  `05_transcripts_review/edits/backups/` e Ctrl+Z desfaz na entrevista
  aberta.
- **✨ Glossário de nomes com AI**: lê as transcrições do projeto e monta
  um glossário de pessoas, lugares e instituições citados, juntando as
  variações de grafia do mesmo nome (IBGE escrito como "BGA", Viçosa como
  "Vistosa"). O glossário passa a acompanhar os prompts do resumo e do
  Perguntar, para a AI tratar as variantes como a mesma entidade — sem
  alterar uma vírgula das transcrições. Sai um relatório com a seção
  "Grafias a conferir", apontando onde cada variante ocorre. Declarar os
  nomes corretos na seção "## Nomes conhecidos" do contexto da pesquisa
  torna a detecção muito mais precisa (num teste com 54 menções reais,
  passou de 3 para 6 de 6 nomes corrompidos identificados). AI 100%
  local, roda sem placa de vídeo. CLI: `transcritorio-cli glossario`.
- Áudio multicanal (fase 4, núcleo): quando a gravação tem 2+ canais
  com microfones distintos (lapelas, gravador de 2 mics), o pipeline
  passa a usar os canais como fonte da separação de falantes, com os
  rótulos casados às vozes do pyannote por semelhança de voz — sinal
  muito mais confiável que qualquer pós-processamento. A verificação
  é barata: três amostras de 1 minuto decidem em menos de 1 segundo se
  os canais são mesmo microfones separados; gravações "falso-estéreo"
  (canais idênticos, o caso comum em gravador de celular) seguem o
  fluxo normal. O áudio por canal é intermediário e fica na pasta
  temporária do sistema, nunca dentro do projeto. Arquivos mono não
  mudam em nada. CLI: `transcritorio-cli channels`.
- Tempos por palavra no Estúdio (fase 3): o alinhamento do Whisper
  sempre produziu o tempo de cada palavra — agora o editor os usa.
  Duplo clique numa palavra do texto leva o áudio exatamente até ela;
  "Dividir bloco" corta no tempo exato da palavra sob o cursor (a nota
  do rodapé diz qual fonte foi usada); com zoom na onda sonora aparecem
  ticks no início de cada palavra, e os do decil inferior de confiança
  do alinhamento ficam âmbar (posição incerta). Tudo degrada em
  silêncio quando não há transcrição/palavras (ex.: arquivos só-mídia).
- Busca semântica mais limpa: o vetor de cada bloco passa a ser
  calculado com janela de contexto (fim do bloco anterior + bloco +
  início do seguinte), então falas curtas e interjeições só aparecem
  quando a vizinhança é tematicamente próxima da consulta — nada é
  excluído do índice; e blocos adjacentes do mesmo momento não se
  repetem nos resultados (fica o melhor). Índices antigos são
  reconstruídos automaticamente no próximo "Preparar".
- Busca nas transcrições (fase 2.3): **Ctrl+F** filtra os blocos do
  arquivo aberto em tempo real (barra estilo navegador, ‹ › navegam, Esc
  fecha); **Ctrl+Shift+F** (ou Editar → Buscar nas transcrições) abre a
  busca do projeto inteiro com duas seções — "Resultados exatos" e
  "Trechos com sentido parecido" (encoder multilíngue local pequeno, roda
  em CPU, sem GPU e sem conta) — e clique no resultado abre a entrevista
  no bloco certo com o áudio posicionado. Índice por arquivo em
  `07_index/`, atualizado sozinho quando a transcrição muda; preparo e
  download do modelo (~0,5 GB, uma vez) oferecidos no próprio diálogo.
  CLI: `transcritorio-cli search --query "..."`.
- Botão contextual "Gerar resumo com temas" na área do arquivo aberto
  (par com "Abrir resumo com temas": sem resumo → gerar; com resumo →
  abrir).
- Infra da análise local (fase 2.0 do plano-programa): módulo
  `research_context` (contexto de pesquisa por projeto — roteiro,
  codebook, nomes conhecidos — grounding das ferramentas de análise);
  módulo `llm_env` (ambiente dedicado criado sob demanda via uv para o
  Qwen3.5-4B + GLiNER — separado porque o transformers>=5.13 exigido pelo
  Qwen é irreconciliável com o pin de huggingface-hub do whisperx);
  categoria `_OPTIONAL_MODELS` no gerenciador de modelos (Qwen3.5-4B e
  GLiNER multi-PII com SHAs pinadas, fora dos obrigatórios e protegidos
  da limpeza de órfãos). Modelo escolhido por julgamento às cegas do
  usuário + benchmark com gabarito real (PoC 2026-08-25).
- Segurança de dados: "Melhorar falantes deste arquivo" (e o "Tentar
  novamente" do banner) agora guarda automaticamente uma cópia de
  segurança da revisão em `05_transcripts_review/edits/backups/` antes de
  recriá-la — antes, todas as edições manuais eram descartadas sem aviso
  fiel e sem backup. O diálogo de confirmação passou a dizer a verdade.
- Defaults seguros: `asr_device/compute_type/batch_size: auto` — CPU usa
  int8/batch 2 (float16 em CPU era convertido p/ float32 pelo CTranslate2,
  ~2x RAM: travava máquinas sem GPU); float16 explícito em CPU é coagido.
- Diarização opcional de ponta a ponta: quem só quer transcrever não cria
  conta HF nem token; escolha no wizard, persistida para projetos novos;
  falha de diarização não derruba mais o lote (render segue sem falantes).
- Diarização roda em subprocesso (`transcritorio-cli diarize
  --progress-json`): crash de pyannote/torch/CUDA não fecha mais a GUI.
- Aceleração NVIDIA como extra `[cuda]` (menu do app); reparo/atualização
  via uv no menu Ajuda; atalho de área de trabalho criado no 1º run;
  aviso discreto de versão nova via PyPI.
- Correções do diagnóstico 2026-08-23 (101 findings triplo-verificados):
  lixeira nunca mais apaga a única cópia de originais, guards de job
  ativos, globs por id sem colisão de prefixo, parser YAML preserva '#',
  token_vault nunca crasha, release não publica com build falho, e ~40
  outros fixes (ver mensagens dos commits de 2026-08-23).
- Qualidade da separação de falantes (4 correções, 2026-08-23/24): os
  metadados por arquivo ("Aplicar falantes", ex.: grupo focal com 6 vozes)
  passam a valer na rota CLI/GUI; o pós-processamento da camada exclusiva
  não atropela mais apartes curtos (padrão A-B-A); o corte palavra→falante
  usa critério acústico (palavra de borda segue o turno vizinho;
  interjeições reais são preservadas); hiperparâmetros de clustering
  aplicam-se por chave (`diarization_fa/fb` eram inertes).
- **"De quem é esta voz?"**: ao fim de cada transcrição (e via banner na
  revisão), o app toca amostras de cada voz — com timestamp e prévia do
  texto — e pede o nome, que vale para a transcrição inteira; confirmação
  registrada por arquivo; opção por projeto de não perguntar (lote).
- Reconhecimento local de vozes recorrentes: as vozes confirmadas em 2+
  arquivos viram âncoras do projeto (embeddings já calculados pela
  diarização, agora persistidos) e os arquivos seguintes abrem com
  "parece ser X" pré-preenchido — sempre confirmável, nunca automático.
- Grupo focal como caso de primeira classe: ao transcrever, o app pergunta
  "Quantas pessoas falam?" (presets entrevista/grupo focal/exato/auto, uma
  vez por lote); cores estáveis por voz na tabela de blocos; rótulos
  default "Moderador/Participante N".
- Partida com feedback e instância única: splash imediato ao abrir e o
  segundo clique no ícone traz a janela existente (nunca mais duas janelas);
  campo Falante editável com "Aplicar a todos desta voz".
- Player das amostras: toca o WAV preparado (timeline exata do pipeline) e
  contorna o descarte silencioso de seek do backend de mídia do Windows.
- Beta público do canal novo: release `beta-0.2.0b1` (wheel instalável por
  URL com `uv tool install`) para testes em máquinas reais antes da v0.2.0.

## 0.1.8 — 2026-04-27

UX baseada em feedback da Denise (1ª usuária externa, instalou v0.1.7 e
transcreveu sua primeira entrevista de 1h36 em ~10 min com aceleração CUDA).
Três pequenos atritos identificados, todos corrigidos:

### 1. Auto-retry no download dos modelos HF

`_manual_snapshot_download` em `transcribe_pipeline/model_manager.py` agora
faz retry automático per-blob com backoff exponencial (5 tentativas, esperas
0/2/4/8/16s). Em conexão flaky (caso reportado: erro a ~40% do download
recorrente), o sistema retenta sozinho até concluir; só levanta exception
para a UI se as 5 tentativas falharem. Antes, cada falha exigia clique
manual em "Voltar"+"Próximo" — Denise teve que repetir 4-5 vezes.

UI (`review_studio_qt.py`) reconhece o evento novo `model_download_retry`
e mostra na label do wizard: `"Reconectando em 4s (3/5) — falha: ConnectionError"`.

Toy test novo: `tests/toy_manual_snapshot_retry.py` (3 cenários:
3-falhas+sucesso, 5-falhas+desiste, caminho-feliz).

### 2. Label informativa durante extração lzma2

`packaging/transcritorio.iss` ganha `CurStepChanged(ssInstall)` que escreve
no `WizardForm.StatusLabel.Caption`:

> Extraindo arquivos do Transcritório (~1.6 GB) — pode levar 5 a 15 minutos.
> NÃO cancele se a barra parecer travada em 99% — é normal nesta fase.

Caso real: Denise cancelou achando que o install pendurou em 99% (o que
pareceu inativo na realidade era a fase final de descompressão lzma2/ultra64).
A label é estática (Inno não emite callback granular durante extração),
mas previne pânico.

### 3. README seção "Avisos de antivírus / SmartScreen"

Adicionada seção em `README.md` explicando que o Transcritório é unsigned
(code signing está em backlog em `docs/WINDOWS_CODE_SIGNING.md`) e que
avisos de AVAST/Norton/Kaspersky/SmartScreen são **falsos positivos
genéricos de unsigned binary**, não malware. Inclui instruções práticas:
"Mais informações → Executar assim mesmo", adicionar exceção AV pra
`Transcritorio.exe` e `whisperx.exe`, link pro doc de code signing.

Mantém também a nota sobre instalação ficar em "99%" por minutos.

## 0.1.7 — 2026-04-25

Duas mudancas combinadas: integracao do cuda_pack no instalador Windows
+ correcao de UI no header `Motor:`.

### Setup.exe baixa cuda_pack durante o install (request do user)

Antes: bundle base instalava em `C:\Program Files\Transcritorio\` (admin),
e o cuda_pack era baixado no first-launch via dialog Python. Mas se user
escolhia "Para todos", o dialog batia em "Permissao insuficiente" pra
escrever em Program Files (precisa elevar de novo). Pior UX.

Agora: o proprio Setup.exe (que ja tem admin se elevado) detecta NVIDIA
durante a instalacao via `nvidia-smi -L` e oferece um checkbox no wizard
("Acelerar transcricoes com minha placa NVIDIA, baixa ~890 MB"). Se
marcado, o installer baixa o `transcritorio-cuda-pack-{VERSION}-win64.zip`
da release do GitHub via `DownloadTemporaryFile` (Inno 6.4+) e extrai
com `tar.exe` (Win10 1803+).

Vantagens:
- Sem erro de permissao em "Para todos" (admin do install cobre).
- Auto-detect: pagina pula se nao tem NVIDIA (user CPU-only nao percebe).
- Default DESMARCADO: nao baixa 890 MB sem consent explicito.
- Fallback duplo: se download falha (offline, timeout), MsgBox amigavel
  e o app oferece de novo no first-launch via cuda_installer.py.
- `[InstallDelete]` apaga DLLs CUDA antigas antes do `[Files]` — evita
  ABI mismatch no upgrade v0.1.6 -> v0.1.7+.
- `[Setup] CloseApplications=force`: fecha app rodando sem prompt.

Gates CI novos em `.github/workflows/release.yml`:
- "Verify tag matches AppVersion": valida que push de tag v0.1.7 casa com
  `AppVersion="0.1.7"` no .iss. Sem isso, URL do cuda_pack apontaria pra
  release errada (404 silencioso).
- "Setup.exe smoke (silent install)": roda Setup.exe pos-compile com
  `/VERYSILENT /SKIPCUDA=1 /CURRENTUSER` em `$RUNNER_TEMP`. Pega erro
  Pascal silencioso. Runner do GitHub nao tem GPU, entao `/SKIPCUDA=1`
  forca o branch CPU; caminho do download real e validado manualmente
  antes de cada tag publica.

### Header `Motor:` respeita `asr_device=cpu` forcado

Bug: ao mudar Motor para CPU em "Configurar transcricao", header continuava
mostrando "Motor: CUDA (NVIDIA)" porque `runtime.describe_backend()` chamava
`detect_device()` que cacheia o resultado e ignora a config do user. O
pipeline real ja usava CPU corretamente (via `resolve_device`), so o header
mentia.

Fix cirurgico: `describe_backend()` aceita `configured_device` opcional.
Override SO quando `cfg == "cpu"` (user forcou). Outros valores (auto, cuda,
mps, None) caem em `detect_device()`, preservando branch MLX em Apple
Silicon (chamar `resolve_device("mps")` coage para cpu e mataria MLX).

`review_studio_qt::project_header_text` passa `config["asr_device"]` ao
chamar `describe_backend`.

Toy tests: 3 casos novos validando override CPU + preservacao auto-detect.

## 0.1.6 — 2026-04-25

Quarto bug pre-existente da serie, exposto agora que v0.1.5 chega ao
ponto de transcribe real do usuario:

```
LocalEntryNotFoundError: Cannot find an appropriate cached snapshot
folder for the specified revision on the local disk
```

### Root cause

`faster_whisper==1.2.1` em `utils.py:11` define o registro `_MODELS`:
```python
"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
```

Mas Transcritorio em `model_manager.ASR_VARIANTS["large-v3-turbo"]["repo"]`
usa `dropbox-dash/faster-whisper-large-v3-turbo` (mudanca em 2026-04-22
para eliminar redirect HTTP 307 que travava `snapshot_download` no bundle
frozen).

O wizard baixa em `models--dropbox-dash--faster-whisper-large-v3-turbo/`,
mas `whisperx --model large-v3-turbo --model_cache_only True` chama
`faster_whisper.WhisperModel(model_size_or_path="large-v3-turbo", ...)`
que resolve via `_MODELS` para `mobiuslabsgmbh/...` e procura no diretorio
errado.

### Fix

- `transcribe_pipeline/model_manager.py`: nova funcao `resolve_asr_repo()`
  que retorna o repo_id completo (e.g. `dropbox-dash/...`) em vez do
  shortcut. Faster_whisper aceita repo_id direto e procura no path certo
  do cache.
- `transcribe_pipeline/whisperx_runner.py`: passa `effective_repo` em
  `--model` em vez de `effective_model` (shortcut).

Ambas mudancas usam apenas o repo_id que o proprio wizard ja salvou no
cache, entao nao requer redownload e nao depende de internet em runtime.

### Bug latente analogo

`mlx_whisper_runner.py:45-46` tem seu proprio mapeamento:
```python
"large-v3-turbo": "mlx-community/whisper-large-v3-turbo"
```

Esse caminho e usado so em Mac com mlx-whisper instalado, e o repo
mlx-community e diferente (modelo MLX, nao faster-whisper). Mantido
como esta — bug nao se aplica ao path MLX.

## 0.1.5 — 2026-04-25

Fix do gate CI introduzido em v0.1.3. v0.1.4 falhou nos 3 SOs com
`LocalEntryNotFoundError` no whisperx — o gate pedia `--model_cache_only
True` mas o cache do tiny estava em tempdir do smoke-test (ja deletado).
Removido `--model_cache_only`: o whisperx baixa o tiny online (~10s) se
necessario, e em troca o gate fica independente de coordenacao de cache
entre steps. Bug do bundle nao mudou — torchvision e torchcodec ja foram
resolvidos em v0.1.3 e v0.1.4.

## 0.1.4 — 2026-04-24

Segundo bug latente pego pelo gate CI introduzido em v0.1.3: apos resolver
o `PackageNotFoundError: torchcodec`, o gate revelou que `whisperx` tambem
quebrava com `ModuleNotFoundError: No module named 'torchvision'` antes de
qualquer transcribe.

### Root cause

`torchmetrics.functional.image.arniqa.py:31` importa `torchvision` no topo
do modulo. Esse arquivo e carregado eagerly via:
```
whisperx -> asr -> vads/pyannote -> pyannote.audio -> lightning -> torchmetrics
```
O spec.py de v0.1.x explicitamente excluia `torchvision` do bundle por
ele "nao ser usado" — comentario incorreto, ele e usado transitivamente.

### Fix

- `packaging/transcritorio.spec`:
  - Removido `"torchvision"` do `excludes`.
  - Adicionado `torchvision` ao loop de `collect_submodules`.
- `.github/workflows/release.yml`:
  - Linux job instala `torchvision==0.23.0 --index-url whl/cpu`.
  - Mac job instala `torchvision==0.23.0` (default arm64+CPU/MPS wheel).
  - Windows ja tinha `torchvision==0.23.0` no install com `whl/cu128`.

### Como o bug nao apareceu antes

Mesmo padrao do bug torchcodec da v0.1.3: o CI nunca invocava `whisperx.exe`
contra audio real. v0.1.3 introduziu o gate que executa o binario contra
3s de silencio offline; foi ele que pegou os DOIS bugs em sequencia.

## 0.1.3 — 2026-04-24

Correcao de bug critico em **todas as plataformas**: `whisperx.exe` crashava
no primeiro transcribe com `PackageNotFoundError: torchcodec`. Bug existia
silenciosamente desde v0.1.1 porque o CI nunca exercitou o caminho
`whisperx.exe audio.wav` — so testava `Test-Path` do binario.

### Root cause

`transformers==5.5.1` em `audio_utils.py:55` faz:
```python
if is_torchcodec_available():
    TORCHCODEC_VERSION = version.parse(importlib.metadata.version("torchcodec"))
```

`is_torchcodec_available()` usa `find_spec("torchcodec")` que retorna
truthy pois o pacote Python esta no bundle. Mas o PyInstaller empacota
os arquivos `.py` sem empacotar o `torchcodec-0.7.0.dist-info/`, entao
`version("torchcodec")` levanta `PackageNotFoundError`. Esse caminho
dispara assim que `whisperx` importa `alignment` (toda invocacao do
whisperx.exe com audio real).

### Fix

- `packaging/transcritorio.spec` — `copy_metadata()` para 13 pacotes
  (torchcodec + torch/torchaudio/transformers/huggingface_hub/tokenizers/
  tqdm/regex/requests/packaging/filelock/pyyaml/numpy). Defensivo — qualquer
  outro pacote que chame `importlib.metadata.version("<self>")` em runtime
  estava sujeito ao mesmo bug.
- `packaging/transcritorio.spec` — `hidden_imports` ganhou `torchcodec` +
  `collect_submodules("torchcodec")`. Complementa o `copy_metadata`.
- `.github/workflows/release.yml` — novo gate "Frozen-bundle whisperx
  import chain" em Windows, Linux e Mac. Roda `whisperx` contra audio
  real (3s silencio, modelo tiny offline). Pega este tipo de bug antes
  da release publicar.

### Como o bug passou despercebido

- `transcritorio-cli.exe models smoke-test` usa `faster_whisper.WhisperModel`
  diretamente, nao carrega `whisperx.alignment` nem `transformers.audio_utils`.
- `whisperx.exe --help` passa porque argparse carrega antes dos imports lazy.
- CI sempre usou `Test-Path whisperx.exe` como validacao — existe o binario,
  mas nunca foi invocado.
- O usuario de v0.1.1 que tenha `torchcodec` instalado via pip no sistema
  (fora do bundle) nao via o bug, porque `find_spec` resolveria via PATH
  do Python externo. Apenas bundles PyInstaller frozen quebravam.

## 0.1.2 — 2026-04-24

Bundle Windows agora funciona **standalone** em PCs sem CUDA Toolkit.
Plataformas Mac e Linux inalteradas no comportamento (torch+cpu wheel
ja resolvia). Tamanho do Windows installer subiu de 596 MB para 1.63 GB
como trade-off pela robustez — v0.1.1 dependia silenciosamente do
CUDA Toolkit instalado pelo usuario e falhava em PCs sem ele.

### Split CPU/CUDA preciso

- `packaging/bundle_filter.py` — lista `CPU_EXTRA` agora mapeia
  **exatamente** as 14 DLLs CUDA que o torch cu128 carrega sob demanda
  via dlopen (cudnn_ops/adv/cnn/engines_*/graph/heuristic, nvrtc*,
  curand, cusolverMg, cufftw, caffe2_nvrtc). A lista `MINIMAL` e vazia
  — `variant=full` preserva todas as 25 CUDA DLLs para o split_bundle
  ter o que rotear ao `cuda_pack`.
- `packaging/transcritorio.spec` — coleta **explicita** das 14 DLLs
  lazy-load via `binaries`. O hook-torch do PyInstaller so pega
  imports IAT; sem essa adicao as lazy-load nunca chegam ao bundle.
- `transcribe_pipeline/runtime.py` — `cuda_libs_present()` usa
  `cudnn_ops64_9.dll` como canario do cuda_pack instalado (antes era
  `torch_cuda.dll`, que agora fica sempre no bundle base por ser IAT).
  `detect_device()` em Windows exige `cuda_libs_present()` alem de
  `torch.cuda.is_available()` — evita crash `cudnn_graph64_9.dll not
  found` em Conv/LSTM quando o usuario NVIDIA ainda nao baixou o
  cuda_pack.

### Por que o bundle Windows cresceu

Versoes 0.1.x anteriores strippavam `cufft64`, `cusparse64` e
`nvJitLink` do bundle — essas 3 sao **imports IAT** de
`torch_cpu.dll`/`torch.dll` em torch cu128. `import torch` falha com
`OSError [WinError 126]` sem elas. v0.1.1 "funcionava" so em PCs que
tinham CUDA Toolkit instalado em `C:\Program Files\NVIDIA GPU
Computing Toolkit` resolvendo via PATH. Em PCs sem CUDA Toolkit o
bundle crashava silenciosamente no primeiro `import torch`.

v0.1.2 preserva as 11 CUDA DLLs IAT obrigatorias no bundle base. As
14 lazy-load vao para o `cuda_pack` separado (download-on-demand a
partir do dialog "Detectamos placa NVIDIA" no primeiro launch).

### Artefatos

| Sistema | Arquivo | Tamanho |
|---|---|---|
| Windows 10/11 | `Transcritorio-0.1.2-Setup.exe` | ~1.63 GB |
| Windows CUDA pack | `transcritorio-cuda-pack-0.1.2-win64.zip` | ~890 MB |
| macOS arm64 | `Transcritorio.dmg` | ~600 MB |
| Linux x86_64 | `Transcritorio-x86_64.AppImage` | ~771 MB |

## 0.1.1 — 2026-04-20

Primeira versao com distribuicao cross-plataforma automatica via GitHub
Actions. Nenhuma mudanca de API ou UX no app em si; esta e uma release
de **infraestrutura** que torna o Transcritorio instalavel em macOS e
Linux alem do Windows.

### Novos artefatos de release

- **Linux AppImage** (`Transcritorio-x86_64.AppImage`, ~1.5 GB) — roda
  em qualquer distro com glibc 2.35+. Pre-requisitos: `ffmpeg` +
  libs xcb do sistema. Veja [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md).
- **macOS .dmg** (`Transcritorio.dmg`, ~500 MB, arm64) — nao assinado;
  primeira execucao requer "botao direito > Abrir". Veja
  [`docs/MAC_INSTALL.md`](docs/MAC_INSTALL.md). Icone e background
  customizados.
- **Windows Setup** (`Transcritorio-0.1.1-Setup.exe`) — mesmo formato
  de antes, agora buildado no CI em vez de localmente.

### Mudancas internas

- **CI multiplataforma** (`.github/workflows/ci.yml`): matriz Windows /
  Linux / macOS rodando toys + smokes a cada push e PR em `main`.
  Deps minimas (PySide6, numpy, keyring, cryptography — sem torch/
  whisperx/pyannote) rodam em ~2-3min por OS.
- **Release workflow** (`.github/workflows/release.yml`): gatilho
  `workflow_dispatch` (manual) ou tag `v*.*.*`. Builda 3 artefatos,
  automatiza smoke Linux via Xvfb offscreen + CLI.
- **Bundle variant infra** (`packaging/bundle_filter.py` novo): spec
  PyInstaller aceita `TRANSCRITORIO_BUNDLE_VARIANT=cpu|full` via env
  var. Em `cpu`, strippa ~3 GB de CUDA DLLs (torch_cuda, cudnn*,
  cublas*, etc.) cross-plataforma (.dll / .so / .dylib).
- **Helper `runtime.cuda_libs_present()`** — detecta se torch_cuda
  esta no bundle. Usado pela GUI para oferecer download do CUDA pack
  em primeira execucao (pipeline 2E).
- **CUDA download-on-demand** (Item 2B-E do backlog):
  - `build.ps1` produz bundle base CPU + `cuda_pack.zip` separado
  - Inno Setup `[Components]` com detecao de NVIDIA via `nvidia-smi`
  - Download sob demanda via Inno Download Plugin
  - Dialog GUI pos-instalacao oferece CUDA se NVIDIA detectada.

### Testes

De 9 toys na 0.1.0 para **17 toys + 5 smokes**. Cobertura nova:
edge cases de filtro cross-plataforma, cuda_libs_present com FS
inusual, detect_device com torch atipico, token_vault com backends
estranhos.

### Autoria

Commits de 0.1.1 sao de autoria exclusivamente humana; assistentes
LLM nao aparecem como Co-Authored-By, Signed-off-by ou similar
(conforme CLAUDE.md regra #9).

## 0.1.0 — 2026-04-14

Release inicial. Windows-only, instalador Inno Setup, ASR via WhisperX
+ diarizacao pyannote community-1. GUI em PySide6.
