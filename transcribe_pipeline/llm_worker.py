"""Worker de analise local — roda DENTRO do ambiente dedicado (llm-venv).

Executado por caminho de arquivo (nunca importado como parte do app em
producao): o llm-venv nao tem o pacote instalado nem as dependencias do
app. Por isso o nivel de modulo e STDLIB PURA — transformers/torch so sao
importados dentro de main(). As funcoes puras (janelamento, montagem do
markdown) ficam aqui para serem testaveis de qualquer ambiente.

Tarefa v1: sumario com indice tematico (fase 2.1 do plano-programa).
Estrategia janelada (map-reduce) e OBRIGATORIA: medimos que sumario de
contexto integral estoura a VRAM ate no modelo 4B (picos de 12-24 GB).

Progresso: linhas '@PROGRESS {json}' no stdout — mesmo contrato de
utils.PROGRESS_JSON_PREFIX (duplicado aqui de proposito: este arquivo nao
pode importar o pacote).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PROGRESS_PREFIX = "@PROGRESS "
# Resultado tambem pelo stdout (2026-09-03): num llm-venv criado a partir
# do Python da Microsoft Store, o arquivo gravado em %LOCALAPPDATA% cai na
# pasta virtualizada do pacote (Packages\...\LocalCache) e o app nunca o
# ve. A linha @RESULT chega sempre; o arquivo fica como reserva.
RESULT_PREFIX = "@RESULT "


def emit_result(payload: dict) -> None:
    print(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
MAX_WINDOW_CHARS = 9000  # ~2,5k tokens pt: prefill leve na GPU de 8 GB


def emit(progress: int, message: str) -> None:
    print(PROGRESS_PREFIX + json.dumps(
        {"event": "summarize_progress", "progress": int(progress), "message": message},
        ensure_ascii=False), flush=True)


def fmt_time(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def load_turns(payload: dict) -> list[dict]:
    """Aceita review ({"transcript": {"turns": ...}}) e canonical ({"turns": ...})."""
    transcript = payload.get("transcript") or payload
    turns = transcript.get("turns") or []
    return [t for t in turns if str(t.get("text") or "").strip()]


def turn_line(turn: dict) -> str:
    label = turn.get("human_label") or turn.get("speaker") or "?"
    text = " ".join(str(turn.get("text") or "").split())
    return f"[{fmt_time(float(turn.get('start', 0) or 0))}] {label}: {text}"


def build_windows(turns: list[dict], max_chars: int = MAX_WINDOW_CHARS) -> list[str]:
    """Janelas de turnos consecutivos limitadas por tamanho; nunca corta turno."""
    windows: list[str] = []
    current: list[str] = []
    size = 0
    for turn in turns:
        line = turn_line(turn)
        if current and size + len(line) > max_chars:
            windows.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        windows.append("\n".join(current))
    return windows


def extract_json_list(text: str) -> list:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def merge_notes(window_notes: list[list[dict]]) -> str:
    """Notas de todas as janelas em texto compacto para o passo de reducao."""
    lines: list[str] = []
    for notes in window_notes:
        for note in notes:
            if not isinstance(note, dict):
                continue
            tema = str(note.get("tema") or "").strip()
            inicio = str(note.get("inicio") or "").strip()
            resumo = str(note.get("resumo") or "").strip()
            if tema:
                lines.append(f"- [{inicio or '??:??:??'}] {tema}: {resumo}")
    return "\n".join(lines)


def format_trechos(trechos: list[dict]) -> str:
    """Trechos numerados [1..n] para o prompt de pergunta (puro, testavel)."""
    lines = []
    for index, trecho in enumerate(trechos, start=1):
        label = str(trecho.get("label") or "?")
        inicio = str(trecho.get("inicio") or "")
        texto = " ".join(str(trecho.get("text") or "").split())
        lines.append(f"[{index}] ({trecho.get('interview_id')}, {inicio}, {label}) {texto}")
    return "\n".join(lines)


SEM_RESPOSTA = "Isso nao aparece nas entrevistas disponiveis."


def _fold(text: str) -> str:
    """Minusculas sem acentos (o Qwen escreve "não… disponíveis"; a recusa
    canonica e sem acento — comparar de forma insensivel)."""
    import unicodedata as _ud

    return "".join(c for c in _ud.normalize("NFD", str(text).lower()) if _ud.category(c) != "Mn")


def validate_answer(resposta: str, n_trechos: int) -> bool:
    """Resposta valida = cita ao menos um [n] existente OU e a recusa exata.

    Ancoragem por construcao: afirmacao sem citacao nao passa (puro,
    testavel). A recusa e reconhecida com ou sem acentos (2026-09-03)."""
    import re as _re

    if _fold(SEM_RESPOSTA).rstrip(".") in _fold(resposta):
        return True
    cited = {int(m) for m in _re.findall(r"\[(\d+)\]", resposta)}
    return bool(cited) and all(1 <= c <= n_trechos for c in cited)


# --- Visao geral: perguntas sobre o CONJUNTO respondidas pelos resumos ---

def split_resumo_sections(markdown: str) -> dict[str, str]:
    """Secoes '## Resumo', '## Indice tematico', '## Observacoes' de um
    {id}.resumo.md -> {"resumo", "indice", "observacoes"} (puro; aceita
    cabecalhos com ou sem acento e em qualquer caixa)."""
    sections = {"resumo": "", "indice": "", "observacoes": ""}
    current = None
    buf: dict[str, list[str]] = {k: [] for k in sections}
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            title = _fold(stripped[3:])
            if title.startswith("resumo"):
                current = "resumo"
            elif title.startswith("indice"):
                current = "indice"
            elif title.startswith("observac"):
                current = "observacoes"
            else:
                current = None
            continue
        if current is not None:
            buf[current].append(line.rstrip())
    return {k: "\n".join(v).strip() for k, v in buf.items()}


def batch_resumos(resumos: list[dict], max_chars: int = MAX_WINDOW_CHARS) -> list[list[dict]]:
    """Lotes de resumos por tamanho (nunca parte um resumo; puro)."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for item in resumos:
        length = len(str(item.get("resumo") or "")) + len(str(item.get("indice") or "")) + 40
        if current and size + length > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        batches.append(current)
    return batches


def format_resumos(resumos: list[dict]) -> str:
    """Bloco '=== [ID] === resumo / indice' para o prompt (puro)."""
    parts = []
    for item in resumos:
        iid = str(item.get("interview_id") or "?")
        titulo = str(item.get("titulo") or "").strip()
        head = f"=== [{iid}]" + (f" ({titulo})" if titulo and titulo != iid else "") + " ==="
        body = str(item.get("resumo") or "").strip()
        indice = str(item.get("indice") or "").strip()
        if indice:
            body = f"{body}\nTemas: {indice}" if body else f"Temas: {indice}"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)


def cited_interviews(resposta: str, known_ids: list[str]) -> list[str]:
    """Ids de entrevista citados como [ID] na resposta, na ordem, sem
    repeticao, so os conhecidos (puro)."""
    import re as _re

    known = {str(i) for i in known_ids}
    seen: list[str] = []
    for match in _re.findall(r"\[([^\[\]]{1,80})\]", str(resposta or "")):
        candidate = match.strip()
        if candidate in known and candidate not in seen:
            seen.append(candidate)
    return seen


VISAO_MAP_PROMPT = (
    "Voce ajuda um pesquisador a responder uma pergunta sobre um CONJUNTO de "
    "entrevistas. Abaixo estao resumos de algumas entrevistas, cada um identificado "
    "por [ID]. Liste, EM PORTUGUES, o que nesses resumos ajuda a responder a "
    "pergunta: um item por ponto, sempre citando a(s) entrevista(s) no formato [ID]. "
    "Use apenas o que esta nos resumos; se nada ajudar, escreva: nada relevante.\n\n"
    "{contexto}=== RESUMOS ===\n{resumos}\n\n=== PERGUNTA ===\n{pergunta}"
)

VISAO_REDUCE_PROMPT = (
    "Voce recebera NOTAS extraidas dos resumos de varias entrevistas (cada nota cita "
    "entrevistas como [ID]). Escreva, EM PORTUGUES e em ate 300 palavras, a resposta "
    "a pergunta do pesquisador sobre o conjunto: organize por temas ou padroes, diga "
    "o que e comum e o que diverge, e cite as entrevistas [ID] que sustentam cada "
    "afirmacao. Regras: use apenas as notas; nao invente; se as notas nao respondem, "
    f"escreva exatamente: {SEM_RESPOSTA}\n\n"
    "{contexto}=== NOTAS ===\n{notas}\n\n=== PERGUNTA ===\n{pergunta}"
)


PERGUNTA_PROMPT = (
    "Voce responde perguntas de um pesquisador sobre entrevistas transcritas, usando "
    "APENAS os trechos numerados abaixo. Regras OBRIGATORIAS:\n"
    "1. Toda afirmacao deve citar o(s) trecho(s) que a sustentam, no formato [n].\n"
    "2. Nao use nenhum conhecimento externo; nao invente.\n"
    f"3. Se os trechos nao contem a resposta, escreva exatamente: {SEM_RESPOSTA}\n"
    "4. Responda em portugues, direto, em ate 200 palavras.\n\n"
    "{contexto}{glossario}=== TRECHOS ===\n{trechos}\n\n=== PERGUNTA ===\n{pergunta}"
)


MAP_PROMPT = (
    "Voce recebera um TRECHO de uma entrevista academica transcrita (portugues "
    "brasileiro), com timestamps. Liste os temas tratados NESTE trecho.\n"
    'Responda APENAS com JSON: [{{"tema": "2-5 palavras", "inicio": "HH:MM:SS do '
    'primeiro momento do tema", "resumo": "1 frase do que foi dito"}}]. '
    "Use somente o que esta no trecho; nao invente.\n\n{contexto}{glossario}=== TRECHO ===\n{janela}"
)

REDUCE_PROMPT = (
    "Voce recebera as NOTAS TEMATICAS (com timestamps) extraidas de uma entrevista "
    "academica inteira, em ordem. Escreva, EM PORTUGUES, um documento markdown com "
    "EXATAMENTE estas secoes:\n\n"
    "## Resumo\nResumo executivo de 150-250 palavras da entrevista inteira.\n\n"
    "## Indice tematico\nOs temas em ordem de aparicao, agrupando repeticoes: "
    "`- [HH:MM:SS] Tema — sintese curta` (timestamp do primeiro momento).\n\n"
    "## Observacoes\nAte 3 pontos que merecem atencao do pesquisador (tensoes, "
    "contradicoes, temas emergentes{fora_do_roteiro}).\n\n"
    "Baseie-se apenas nas notas; nao invente conteudo.\n\n{contexto}{glossario}=== NOTAS ===\n{notas}"
)


def _make_asker(model_repo: str):
    """Carrega o modelo 4-bit e devolve ask(prompt, max_new_tokens)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tokenizer = AutoTokenizer.from_pretrained(model_repo)
    model = AutoModelForCausalLM.from_pretrained(
        model_repo,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_quant_type="nf4"),
        device_map="cuda:0",
    )
    model.eval()

    def ask(prompt: str, max_new_tokens: int) -> str:
        try:
            text = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt}], tokenize=False,
                add_generation_prompt=True, enable_thinking=False)
        except TypeError:
            text = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to("cuda")
        # Receita do fabricante para o modo sem "thinking" (model card do
        # Qwen3.5): amostragem leve em vez de greedy puro, que tende a
        # repetir em saidas longas. Semente fixa = resposta reprodutivel.
        torch.manual_seed(0)
        with torch.no_grad():
            output = model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=True,
                temperature=0.7, top_p=0.8, top_k=20, repetition_penalty=1.05)
        return tokenizer.decode(output[0][inputs.input_ids.shape[1]:], skip_special_tokens=True).strip()

    return ask


# Receita calibrada na PoC 2.0.b (2026-08-25): th=0,5 + exigir maiuscula
# no span + stoplist -> precisao 0,47, recall 0,96 e 6/6 dos nomes
# corrompidos pelo ASR. O modelo cru erra em pronomes/genericos, nao em
# semantica; por isso a camada de regras vive aqui e nao no modelo.
NER_LABELS = ("person", "location", "organization")
NER_TIPOS = {"person": "pessoa", "location": "lugar", "organization": "instituicao"}
NER_THRESHOLD = 0.5
NER_STOPLIST = frozenset([
    # --- da PoC 2.0.b (medidos como falsos positivos) ---
    "eu", "voce", "voces", "ele", "ela", "eles", "elas",
    "gente", "a gente", "pessoa", "pessoas", "a pessoa",
    "casa", "rua", "lugar", "bairro", "la", "domicilio",
    "vizinho", "vizinhos", "minha mae", "meu pai", "censo",
    "supervisor", "supervisores", "recenseador", "recenseadores",
    "prefeitura", "universidade", "colegio", "escola", "igreja",
    "morador", "moradores", "cidade", "estado", "pais",
    "regiao", "setor", "zona",
    # --- E2E de 2026-08-28: demonstrativos e genericos capitalizados no
    # inicio da fala viravam "nomes" e, por serem frequentes, chegavam a
    # engolir siglas reais ("Isso" absorveu INSS). A comparacao e do span
    # INTEIRO, entao nomes compostos legitimos ("Bolsa Familia", "Arena
    # Park") nao sao afetados.
    "isso", "isto", "esse", "essa", "esses", "essas", "aquilo",
    "aquele", "aquela", "aqui", "ali", "ai", "entao", "tipo",
    "coisa", "coisas", "show", "familia", "obrigado", "obrigada",
    "oi", "ola", "ah", "ahn", "eita", "uhum", "aham", "beleza",
    "ne", "ta", "tudo", "nada", "sim", "nao", "bom", "bem",
    "hoje", "ontem", "amanha", "agora", "depois", "antes",
    "sempre", "nunca", "muito", "pouco", "mais", "menos",
    "trabalho", "servico", "dinheiro", "salario", "tempo",
    "ano", "mes", "dia", "semana", "hora", "vez", "vezes",
    "forma", "parte", "caso", "exemplo", "verdade", "questao",
    "problema", "pergunta", "resposta", "nome", "numero", "area",
    "equipe", "base", "sistema", "aplicativo", "celular",
    "telefone", "internet", "computador", "predio", "predios",
    "apartamento", "condominio", "familia toda", "senhor", "senhora",
])


def _ner_keep(text: str) -> bool:
    """Filtro de regras da PoC: maiuscula inicial e fora da stoplist (puro)."""
    import unicodedata as _ud

    cleaned = " ".join(str(text or "").split())
    if not cleaned or not cleaned[0].isupper():
        return False
    folded = "".join(
        c for c in _ud.normalize("NFD", cleaned.lower()) if _ud.category(c) != "Mn")
    return folded not in NER_STOPLIST


def _run_entidades(args) -> int:
    """Varre as transcricoes com o GLiNER e devolve as mencoes filtradas."""
    from gliner import GLiNER  # so existe no llm-venv

    alvos = json.loads(Path(args.alvos_file).read_text(encoding="utf-8"))
    emit(5, "Carregando o GLiNER, nosso modelo de AI local para nomes...")
    model = GLiNER.from_pretrained(args.model_repo)
    mencoes: list[dict] = []
    for index, alvo in enumerate(alvos, start=1):
        interview_id = str(alvo.get("interview_id") or "")
        emit(5 + int(90 * index / max(1, len(alvos))),
             f"O GLiNER esta procurando nomes em {interview_id} ({index}/{len(alvos)})...")
        try:
            payload = json.loads(Path(alvo["path"]).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - um arquivo nao derruba o lote
            print(f"Falha ao ler {interview_id}: {exc}")
            continue
        for turn in load_turns(payload):
            texto = " ".join(str(turn.get("text") or "").split())
            if not texto:
                continue
            for ent in model.predict_entities(texto, list(NER_LABELS), threshold=NER_THRESHOLD):
                if not _ner_keep(ent.get("text", "")):
                    continue
                mencoes.append({
                    "tipo": NER_TIPOS.get(ent.get("label"), str(ent.get("label"))),
                    "texto": " ".join(str(ent.get("text")).split()),
                    "interview_id": interview_id,
                    "turn_id": str(turn.get("id") or ""),
                    "start": float(turn.get("start", 0) or 0),
                    "score": round(float(ent.get("score", 0) or 0), 3),
                    "trecho": texto[:200],
                })
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"mencoes": mencoes}, ensure_ascii=False), encoding="utf-8")
    emit_result({"mencoes": mencoes})
    emit(100, f"{len(mencoes)} mencoes de nomes encontradas.")
    return 0


def _load_glossary_block(path_text: str) -> str:
    """Bloco de glossario ja formatado pelo app; "" quando ausente."""
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.exists():
        return ""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 - glossario e opcional
        return ""
    return f"{raw}\n\n" if raw else ""


def _run_perguntar(args) -> int:
    """Resposta ancorada nos trechos recuperados, com citacoes [n]."""
    trechos = json.loads(Path(args.trechos_file).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not trechos:
        payload = {"resposta": SEM_RESPOSTA, "valida": True}
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        emit_result(payload)
        emit(100, "Sem trechos relevantes.")
        return 0
    contexto = ""
    if args.context_file:
        context_path = Path(args.context_file)
        if context_path.exists():
            raw = context_path.read_text(encoding="utf-8").strip()
            if raw:
                contexto = f"=== CONTEXTO DA PESQUISA ===\n{raw[:4000]}\n\n"
    glossario = _load_glossary_block(args.glossario_file)
    emit(15, "Carregando o Qwen 3.5, nosso modelo de AI local...")
    ask = _make_asker(args.model_repo)
    emit(55, "Escrevendo a resposta com base nos trechos...")
    resposta = ask(
        PERGUNTA_PROMPT.format(
            contexto=contexto, glossario=glossario,
            trechos=format_trechos(trechos), pergunta=args.question),
        max_new_tokens=600,
    )
    valida = validate_answer(resposta, len(trechos))
    if not valida:
        # Ancoragem por construcao: resposta sem citacao nao e entregue
        # como resposta — vira recusa honesta.
        resposta = SEM_RESPOSTA
        valida = True
    payload = {"resposta": resposta, "valida": valida}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    emit_result(payload)
    emit(100, "Resposta pronta.")
    return 0


def _run_visao_geral(args) -> int:
    """Pergunta sobre o CONJUNTO: map (lotes de resumos -> notas com [ID])
    e reduce (sintese citando entrevistas). Sem resumos = recusa."""
    resumos = json.loads(Path(args.resumos_file).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    known = [str(r.get("interview_id") or "") for r in resumos]
    if not resumos:
        payload = {"resposta": SEM_RESPOSTA, "citadas": [], "valida": True}
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        emit_result(payload)
        emit(100, "Sem resumos no escopo.")
        return 0
    contexto = ""
    if args.context_file:
        context_path = Path(args.context_file)
        if context_path.exists():
            raw = context_path.read_text(encoding="utf-8").strip()
            if raw:
                contexto = f"=== CONTEXTO DA PESQUISA ===\n{raw[:4000]}\n\n"
    emit(10, "Carregando o Qwen 3.5, nosso modelo de AI local...")
    ask = _make_asker(args.model_repo)
    batches = batch_resumos(resumos)
    notas: list[str] = []
    for index, batch in enumerate(batches, start=1):
        emit(10 + int(70 * index / max(1, len(batches))),
             f"O Qwen esta lendo os resumos ({index}/{len(batches)} lotes, "
             f"{sum(len(b) for b in batches[:index])} de {len(resumos)} entrevistas)...")
        answer = ask(VISAO_MAP_PROMPT.format(
            contexto=contexto, resumos=format_resumos(batch), pergunta=args.question),
            max_new_tokens=700)
        if answer and _fold("nada relevante") not in _fold(answer)[:40]:
            notas.append(answer.strip())
    if not notas:
        resposta = SEM_RESPOSTA
    else:
        emit(85, "Escrevendo a visão geral...")
        resposta = ask(VISAO_REDUCE_PROMPT.format(
            contexto=contexto, notas="\n\n".join(notas), pergunta=args.question),
            max_new_tokens=900)
    citadas = cited_interviews(resposta, known)
    valida = bool(citadas) or _fold(SEM_RESPOSTA) in _fold(resposta)
    if not valida:
        resposta = SEM_RESPOSTA
    payload = {"resposta": resposta, "citadas": citadas, "valida": True, "lotes": len(batches)}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    emit_result(payload)
    emit(100, "Visão geral pronta.")
    return 0


NOMEAR_TEMAS_PROMPT = (
    "Voce ajuda um pesquisador a nomear TEMAS encontrados em entrevistas transcritas. "
    "Para cada tema abaixo (identificado por id), com seus termos caracteristicos e "
    "algumas passagens centrais, escreva um NOME curto (2 a 6 palavras, em portugues, "
    "sem aspas) e uma DESCRICAO de uma frase do que as passagens tem em comum. "
    "Nao invente: baseie-se so nas passagens.\n"
    'Responda APENAS com JSON: [{{"id": "...", "nome": "...", "descricao": "..."}}, ...]\n\n'
    "{contexto}=== TEMAS ===\n{temas}"
)


def format_temas(batch: list[dict]) -> str:
    """Bloco de temas para o prompt de nomeacao (puro)."""
    parts = []
    for item in batch:
        termos = ", ".join(item.get("terms") or [])
        passagens = "\n".join(f"  - {p}" for p in (item.get("passages") or []))
        parts.append(f"[{item.get('id')}] termos: {termos}\n{passagens}")
    return "\n\n".join(parts)


def _run_nomear_temas(args) -> int:
    """Nomes e descricoes para lotes de temas (JSON por lote; robusto a lixo)."""
    batches = json.loads(Path(args.temas_file).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    contexto = ""
    if args.context_file:
        context_path = Path(args.context_file)
        if context_path.exists():
            raw = context_path.read_text(encoding="utf-8").strip()
            if raw:
                contexto = f"=== CONTEXTO DA PESQUISA ===\n{raw[:3000]}\n\n"
    emit(10, "Carregando o Qwen 3.5, nosso modelo de AI local...")
    ask = _make_asker(args.model_repo)
    nomes: list[dict] = []
    for index, batch in enumerate(batches, start=1):
        emit(10 + int(85 * index / max(1, len(batches))),
             f"O Qwen esta dando nome aos temas ({index}/{len(batches)} lotes)...")
        answer = ask(NOMEAR_TEMAS_PROMPT.format(contexto=contexto, temas=format_temas(batch)),
                     max_new_tokens=600)
        for item in extract_json_list(answer):
            if isinstance(item, dict) and item.get("id") and item.get("nome"):
                nomes.append({"id": str(item["id"]), "nome": str(item["nome"]),
                              "descricao": str(item.get("descricao") or "")})
    payload = {"nomes": nomes}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    emit_result(payload)
    emit(100, f"{len(nomes)} temas nomeados.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True,
                        choices=["sumario", "perguntar", "entidades", "visao_geral", "nomear_temas"])
    parser.add_argument("--temas-file", default="")
    parser.add_argument("--question", default="")
    parser.add_argument("--trechos-file", default="")
    parser.add_argument("--resumos-file", default="")
    parser.add_argument("--alvos-file", default="")
    parser.add_argument("--review", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--notes-out", default="")
    parser.add_argument("--model-repo", required=True)
    parser.add_argument("--context-file", default="")
    parser.add_argument("--glossario-file", default="")
    parser.add_argument("--hf-cache", default="")
    args = parser.parse_args()

    if args.hf_cache:
        # Cache do app; offline: o modelo ja esta pinado e baixado.
        os.environ["HF_HUB_CACHE"] = args.hf_cache
        os.environ["HF_HOME"] = str(Path(args.hf_cache).parent)
        os.environ["HF_HUB_OFFLINE"] = "1"

    if args.task == "entidades":
        return _run_entidades(args)
    if args.task == "perguntar":
        return _run_perguntar(args)
    if args.task == "visao_geral":
        return _run_visao_geral(args)
    if args.task == "nomear_temas":
        return _run_nomear_temas(args)

    if not args.review:
        print("--review e obrigatorio para a tarefa sumario.")
        return 1
    payload = json.loads(Path(args.review).read_text(encoding="utf-8"))
    turns = load_turns(payload)
    if not turns:
        print("Transcricao vazia; nada a resumir.")
        return 1
    windows = build_windows(turns)

    contexto = ""
    fora_do_roteiro = ""
    if args.context_file:
        context_path = Path(args.context_file)
        if context_path.exists():
            raw = context_path.read_text(encoding="utf-8").strip()
            if raw:
                contexto = f"=== CONTEXTO DA PESQUISA ===\n{raw[:6000]}\n\n"
                fora_do_roteiro = " fora do roteiro"

    glossario = _load_glossary_block(args.glossario_file)
    emit(5, "Carregando o Qwen 3.5, nosso modelo de AI local...")
    ask = _make_asker(args.model_repo)

    window_notes: list[list[dict]] = []
    for index, window in enumerate(windows, start=1):
        emit(5 + int(75 * index / max(1, len(windows))),
             f"O Qwen esta lendo a entrevista e anotando os temas ({index}/{len(windows)})...")
        answer = ask(
            MAP_PROMPT.format(contexto=contexto, glossario=glossario, janela=window),
            max_new_tokens=500)
        window_notes.append(extract_json_list(answer))

    # Notas de tema por janela persistidas (2026-09-03): materia-prima da
    # funcao de temas e sementes de nomes; antes eram descartadas apos o
    # reduce. Opcional; falha ao gravar nunca derruba o resumo.
    if args.notes_out:
        try:
            notes_path = Path(args.notes_out)
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            flat = [dict(n, janela=w) for w, notes in enumerate(window_notes)
                    for n in notes if isinstance(n, dict)]
            notes_path.write_text(json.dumps({"notas": flat}, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"Nao foi possivel guardar as notas de tema: {exc}")

    emit(85, "Montando o resumo e o indice tematico...")
    notas = merge_notes(window_notes)
    if not notas.strip():
        print("O modelo nao extraiu notas; sumario abortado.")
        return 1
    final_md = ask(
        REDUCE_PROMPT.format(contexto=contexto, glossario=glossario, notas=notas,
                             fora_do_roteiro=fora_do_roteiro),
        max_new_tokens=1400,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(final_md + "\n", encoding="utf-8")
    emit(100, "Resumo pronto.")
    print(f"OK {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
