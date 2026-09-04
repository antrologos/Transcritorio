# Changelog

## 0.3.0b1 — 2026-09-03 (versão de teste, não publicada)

Beta para uso lado a lado com a estável: `scripts\Instalar-Beta.bat` instala num
ambiente próprio (`%LOCALAPPDATA%\Transcritorio\beta-venv`), compartilhando os modelos já
baixados e as preferências. As duas podem ficar abertas ao mesmo tempo — a janela da beta
diz "versão de teste" no título. Para remover, basta apagar a pasta. Não está no site nem
no PyPI.

Busca por sentido e "Perguntar às entrevistas com AI" refeitos, depois de um teste com
8 entrevistas reais e um gabarito de 12 perguntas julgado à mão.

- **Por que a busca trazia trechos sem relação.** O modelo antigo era de paráfrase
  ("estas frases dizem o mesmo?"), não de busca ("este trecho responde a esta
  pergunta?"); cada turno era pontuado junto com pedaços dos vizinhos, mas a lista
  mostrava o turno sozinho ("Autorizo." como "muito próximo"); e os rótulos eram
  absolutos. Agora a unidade é a **passagem** (turnos contíguos de ~100 palavras, com
  quem fala), o encoder é de recuperação (`multilingual-e5-small`, mesmo tamanho do
  antigo; `multilingual-e5-large-instruct` como opção de qualidade, aplicado quando
  instalado), os acertos literais entram na disputa, e um **reordenador**
  (`bge-reranker-v2-m3`, opcional) lê pergunta e trecho juntos e decide o que
  "Responde" e o que é só "Relacionado" — o resto some. Precisão dos 5 primeiros no
  gabarito: 0,48 → 0,67. Índices em `.npy` (menores), refeitos automaticamente quando a
  transcrição muda ou o modelo troca.
- **Um só botão.** "Encontrar trechos" e "Perguntar" eram a mesma busca; agora
  "Perguntar" mostra primeiro os trechos que tratam do tema (em segundos, em qualquer
  máquina) e, quando o modelo de análise está disponível, escreve a resposta citando-os
  com `[n]` clicáveis. Campo "até N trechos" (padrão 20) — vem menos quando menos
  trechos tratam do tema, e o rodapé diz quantos ficaram de fora e por quê. "Cancelar a
  resposta" mantém os trechos; cancelar a reordenação mostra a ordem por semelhança.
- **Perguntas sobre o conjunto** ("do que falam as entrevistas?") não são busca: a AI
  responde pelos resumos por entrevista, citando as entrevistas `[ID]`, com o escape
  "responder pelos trechos mesmo assim"; sem resumos, oferece "Resumir as N entrevistas
  agora". Quando nada responde, a AI não é chamada e a janela explica.
- Linha "Nesta máquina: …" na abertura da janela; a resposta escrita continua exigindo
  placa NVIDIA, os trechos não. `contexto_pesquisa.md` (roteiro, codebook, nomes) nasce
  com o projeto. Recusa da AI reconhecida com acentos; geração com a receita do
  fabricante do Qwen. Notas de tema do Resumir passam a ser guardadas (base da função
  de temas). CLI `search`/`ask` com o mesmo pipeline da janela.
- **Entrevista que sumia da busca.** Um arquivo de índice corrompido (sincronização
  interrompida, disco cheio) continuava valendo como atual: não era refeito, e aquela
  entrevista simplesmente deixava de aparecer nos resultados, sem aviso. Agora o índice só
  passa por atual se abrir e tiver o tamanho certo.
- **O modelo lia as instruções do formulário.** O `contexto_pesquisa.md` passou a nascer
  com o projeto, e o texto de exemplo ("Preencha o que fizer sentido…") ia para a AI como
  se fosse o contexto do estudo, em todas as perguntas e resumos. Agora só vai quando você
  escreveu alguma coisa nele.
- **"A AI local falhou" com o trabalho feito.** Quando o ambiente de análise era criado
  a partir do Python da Microsoft Store, o Windows redireciona o que ele grava em
  `%LOCALAPPDATA%` para uma pasta privada do pacote: a resposta era escrita, mas o
  aplicativo procurava o arquivo no caminho real e não achava nada. Os resultados das
  análises (perguntar, visão geral, glossário, temas) agora voltam pela saída do próprio
  processo, com o arquivo como reserva.

**Temas das entrevistas, codificação e exportação para o QualiLab** (menu Analisar →
"✨ Temas das entrevistas…"): a segunda metade do que a busca por sentido tornou possível.

- **Os temas mais tratados, sem pergunta nenhuma.** A janela agrupa *todos* os trechos
  das entrevistas escolhidas por semelhança de sentido e lista os temas com **todos** os
  trechos de cada um — sem teto. O agrupamento é aritmética (numpy): roda em qualquer
  computador, em segundos, e não usa a AI que escreve. Um trecho pode entrar em **mais de
  um tema** (assuntos não são caixas separadas), e o que não se parece com nada fica em
  "sem tema definido" em vez de ser empurrado para o tema mais próximo. "Quantos temas"
  é ajustável (automático, ou de 1 a 40).
- **Quem entra na análise.** Em entrevistas não se costuma codificar quem pergunta; em
  grupos focais, quem modera — e os nomes desses papéis mudam de pesquisa para pesquisa.
  Então o app **pergunta**, uma vez por projeto, na primeira descoberta: a janela lista os
  falantes com o peso de cada um (turnos, palavras, entrevistas) e marca como "sugerido de
  fora" quem parece conduzir. Você decide, e pode inclusive manter todo mundo. A fala de
  quem fica de fora **continua na transcrição e no arquivo exportado**, logo acima da
  resposta, como contexto: ela só deixa de influenciar o agrupamento e de receber código.
  Um trecho em que quem você escolheu quase não fala — a leitura do termo de consentimento,
  um "tá bom" respondendo a uma pergunta longa — não entra nos temas: aparece em «sem tema
  definido», onde continua visível e codificável. Sem esse piso, as respostas de cortesia
  ao roteiro se pareciam entre si e formavam um tema grande de nada (medido na cópia de
  teste: 54 trechos em todas as 10 entrevistas).
- **Nomes: imediatos, e melhores depois.** Cada tema nasce com seus termos
  característicos (o que aparece nele e não nos outros). Com o modelo de análise
  instalado, a AI lê os trechos centrais e escreve nome e descrição de cada tema **em
  segundo plano** — a janela nunca espera por ela, e um nome que você tenha dado nunca é
  sobrescrito. Sem placa NVIDIA, ficam os termos, e a janela diz por quê.
- **Codificar.** Um ou mais códigos por trecho (códigos não são exclusivos), aplicados de
  uma vez aos trechos marcados de um tema ou um a um. O codebook nasce com os códigos que
  você tiver escrito no `contexto_pesquisa.md`. Fica em `Transcricoes/08_codificacao/`, em
  arquivos legíveis, separado da transcrição — nada é alterado no texto original.
- **Exportar para o QualiLab** (`.qualilab`) ou para planilha (CSV, com `;` — o separador
  que o Excel em português espera). Cada entrevista vira um documento com o texto em
  blocos "[hh:mm:ss] Falante: texto", e cada código aponta para a posição exata do trecho
  nesse texto — a citação e o texto apontado são o mesmo, por construção. Se você editar a
  transcrição **depois** de codificar (dividir um bloco, por exemplo), os trechos que
  deixaram de bater não são exportados com o texto errado: ficam de fora e a janela diz
  quantos foram.

## v0.2.8 — 2026-09-02

Rodada de revisões de um lote real de 5 entrevistas (máquina com GPU):
por que as ações ficam cinza, tempo do lote e as vozes das entrevistas que
não estavam abertas.

- **As ferramentas de AI não "sumiram" durante o lote — e agora o app diz
  isso.** Durante uma transcrição em lote as análises com AI (e as demais
  ações) ficavam desabilitadas com o motivo escondido num tooltip que um
  item cinza de menu nem mostra; a leitura natural era "as ferramentas de AI
  não vieram instaladas". Agora: uma faixa na lista de arquivos explica
  ("⏳ Lote em andamento — Arquivo 3 de 5 · … As análises com AI e as
  demais ações voltam sozinhas quando terminar"); o menu Analisar ganha uma
  linha com o mesmo aviso; o tooltip de todo item cinza diz o estado do
  lote; e os itens de AI continuam **clicáveis** — o clique explica na
  própria faixa ("O Transcritório executa uma tarefa por vez. Em
  andamento: … 'Resumir a entrevista com AI' fica disponível quando o
  lote terminar — não é preciso fazer nada"), sem janela modal e sem
  executar nada. O aviso modal das demais ações passa a dizer o estado do
  lote e que o aplicativo não está travado.
- **Separação de falantes num só processo por lote.** Cada arquivo abria um
  processo novo só para separar as vozes, e ~35 s de cada um eram abrir o
  Python, importar torch/pyannote e carregar o modelo — mais que a própria
  separação numa entrevista de 50 min em GPU. Agora o lote abre **um**
  servidor de separação (`transcritorio-cli diarize-serve`) no início, que
  carrega o modelo uma vez, em paralelo com o preparo e a transcrição do
  primeiro arquivo, e atende um pedido por arquivo. Se o servidor cair, o
  arquivo segue pela rota antiga (um processo por arquivo), sem intervenção.
  Lote de um arquivo continua como era. Entre um pedido e outro o servidor
  devolve a memória de GPU em cache ao motor de transcrição.
- **Áudios preparados em paralelo no início do lote.** Com dois ou mais
  arquivos, a conversão para WAV (ffmpeg) vira um único passo do lote,
  duas conversões por vez ("Preparando os áudios (2 de 5)"), antes das
  transcrições. Um arquivo cuja conversão falha é pulado com a causa na fila
  de processamento; os outros seguem.
- **Vozes por identificar ao fim do lote.** Num lote de 5 entrevistas o app
  perguntava "De quem é esta voz?" uma vez só — para a transcrição que
  estava aberta; as outras perguntavam apenas quando abertas. Agora, ao fim
  do lote, a lista mostra a faixa "🎙 N entrevistas com vozes por
  identificar" com **Identificar agora…** (abre cada uma em sequência e
  pergunta, com os nomes sugeridos pelo reconhecimento de vozes recorrentes;
  cancelar numa delas interrompe a sequência e a faixa segue com o
  restante) e **Depois** (cada entrevista continua perguntando ao ser
  aberta).

## v0.2.7 — 2026-09-02

- **Corrigido: caracteres "�" na barra de progresso** ("Separando falantes de
  X � 84%"). As etapas que rodam em subprocesso (separar falantes, canais,
  conferir trocas) escreviam a saída na codificação do Windows e o app a lia
  como UTF-8. Agora todo subprocesso do app escreve em UTF-8.

## v0.2.6 — 2026-09-02

- **Corrigido: a aceleração GPU do TAGARELA parava de funcionar em
  silêncio quando o Python do app mudava.** O pacote `onnxruntime-gpu` é
  por versão de Python; o instalado no 3.13 (v0.2.2) não carregava no 3.12
  (v0.2.3+): o worker falhava com "DLL load failed" e a transcrição caía
  para o processador (8–16× o tempo real em vez de ~60×) — numa máquina
  com placa NVIDIA, sem aviso que ficasse. Agora o app registra o Python do
  pacote, detecta a incompatibilidade ("Incompleto — precisa ser
  reinstalada: o pacote foi instalado para o Python 3.13…" em Gerenciar
  modelos), oferece a reinstalação ao transcrever mesmo para quem havia
  recusado a oferta original, instala a wheel do Python certo
  (`--python` do próprio app) e recusa uma instalação que venha para outro
  Python. A queda para a CPU passa a ficar registrada na fila de
  processamento (`gpu_fallback`).
- **Progresso do lote em dois níveis, sem números confusos.** Durante um
  lote, a coluna Transcrição ficava em "Processando 0%" para todos os
  arquivos (a lista só era redesenhada no fim) e a barra de baixo mostrava
  dois percentuais com significados diferentes (o do lote e o interno do
  motor). Agora **cada arquivo mostra a própria etapa e o próprio avanço**
  na lista ("Transcrevendo 46%", "Separando falantes 12%", "Na fila",
  "Transcrita" assim que termina, com "(WAV pronto)"), atualizada a cada
  1,5 s sem reconstruir a tabela; e a **barra de baixo é só o lote**:
  "Arquivo 2 de 5 · F03R_0729 · transcrevendo com o TAGARELA… · ~12 min
  restantes". O filtro "Processando" da lista acompanha.

## v0.2.5 — 2026-09-02

Três decisões de produto num só lançamento, todas com medição: o passo de
2 s na separação de falantes vira padrão em todos os dispositivos; o
TAGARELA vira o motor padrão em todas as máquinas; e transcrever em outro
idioma passa a ser explicado e resolvido com o Whisper de reserva. Mais 12
correções da auditoria "qualquer nome de arquivo válido funciona".

- **Separação de falantes 2× mais rápida, em CPU e em GPU, com a mesma
  qualidade.** A/B automático (12 conversas sintéticas com verdade por
  construção + 10 entrevistas reais julgadas pelo verificador acústico do
  próprio app): passo de 2 s entre janelas de 10 s (em vez de 1 s) manteve
  DER, posição das fronteiras e flags "duvida" (105 → 110) iguais dentro do
  ruído, e cortou o tempo pela metade — CPU 0,12× → 0,060× (1 h de áudio ≈
  4 min na máquina de referência de 24 threads; ~11 min com 8 threads, ~22
  min com 4), GPU 44 s → 23 s por hora. Chave `diarization_segmentation_step` (0.1 =
  pyannote original). Estimativas do app, README, INSTALL e site
  acompanham.
- **O TAGARELA é o motor padrão em todas as máquinas** (antes, só nas sem
  placa de vídeo): treinado para o português falado — segundo os autores do
  modelo, em fala espontânea erra menos palavras que o Whisper large-v3
  (cerca de 14% contra 23%) —, com pontuação e tempos por palavra
  nativos; em GPU roda no processador (16× o tempo real) até o pacote
  onnx-gpu ser instalado — ainda 2× mais rápido que o turbo em GPU. Um
  Whisper acompanha como **reserva para outros idiomas**: `large-v3-turbo`
  com GPU, `small` no resto (Mac inclusive, pela rota MLX). O assistente
  marca os dois; a faixa de oferta aparece para quem já estava no Whisper
  em qualquer máquina, com texto novo; o padrão de fábrica é
  `parakeet-pt`; rótulos "Recomendado"/"reserva" nos modelos; o aviso de
  tempo do assistente fala do TAGARELA e da separação de falantes; a
  estimativa antes do lote também aparece em máquinas com GPU quando o
  TAGARELA vai rodar no processador.
- **Outro idioma com o TAGARELA: aviso didático e o caminho para outro
  modelo.** Nota inline ao escolher um idioma que não é português (em
  Configurar transcrição, na aba Propriedades, em Editar propriedades e no
  assistente). Ao Transcrever, a janela "Este lote tem outro idioma" explica
  onde o modelo mora (Ferramentas → Configurar transcrição → Modelo) e
  oferece o Whisper com a qualidade de cada opção instalada (o small é
  rápido mas erra mais; o turbo é o mais preciso), baixando se preciso —
  só para o lote ou para o projeto inteiro; "Configurar transcrição…" abre
  o diálogo do motor. "Automático" conta como português também no motor
  (o lote não cai mais no meio com "N falha(s)").
- **Qualquer nome de arquivo válido no sistema funciona (auditoria de 12
  defeitos).** Apagar transcrição/Lixeira de `Sonia` não leva mais os
  derivados de `Sonia.Venancio` (o dono é o id mais longo); colchetes no
  nome não quebram mais o glob (Apagar, Lixeira, Documentos); o QC não
  herda mais o JSON de `Entrevista 10` para `Entrevista 1`; `Entrevista
  #3.m4a` não some mais ao reabrir o projeto (parser YAML); nome começando
  com "-" não vira mais flag ao separar falantes; "Mostrar na pasta" aceita
  vírgula no caminho; o atalho da área de trabalho aceita apóstrofo no nome
  do usuário Windows; renomear a mídia só na caixa mantém título, rótulos e
  idioma; `--ids` na CLI ignora maiúsculas; o filtro da lista ignora
  acentos. E **dois arquivos com o mesmo nome em pastas diferentes são
  duas entrevistas** (a segunda ganha o sufixo com o nome da pasta e, se
  ainda colidir, um número), inclusive nomes iguais só na caixa; na mesma
  pasta (.mp3 + .m4a) continuam a mesma gravação.

## v0.2.4 — 2026-09-02

Correção de um relato real de beta (arquivo com espaço no nome falhava ao
montar a transcrição editável, depois de mais de uma hora separando
falantes), a causa das falhas visível no diálogo, e a separação de
falantes ~3× mais rápida em CPU.

- **Separação de falantes ~3× mais rápida em CPU, com resultado idêntico.**
  Medição de 2026-09-02: 94% do tempo dessa etapa era a rede de
  embeddings (ResNet34) rodando **três vezes por janela de 10 s** — uma por
  vaga de falante, mesmo numa entrevista a dois — sendo que a máscara de
  quem fala só entra no *pooling* final. Agora a rede roda uma vez por
  janela e só o pooling é feito por falante (`diar_fast.py`), sem tocar o
  pyannote instalado: embeddings numericamente iguais (diferença máxima
  4×10⁻⁸, cos-sim 1,000000 no teste contra o caminho original) e mesmos
  hooks de progresso. Numa entrevista real de 62 min em CPU: **24,7 min →
  7,1 min**, com separação de falantes idêntica (DER 0,000%, mesmos 648
  segmentos). A estimativa mostrada ao transcrever acompanha (1 h de áudio
  ≈ 7 min na máquina de referência; ~20 min num notebook de 4 núcleos).
  Chave de escape `diarization_fast_embeddings: false` no `run_config.yaml`.
- **Corrigido: arquivo com espaço ou acento no nome falhava em "montando
  transcrição editável: 1 falha(s)"** com o TAGARELA (e com o MLX no Mac) —
  depois de gastar a hora inteira da separação de falantes. O motor gravava
  o texto transcrito com o nome "sanitizado" (`Sonia_Venancio.json` para
  `Sonia Venancio.m4a`; acentos e cedilha também viravam `_`), e a etapa
  final procurava pelo nome original. Agora a saída nasce com o nome
  idêntico ao do arquivo: vale qualquer nome que o sistema operacional
  aceite (espaços, acentos, maiúsculas, parênteses, colchetes, `&`, `%`,
  `#`, apóstrofo…); só separadores de caminho viram `_`
  (`utils.safe_output_id`, defesa contra manifest editado à mão). Quem já
  tem a separação de falantes pronta não precisa refazê-la: basta
  transcrever de novo sem "Separar falantes agora" e a montagem a
  aproveita. Reproduzido ponta a ponta com
  `Entrevista (Sônia) ÇÃO & cia [2], 100% #1 O'Neil.m4a`.
- **A causa real de uma falha agora aparece.** O diálogo dizia só
  "montando transcrição editável: 1 falha(s)" e o motivo ia para o
  console. A montagem passa a devolver a causa ("Não encontrei o texto
  transcrito de … (pasta Transcricoes/02_asr_raw)"), e ela aparece no
  diálogo de erro e na fila de processamento (Ferramentas).

## v0.2.3 — 2026-09-02

Correções guiadas por três relatos reais de beta testers (instalação
travada no Python 3.14; "o padrão continuou o Whisper small"; "congelou
no 88%" em computador sem placa de vídeo).

- **A separação de falantes ganhou barra de progresso REAL e estimativa
  de tempo.** O "congelou no 88%" era o teto de um relógio calibrado para
  GPU: em CPU o pyannote leva ~0,4× a duração do áudio (medido: 1 h de
  entrevista ≈ 25 min na máquina de referência; até ~1 h num notebook de
  4 núcleos) e a barra ficava parada esse tempo todo. Agora o progresso
  que o próprio pyannote informa (por trecho e por lote de vozes) move a
  barra, a mensagem diz "Separando falantes — N% (tempo decorrido,
  ~M min restantes)" e a carga do modelo em CPU avisa que pode levar ~1
  min. A mensagem honesta da fase silenciosa da transcrição (v0.2.2)
  passou a chegar de fato à barra de status, e o rótulo do trabalho diz
  o motor certo ("com o TAGARELA").
- **Sem placa de vídeo, a janela "Quantas pessoas falam?" mostra a
  estimativa do lote** ("transcrição ≈ 4 min · separação de falantes ≈
  49 min") e a caixa **"Separar falantes agora"**. Desmarcada, o lote sai
  só com o texto — em minutos — e a lista oferece "Separar falantes
  agora" para completar as vozes depois, em lote (que agora também
  pergunta quantas pessoas falam, se ainda não souber). Nada é gravado
  no projeto: a escolha vale só para aquele lote.
- **Faixa na lista migra quem já estava instalado para o TAGARELA**: em
  máquina sem placa de vídeo com o projeto no Whisper aparece "Este
  computador não tem placa de vídeo — o motor TAGARELA transcreve 1 hora
  em poucos minutos [Usar o TAGARELA] [Continuar com o Whisper]". Aceitar
  troca o projeto e o padrão do computador e já baixa o modelo; recusar
  é lembrado. A oferta ao clicar Transcrever deixou de ser bloqueada por
  arquivos com idioma "Automático" (mesmo ajuste no guard do motor), e o
  selo da barra de status mostra o modelo configurado — com "(não
  instalado)" quando outro seria usado no lugar.
- **Python fixo em 3.12 em todos os comandos de instalação** (instalador
  .bat, README, guias, site e os comandos de Reparar/aceleração NVIDIA do
  app). Caso real de beta tester: o `uv` escolhia o Python mais novo da
  máquina (3.14) e o `torchcodec` ainda não tem pacote para ele — o teto
  `<3.14` do pacote não faz o `uv` trocar de interpretador sozinho. Com
  `--python 3.12`, o `uv` baixa o Python oficial certo se não houver.
- **Instalador .bat blindado contra falhas de rede** (caso real de beta
  tester: a causa do erro rolava para fora da tela). Tempo limite de
  download ampliado para conexões lentas (`UV_HTTP_TIMEOUT=600`), segunda
  tentativa automática com registro em `%TEMP%\Transcritorio-instalador.log`
  e as últimas linhas do registro impressas junto da mensagem de erro; a
  lista de endereços a liberar na TI passa a citar o `github.com` (de
  onde o `uv` baixa o Python) e o espaço em disco necessário (~4 GB).
  Mesma blindagem no `Atualizar-Transcritorio.bat`.

## v0.2.2 — 2026-09-01

- **TAGARELA vira o motor padrão em máquinas sem placa de vídeo**
  (Windows/Linux; decisão motivada por beta testers travados no Whisper
  em CPU). O assistente de primeiro uso agora recomenda o Parakeet
  pt-BR "TAGARELA" (13–25× o tempo real em CPU, tempos por palavra
  nativos) com o Whisper `small` de reserva para outros idiomas
  (~3,5 GB no perfil Essencial). Quem já estava instalado recebe UMA
  oferta de troca ao clicar Transcrever em CPU — qualquer resposta
  segue transcrevendo, e a recusa é lembrada. No Mac nada muda (lá o
  Whisper já tem a rota rápida Metal/MLX). Com GPU NVIDIA, o padrão
  segue `large-v3-turbo`.
- **A barra não "trava mais aos 43%"** (na percepção): na fase
  silenciosa de marcação dos tempos por palavra em CPU, a barra agora
  diz o que está acontecendo ("pode demorar; a barra fica parada, mas o
  trabalho continua") — e as mensagens não falam mais em "GPU" numa
  máquina sem placa.

- **Instalador .bat acha o uv mesmo quando o winget não cria o atalho**
  (caso real de beta tester): em algumas máquinas o pacote portable fica
  só dentro de `WinGet\Packages\astral-sh.uv*` — a busca agora varre o
  pacote, o instalador oficial da Astral (`.local\bin`) e o escopo de
  máquina, e o passo 1 só diz "concluído" depois de confirmar o
  executável. O erro passou a apontar a causa mais comum (App Installer
  desatualizado, com o link da Microsoft Store). Guia ganhou o caso
  "transcritorio não é reconhecido" com terminal já aberto (abas novas
  herdam o PATH antigo; fechar todas as janelas do terminal resolve).

- **Dividir bloco: o tempo da fronteira agora segue o cursor de texto.**
  Antes, a posição do player (que durante a reprodução está sempre dentro
  do bloco tocando — e ainda avançava enquanto o diálogo de confirmação
  estava aberto) ou um cursor antigo na onda sonora decidiam o tempo da
  divisão, produzindo fronteiras incoerentes com o texto: o destaque que
  acompanha o play trocava de bloco em momentos que não correspondiam à
  divisão feita. Nova ordem: clique deliberado na onda sonora > palavra
  sob o cursor de texto (tempos por palavra) > player pausado dentro do
  bloco > estimativa pela posição no texto. A barra de status continua
  dizendo qual regra valeu.

## v0.2.1 — 2026-09-01

Correções da checagem geral pós-lançamento (auditoria de 48 achados;
26 confirmados e corrigidos).

- **Aba Propriedades mais defensiva ao trocar de entrevista**: um guard
  extra recusa salvar propriedades se a entrevista carregada no painel
  não é mais a selecionada (re-sincroniza e avisa na barra de status), e
  o botão Salvar volta a habilitar corretamente após edições não salvas.
- **Janela "Sobre" reconhece a instalação oficial**: instalado via
  uv/PyPI mostra "Instalação oficial (uv/PyPI)" com a versão real do
  pacote; rodando do código-fonte mostra "Versão de desenvolvimento".
- **Aviso de instância única neutro**: quando outra janela de versão
  diferente está aberta, o aviso diz apenas "versão diferente" (sem
  presumir qual é a mais nova) e não dispara mais durante a abertura
  normal de uma janela ocupada.
- **Revisão de grafias e glossário sem duplicatas**: entrevistas
  transcritas em duplicidade na lista não geram mais itens repetidos.
- **Instalador .bat mais robusto**: detecta o FFmpeg recém-instalado
  mesmo antes de reabrir o terminal (estende o PATH da sessão), avisa
  para fechar o aplicativo antes de atualizar e explica melhor os erros
  de rede.
- **Ícone multi-resolução**: o .ico agora traz 7 tamanhos (16–256 px) —
  antes só um, o que deixava o ícone serrilhado na barra de tarefas.
- **Números medidos nesta máquina, em todos os materiais**: TAGARELA em
  CPU publicado como faixa 13–25× o tempo real (era "~25×"); perfil
  Completo ~20 GB de disco (análise AI soma ~10 GB); 15 outros idiomas
  com tempos por palavra (16 pacotes de alinhamento, incluindo o
  português); grupos focais "bem até 6–8 falantes". README, docs, site
  e FAQ alinhados aos mesmos números.
- Textos que apontavam para a interface antiga corrigidos (selo do
  Motor na barra de status; "Ferramentas → Gerenciar modelos…").

## v0.2.0 — 2026-08-31

Primeira versão publicada no PyPI — `uv tool install transcritorio` é o
canal oficial daqui em diante.

> **Nomes que mudaram durante o desenvolvimento da 0.2** (os bullets
> mais antigos desta seção citam os rótulos da época): *Reprocessar
> falantes* e *Atualizar transcrição editável* saíram da interface (a
> remontagem é automática; a separação pendente vira o botão *Separar
> falantes agora* na lista, e o refazer por entrevista é *Entrevista →
> Refazer separação de falantes…*); *Melhorar falantes deste arquivo* →
> *Refazer separação de falantes…*; *Gerar resumo com temas* →
> *✨ Resumir a entrevista com AI*; *Instalar modelos…* /
> *Configurar modelos…* → *Ferramentas → Gerenciar modelos…*; o selo
> Modelo/Motor saiu do cabeçalho e vive na barra de status (embaixo).

**Mudança de canal de distribuição**: o standalone (Setup.exe/.dmg/AppImage)
foi descontinuado — antivírus/SmartScreen bloqueavam a instalação sem
assinatura digital, e o SignPath recusou a assinatura gratuita. O canal
oficial passa a ser **PyPI + uv** (`uv tool install transcritorio`); ver
`docs/INSTALL_WINDOWS.md` e `docs/LEGACY_STANDALONE.md`.

- **Instalação em um clique (Windows)**: `Instalar-Transcritorio.bat`
  e `Atualizar-Transcritorio.bat` — baixar, clicar duas vezes e
  esperar. Fazem exatamente os comandos do guia (winget → uv → FFmpeg
  → PyPI), só de fontes oficiais assinadas, sem senha de administrador
  e com mensagens em português para quem nunca abriu um terminal. E o
  aplicativo instalado agora tem ícone: a janela e o atalho da área de
  trabalho mostram o símbolo do Transcritório (antes herdavam o ícone
  genérico do Python).
- **Revisão de grafias mais honesta e mais útil**: o botão "Revisar
  grafias…" agora distingue três situações — nunca analisado (oferece
  rodar a análise ali mesmo), análise desatualizada (entrevistas
  transcritas depois do último glossário; oferece re-analisar) e em
  dia. Antes, ele dizia "nada a corrigir" mesmo sem a análise nunca
  ter rodado. E a correção sugerida pela AI virou um campo editável:
  se ela sugerir "UEG" onde o certo é "UERJ", você digita a grafia
  certa e aplica a todas as ocorrências marcadas.
- **Painéis da transcrição do seu jeito**: as seções da aba
  Transcrição (vídeo, áudio, blocos e editor) viraram quatro painéis
  independentes — cada divisor arrasta de verdade, qualquer seção
  pode ser recolhida a zero e a que importa pode ocupar a janela
  inteira. Em entrevistas de vídeo, o painel de imagem abre compacto
  e um botão "Ocultar vídeo" o esconde sem parar o áudio (antes o
  vídeo tomava um terço da janela e os blocos ficavam com duas
  linhas). O aviso de trocas de falante ganhou um × para dispensar.
- **Reforma de interface — primeiro contato e polimento (R4)**: a
  etapa final da reforma. Arrastar gravações sem projeto aberto deixou
  de ser ignorado — o aplicativo oferece criar o projeto ali mesmo e
  já adiciona os arquivos; depois de adicionar mídia, a barra de
  status aponta o próximo passo e o botão Transcrever fica em
  destaque enquanto tudo está pendente. Resumo e glossário prontos
  agora são anunciados na própria aba Documentos (uma faixa discreta
  com Abrir/Revisar grafias, no lugar das janelas que interrompiam), e
  a verificação — que terminava em silêncio — também. A aba
  Propriedades ficou editável (língua, falantes, rótulos e contexto,
  com salvar explícito que grava só o que mudou); o diálogo antigo
  continua para editar várias entrevistas de uma vez. A Fila de
  processamento mostra o tempo restante ("cerca de 3min") em vez do
  horário de término. Quem abre o aplicativo logo após atualizar é
  avisado quando a janela antiga ainda está aberta ("feche e abra de
  novo"). E ~70 textos de assistentes e diálogos foram corrigidos —
  inclusive instruções que apontavam para menus que não existem mais
  desde a reorganização (agora a guarda automática de textos também
  vigia assistentes e diálogos).
- **Reforma de interface — comandos consolidados (R3)**: cada função
  passou a ter UM nome e UM lugar. "Exportar…" é um comando só (o
  escopo se escolhe no diálogo); o trio confuso de falantes virou dois
  comandos claros — "Dar nome às vozes…" e "Refazer separação de
  falantes…" (que avisa: descarta edições e guarda cópia em
  Documentos › Versões anteriores) — e a remontagem da transcrição
  ficou automática; "Limpar transcrição gerada" agora se chama
  "Apagar transcrição… (a gravação fica)". Quando há entrevistas
  transcritas ainda sem separação de vozes e o recurso está
  instalado, a lista oferece um botão "Separar falantes agora" — sem
  procurar em menu. O botão Transcrever age direto no clique
  (☑ marcadas; sem nenhuma marcada, todas as pendentes) e a setinha
  guarda as variantes e a chave "Separar falantes", que saiu da barra
  de ferramentas. F5 ("Recarregar lista") também procura gravações
  novas nas pastas do projeto, e "Créditos" e "Sobre" viraram um
  único "Sobre o Transcritório". Quatro comandos órfãos ou duplicados
  foram removidos.
- **Reforma de interface — abas e a casa dos resultados (R2)**: o
  painel direito ganhou três abas. **Transcrição** segue sendo o
  trabalho diário; **Documentos** é a casa de tudo que o app produz —
  transcrição final, legendas, ✨ resumo, glossário, relatório de
  verificação e versões anteriores, cada item com Abrir/Gerar ali
  mesmo e a data em que foi feito (fim do "não sei onde salvou");
  **Propriedades** reúne os dados da entrevista aberta (gravação
  original, formato, duração, língua, falantes, contexto). Os três
  avisos acima dos blocos passam a ocupar um único espaço, um por vez,
  do mais urgente ao menos.
- **Reforma de interface — janela nova (R1)**: a janela ganhou a
  estrutura definitiva: barra de ferramentas fixa no topo (Adicionar
  mídia · Transcrever | Salvar · Exportar | ✨ Perguntar), barra de
  status embaixo (atividade e progresso à esquerda; salvamento e o selo
  clicável Modelo/Motor à direita), e **6 menus reorganizados** —
  Projeto, Editar, Entrevista, Analisar, Ferramentas e Ajuda — com cada
  função em um lugar previsível. O cabeçalho antigo saiu (o nome do
  projeto agora está na barra de título) e a janela ganhou uma linha a
  mais de espaço útil.
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
  de entrevista). Roda inteiramente na CPU a 13–25× tempo real
  (conforme o áudio; medições de 2026-08-28 e 2026-08-31). Marcado
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
  fecha); **Ctrl+Shift+F** (ou Analisar → Buscar palavras…) abre a
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
