# PoC 2.0.b — veredito parcial: GLiNER (tarefa de nomes)

Data: 2026-08-25 | Gabarito: 54 mencoes (D01R 37 + F03R 17), construido
manualmente, com 6 nomes corrompidos pelo ASR marcados (BGA, Realembo,
Vistosa, Ubar, CFET, Cefete).

## Resultados

| Configuracao | P | R | F1 | Garbles |
|---|---|---|---|---|
| GLiNER cru, th=0.4 | 0,05 | 0,96 | 0,09 | 6/6 |
| GLiNER cru, th=0.95 (melhor F1 sem filtro) | 0,17 | 0,69 | 0,27 | 4/6 |
| **GLiNER th=0.5 + filtro maiuscula + stoplist(30)** | **0,47** | **0,96** | **0,63** | **6/6** |

## Conclusoes

1. Recall de 96% INCLUINDO 100% dos garbles do ASR — nenhum LLM deve
   chegar perto disso nos garbles (a confirmar quando a bateria terminar).
2. Falsos positivos do modelo cru = pronomes/genericos ("eu" 318x,
   "voce" 186x, "pessoa", "casa", "rua") — nao e fraqueza semantica, e
   ausencia da camada de regras.
3. Filtro vencedor: manter th=0.5 (recall alto) + exigir maiuscula no
   span + stoplist: eu, voce(s), ele(a)(s), gente, a gente, pessoa(s),
   a pessoa, casa, rua, lugar, bairro, la, domicilio, vizinho(s),
   minha mae, meu pai, censo, supervisor(es), recenseador(es),
   prefeitura, universidade, colegio, escola, igreja, morador(es),
   cidade, estado, pais, regiao, setor, zona.
4. Sobras (~30/entrevista) para a camada-juiz do LLM: interjeicoes
   capitalizadas (Aham, Eita, Beleza, Uhum), referencias relacionais
   (meu avo, um amigo meu — UTEIS para revisao de anonimizacao),
   garbles ambiguos (Celso=Censo?, DMC=?).
5. Confirma o desenho em camadas da fase 2.6/2.2: regex -> lista
   conhecida -> GLiNER(filtrado) -> LLM juiz -> humano.

Scripts da PoC (scratchpad da sessao): poc2_gliner_nomes.py (extracao),
poc2_score_nomes.py (avaliacao). Saida bruta: poc2_out/nomes_gliner.json.

## Onde isto virou codigo (2026-08-27, lote 6a)

- Receita do filtro portada tal qual em `llm_worker._run_entidades`
  (`NER_THRESHOLD = 0.5`, `NER_STOPLIST`, `_ner_keep` exigindo maiuscula).
- Agrupamento de variantes em `glossario.py`: dobra fonetica de PT-BR
  (`c[ei]->s`, `lh`/`nh` como fonemas proprios) + esqueleto de consoantes
  (casa "BGA" com "IBGE") + **trava de frequencia** (so absorve forma que
  seja minoria clara — "Maria"/"Mario" tem similaridade 0,90 e NAO podem
  se fundir). Limiar 0,75 calibrado contra este gabarito.
- Gabarito promovido para `tests/data/gabarito_nomes.json`; a regressao
  em `tests/toy_glossario.py` exige os 6/6 garbles agrupados quando os
  nomes canonicos estao declarados no contexto da pesquisa.
- Tensao conhecida e resolvida por desenho: "censo" esta na stoplist (e
  substantivo comum), entao a forma CORRETA nao e extraida enquanto o
  garble "Celso" sobrevive. E por isso que declarar o nome canonico em
  "## Nomes conhecidos" e o caminho de maior precisao — um nome
  declarado e canonico mesmo sem nunca aparecer certo na transcricao.
