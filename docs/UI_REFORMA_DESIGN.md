# Programa R — Dossiê de design da reforma de UI

**Status**: EM APROVAÇÃO (aguardando o aceite do autor)
**Data**: 2026-08-31
**Plano-programa**: `.claude/plans/` (Programa R). Nenhuma mudança
visível de UI é implementada antes do aceite deste dossiê.

Por que "Programa R": o acervo tem três coisas chamadas "Fase 5"
(Mac/Linux MVP, auditoria de design, i18n). Este programa é a
auditoria+reforma de design; a i18n foi adiada de vez (2026-08-31).

---

## 1. Diagnóstico em números (inventários de 2026-08-31)

| Métrica | Hoje |
|---|---|
| QActions / itens de menu | 59 / 52 (duplicações ×2 e ×3; 4 ações órfãs) |
| Menus | 4 (Transcrever tem 19 itens misturando produção, falantes, AI, fila, config e modelos) |
| Diálogos | 16 abríveis (+3 QMessageBox que agem como diálogos) |
| Strings visíveis | ~1.080 distintas; ~40% dos rótulos sem acento; "AI"×"IA"; "…"×"..." |
| Cores | 32 hex hardcoded em 38 pontos; 14 font-sizes literais |
| Estrutura | Sem QToolBar nem QStatusBar; "status" no TOPO; 3 banners empilháveis |
| Resultados | Resumo/glossário entregues por QMessageBox "ponte mínima"; pastas 05/06/07 invisíveis |
| Dívida registrada | 67 itens (memória + planos), empilhados de propósito para esta rodada |

---

## 2. Arquitetura-alvo da janela

Decisões estruturais:

1. **QToolBar real** (fixa): botão = QAction (um ponto de verdade para
   rótulo/tooltip/estado/atalho — mata a deriva de vocabulário).
   5 lugares na ordem da jornada:
   `[+ Adicionar mídia ▾] [▶ Transcrever ▾] | [Salvar] [Exportar…] |
   [✨ Perguntar às entrevistas…]`. A ênfase primária (verde) caminha
   com o estado: sem mídia → Adicionar grita; com pendentes →
   Transcrever; edições não salvas → Salvar.
2. **QStatusBar real, EMBAIXO** (convenção universal): atividade à
   esquerda (etapa em gerúndio + barra + Cancelar + link "Fila (N)"),
   estado de salvamento ao centro ("Salvo às 14:02"), selo
   Motor/Modelo à direita (link para Configurar transcrição). Barra
   nunca parada em 0%.
3. **Header customizado removido**: o nome do projeto vai para a barra
   de título; os links Modelo/Motor vão para a status bar. Ganha-se
   uma linha.
4. **Painel direito com 3 abas**: **Transcrição** (trabalho diário) |
   **Documentos** (casa definitiva dos resultados — seção 4) |
   **Propriedades** (absorve o diálogo de metadados para seleção
   única; o diálogo sobrevive só para edição em lote).
5. **Lista de entrevistas: 10 → 5 colunas**: ☑ · Entrevista · Duração
   · **Situação** (unifica status+progresso: "Pendente" /
   "Transcrevendo… 43%" / "Pronta para revisar" / "Revisada") ·
   Avisos. Formato/Língua/Falantes/Rótulos/Contexto migram para a aba
   Propriedades. Clique = selecionar (painel mostra o estado + CTA
   "Abrir para revisar"); duplo clique/Enter = abrir.
6. **Falantes dentro da aba Transcrição**: faixa fixa de chips
   coloridos entre player e blocos ("● Maria" "● Entrevistador"
   "● Voz não identificada"), botão "Dar nome às vozes…", menu "⋯"
   com a destrutiva. Os 3 banners atuais viram **um slot único** com
   fila de prioridade. O checkbox "Separar falantes" sai da toolbar
   (instalado ⇒ aplicado; vive no dropdown do Transcrever e em
   Configurar transcrição).

### Wireframe

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Projeto  Editar  Entrevista  Analisar  Ferramentas  Ajuda                    │
├──────────────────────────────────────────────────────────────────────────────┤
│ [+ Adicionar mídia ▾] [▶ Transcrever ▾] │ [Salvar] [Exportar…] │ [✨ Pergun- │
│                                         │                      │ tar às      │
│                                         │                      │ entrevistas…]│
├──────────────────────────┬───────────────────────────────────────────────────┤
│ Entrevistas              │ Entrevista com Maria · 1 h 42 min  [⟳ Transcrever │
│ [Situação ▾] [Buscar…  ] │                                       de novo…]   │
│ ┌──────────────────────┐ │ ┌─Transcrição─┬─Documentos─┬─Propriedades─┐       │
│ │☑│Entrevista│Dur │Sit │ │ │ ╔═ banner (slot único, se preciso) ═══╗ │       │
│ │ │Maria     │1:42│Rev │ │ │ ╚═════════════════════════════════════╝ │       │
│ │ │Campo 02  │0:58│43% │ │ │ ┌ vídeo (se houver) / onda sonora ───┐  │       │
│ │▓│Campo 03  │2:10│Pen │ │ │ │ [−][+][Ver ▾]                      │  │       │
│ └──────────────────────┘ │ │ └────────────────────────────────────┘  │       │
│ [Buscar palavras…]       │ │ [▶][−5s][+5s][Repetir] ──●── 00:12/1:42 │       │
│                          │ │            [1.0x ▾][🔊▾] ☑ Acompanhar   │       │
│ (drop zone / empty       │ │ Falantes: (● Maria)(● Entrevistador)    │       │
│  state quando vazio)     │ │   (● Voz não identificada)              │       │
│                          │ │   [Dar nome às vozes…] [⋯]              │       │
│                          │ │ ┌ blocos: Tempo│Falante│Texto│Marcas ─┐ │       │
│                          │ │ └─────────────────────────────────────┘ │       │
│                          │ │ ┌ editor do bloco selecionado ────────┐ │       │
│                          │ └─└─────────────────────────────────────┘─┘       │
├──────────────────────────┴───────────────────────────────────────────────────┤
│ Transcrevendo Campo 02… 43% ▓▓▓░ [Cancelar] · Fila (2) │ Salvo às 14:02 │    │
│                                                        │  Motor: NVIDIA │    │
└──────────────────────────────────────────────────────────────────────────────┘
```

Painel direito com entrevista selecionada e NÃO aberta: empty-state
dirigido — "Esta entrevista ainda não foi transcrita [▶ Transcrever
esta entrevista]" ou "Transcrição pronta [Abrir para revisar]".

---

## 3. Menus e mapa de comandos

**6 menus, ~49 itens, zero duplicação interna.** Menu = catálogo
completo; o lugar primário de cada comando é sempre contextual.

```
Projeto                      Editar                     Entrevista
├ Novo projeto…              ├ Desfazer         Ctrl+Z  ├ Abrir              Enter
├ Abrir projeto…             ├ Refazer          Ctrl+Y  ├ Salvar transcrição Ctrl+S
├ Projetos recentes ▸        └ Buscar nesta     Ctrl+F  ├ Fechar
├ Adicionar mídia ▸            transcrição              ├ Transcrever
├ Recarregar lista     F5                               ├ Transcrever novamente…
├ Exportar…     Ctrl+Sh+S    Analisar                   ├ Dar nome às vozes…
├ Abrir pasta do projeto     ├ Buscar palavras… C+Sh+F  ├ Refazer separação de
└ Sair                       ├ ✨ Perguntar às          │   falantes…
                             │    entrevistas…          ├ Propriedades…
Ferramentas                  ├ ✨ Resumir entrevista…   ├ Renomear rótulo…   F2
├ Fila de atividades         ├ ✨ Glossário de nomes    ├ Mover p/ cima  C+Alt+↑
├ Verificar exportações      │    do projeto            ├ Mover p/ baixo C+Alt+↓
├ Configurar transcrição…    └ ✨ Revisar grafias       ├ Apagar transcrição…
├ Gerenciar modelos…              de nomes…             ├ Enviar p/ a Lixeira… Del
├ Instalar aceleração                                   └ Desfazer exclusão
│   NVIDIA…                  Ajuda: Guia rápido ·
└ ☑ Perguntar de quem é      Documentação · Atualizações ·
    cada voz ao abrir        Reparar · Sobre o Transcritório
```

Consolidações-chave (hoje → novo):

| Hoje | Novo | Contrato |
|---|---|---|
| Exportar… / Exportar este arquivo… / Exportar selecionados… | **Exportar…** (um comando) | Escopo escolhido DENTRO do diálogo |
| "Melhorar falantes deste arquivo" | **Refazer separação de falantes…** | "Recria a transcrição do zero. Suas edições serão descartadas — guardamos uma cópia em Documentos › Versões anteriores." Confirma com o NOME |
| "Reprocessar falantes" (lote) | **Banner de oferta na lista** ("N entrevistas sem separação — [Separar agora]") | Só toca quem não tem separação/edições; aparece quando o recurso é instalado depois |
| "Atualizar transcricao editavel" | **Removido da UI** — remontagem automática | Reparo excepcional vive em Documentos › Versões anteriores |
| "Identificar vozes (De quem é esta voz?)…" | **Dar nome às vozes…** | Faixa de falantes + chips |
| "Limpar transcricao gerada…" | **Apagar transcrição… (a gravação fica)** | Confirma listando nomes |
| SPEAKER_UNKNOWN | **Voz não identificada** (chip cinza) | |
| Créditos + Sobre; Recarregar + Atualizar biblioteca; 3 órfãs | Fundidos/removidos | |

---

## 4. Aba "Documentos" — a casa dos resultados

O pesquisador vê nomes de coisas, nunca pastas numeradas. Duas seções:

- **Desta entrevista**: Transcrição final (Word/texto/legendas),
  ✨ Resumo com temas, Versões anteriores (backups datados com
  "Restaurar…"). Cada linha tem 3 estados: **existe** (data + Abrir +
  "Mostrar na pasta"), **não existe** ("ainda não gerado" + botão
  Gerar/Exportar ali mesmo), **gerando** (barra fina + Cancelar).
- **Do projeto**: ✨ Glossário de nomes (com "Revisar grafias…"),
  Relatório de verificação.
- Rodapé: "Tudo isso fica na pasta Resultados do projeto.
  [Mostrar na pasta Resultados]".
- Fim de job de AI: a linha vira "existe" + banner de sucesso na aba
  ativa ("Resumo pronto. [Abrir]"); ponto "●" no título da aba
  enquanto houver documento novo não aberto. Fim das pontes-QMessageBox.

---

## 5. Sistema visual (`ui_tokens.py`)

12 tokens de cor (tema escuro único):

| Token | Hex | Uso |
|---|---|---|
| BG_BASE | `#1b1e23` | fundo da janela |
| BG_RAISED | `#23272e` | painéis, tabelas |
| BG_OVERLAY | `#2b3038` | popovers, hover |
| BORDER | `#3a4048` | bordas |
| TEXT | `#e6e8eb` | texto |
| TEXT_MUTED | `#9aa0a8` | secundário |
| ACCENT | `#44d7b6` | ação primária (teal já em uso) |
| INFO | `#4dabf7` | banners informativos |
| WARN | `#ffa94d` | avisos |
| DANGER | `#e5534b` | destrutivas |
| SUCCESS | `#2ea043` | pronto/salvo/acelerado |
| AI | `#b197fc` | tudo que é ✨ AI assistiva |

Banner por fórmula única (fundo rgba(token, .14), borda rgba(token,
.45)). Espaçamento 4/8/12/16/24. Fontes: 11 caption / 13 body /
16 título / 18 hero. Fábricas: primary/ghost/danger button,
banner(kind, texto, ações), chip, empty_state.

---

## 6. Guia de estilo verbal — 10 regras

1. Acentuação sempre correta, sem exceção.
2. "AI", nunca "IA"; ✨ + selo "AI local — nada sai do seu computador"
   só na AI assistiva (o motor de transcrição não leva ✨).
3. "Entrevista" = a unidade de trabalho; "arquivo" só para o objeto no
   disco.
4. Verbo no infinitivo em botão e menu.
5. Reticências "…" (um caractere) só quando abre janela que pede
   decisão.
6. Contratos padronizados: "Mantém suas edições." ou "Descarta suas
   edições — guardamos uma cópia em Documentos › Versões anteriores."
7. Destrutiva nomeia o alvo ("Enviar 'Entrevista com Maria' para a
   Lixeira?") — nunca número, nunca escopo de ☑.
8. Jargão com tradução fixa: QC→verificação; diarização→separação de
   falantes; SPEAKER_UNKNOWN→Voz não identificada; ASR→motor; pastas
   05/06/07→nome do documento/pasta Resultados.
9. Progresso no gerúndio, estado no particípio; barra nunca em 0%.
10. Tooltip ≤3 linhas (o que faz / efeito-contrato / atalho);
    tratamento "você"; unidades espaçadas ("1 h 42 min", "3 GB").

---

## 7. Primeiro contato (4 passos, ~3 decisões)

0. **Instalar e abrir**: preparo de modelos DENTRO da janela principal
   (progresso nomeado), não em diálogo-pedágio; separação de falantes
   instalada quando o computador comporta (instalado ⇒ aplicado).
1. **Criar projeto** (nome + pasta pré-preenchida): empty-state com
   modelo mental ("pasta única; suas gravações não são copiadas nem
   alteradas").
2. **Adicionar e transcrever**: arrastar → Transcrever grita → um
   clique transcreve as pendentes (sem entender ☑ antes).
3. **Primeira revisão**: linha "Pronta para revisar" → CTA no painel →
   "De quem é esta voz?" (adiável) → Salvar grita → "Salvo às…".
4. **Primeiro resultado**: convite a Exportar; o documento aparece na
   aba Documentos — o lugar de tudo dali em diante.

---

## 8. Regras de migração (resumo; detalhe no plano)

Extrair SÓ `ui_tokens.py` / `ui_shell.py` / `ui_docs_panel.py` /
`ui_banners.py`. Aliasing de atributos (nomes preservados para o
worker e update_action_states). Reparent, nunca recriar
interview_table/text_edit. Menubar novo em UM commit atômico (código +
smokes + rules + CLAUDE.md). Nenhum esquema de dados muda. Rede nova:
smoke_nav_ui (ação órfã/colisão de atalhos/matriz de estados), guard
de strings, catraca de cores (38→0), galeria de screenshots offscreen.
Etapas R0→R4 (~30-40 commits, 5 wheels), R3 fora de janela de lote.

---

## 9. Pontos que exigem o SEU aceite (sem volta barata)

1. Os **6 menus** e seus nomes (memória muscular quebra uma vez).
2. O **guia verbal** (reticências, AI, entrevista/arquivo) — decide
   antes da varredura de mil strings.
3. As **3 abas** do painel direito (Transcrição/Documentos/
   Propriedades) e a lista de 5 colunas.
4. O destino do **trio de falantes** (2 comandos + automatismo; fim do
   "Atualizar transcrição editável" na UI).
5. A **paleta de 12 tokens** (seção 5).
