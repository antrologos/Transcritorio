"""Toy: codificacao e exportacao (Parte B, 2026-09-03) — puro + disco temporario.

Codebook (criar sem repetir, renomear, sementes do contexto), codings (um
trecho com varios codigos, sem duplicata, remover), tema -> codigo, texto
plano por entrevista com offsets, .qualilab com a invariante
quote == content[span_start:span_end] (tambem com emoji fora do BMP),
CSV e persistencia em 08_codificacao.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import coding  # noqa: E402

# --- codebook ---
codes: list[dict] = []
c1 = coding.add_code(codes, "  Pagamento   atrasado ", "Atrasos na remuneração")
c1b = coding.add_code(codes, "pagamento atrasado")
assert c1 is c1b and len(codes) == 1 and c1["name"] == "Pagamento atrasado" and c1["description"] == "Atrasos na remuneração"
c2 = coding.add_code(codes, "Treinamento")
assert c2["hue_deg"] != c1["hue_deg"] and c2["position"] == 1 and c2["id"].startswith("code_")
assert coding.rename_code(codes, c2["id"], "Treinamento dos recenseadores", "Duração") is True
assert codes[1]["name"] == "Treinamento dos recenseadores" and codes[1]["description"] == "Duração"
assert coding.rename_code(codes, "nao_existe", "x") is False
assert coding.find_code(codes, "TREINAMENTO DOS RECENSEADORES") is c2
assert {coding.hue_for(i) for i in range(12)}.__len__() == 12
print("PASS: codebook")

# --- sementes do contexto_pesquisa.md ---
md = """# Contexto
## Codebook inicial
- (uma linha por codigo: `nome: descricao`)
- recepcao: como as pessoas receberam o recenseador
- `remuneracao`: pagamento e atrasos
- semdescricao
## Outra secao
- nao entra
"""
assert coding.parse_codebook_seed(md) == [("recepcao", "como as pessoas receberam o recenseador"),
                                          ("remuneracao", "pagamento e atrasos"), ("semdescricao", "")]
assert coding.parse_codebook_seed("") == []
print("PASS: parse_codebook_seed")

# --- codings ---
codings: list[dict] = []
a = coding.add_coding(codings, "E1", 3, 4, c1["id"], quote="  o pagamento   atrasou ", origem="tema")
assert a is not None and a["quote"] == "o pagamento atrasou" and a["origem"] == "tema" and a["layer"] == "individual"
assert coding.add_coding(codings, "E1", 3, 4, c1["id"]) is None, "duplicata"
b = coding.add_coding(codings, "E1", 3, 4, c2["id"])          # segundo codigo no MESMO trecho
assert b is not None and len(codings) == 2
coding.add_coding(codings, "E2", 0, 0, c1["id"])
assert coding.codes_at(codings, "E1", 3, 4) == [c1["id"], c2["id"]]        # dois codigos na MESMA faixa
assert coding.codes_at(codings, "E1", 4, 6) == [] and coding.codes_at(codings, "E1", 3, 3) == []   # faixa exata, nao sobreposicao
assert [c["t_from"] for c in coding.codings_for(codings, "E1")] == [3, 3]
assert coding.remove_coding(codings, b["id"]) is True and len(codings) == 2
assert coding.remove_coding(codings, "nada") is False
print("PASS: codings")

# --- tema -> codigo ---
theme = {"id": "tema_001", "name": "Remuneração", "description": "Atrasos", "passages": [
    {"interview_id": "E1", "t_from": 3, "t_to": 4, "text": "o pagamento atrasou"},
    {"interview_id": "E2", "t_from": 0, "t_to": 0, "text": "sem pagamento"},
    {"interview_id": "E2", "t_from": 5, "t_to": 6, "text": "atraso de meses"},
]}
# As faixas e a citacao vem de FORA: so a janela sabe quem entra na analise.
FAIXAS = {("E2", 5, 6): []}          # trecho so com fala de quem ficou de fora


def ranges_for(p):
    chave = (p["interview_id"], p["t_from"], p["t_to"])
    return FAIXAS.get(chave, [(p["t_from"], p["t_to"])])


def quote_for(iid, t_from, t_to):
    return f"{iid} {t_from}-{t_to}"


r = coding.apply_theme_as_code(codes, codings, theme, ranges_for, quote_for)
code = r["code"]
assert code["name"] == "Remuneração" and code["description"] == "Atrasos"
assert r["novo"] is True and r["criadas"] == 2 and r["ja_tinham"] == 0
assert r["sem_fala"] == 1, "trecho sem ninguem escolhido e CONTADO, nao ignorado em silencio"
assert len(codes) == 3 and len(codings) == 4
assert codings[-1]["quote"] == "E2 0-0", "a citacao gravada e a que veio de fora"

# de novo: nada novo, e o codigo nao e duplicado
r2 = coding.apply_theme_as_code(codes, codings, theme, ranges_for, quote_for)
assert r2["code"] is code and r2["novo"] is False
assert r2["criadas"] == 0 and r2["ja_tinham"] == 2 and len(codings) == 4
assert coding.codes_at(codings, "E1", 3, 4) == [c1["id"], code["id"]]   # dois codigos no mesmo trecho

# a faixa liberada entra na proxima passada
FAIXAS.pop(("E2", 5, 6))
r3 = coding.apply_theme_as_code(codes, codings, theme, ranges_for, quote_for)
assert r3["criadas"] == 1 and r3["sem_fala"] == 0 and len(codings) == 5
print("PASS: apply_theme_as_code")

# --- todos os temas de uma vez ---
tema_b = {"id": "tema_002", "name": "Recepção", "description": "Portas", "passages": [
    {"interview_id": "E1", "t_from": 3, "t_to": 4},      # trecho que ja tem outros codigos
    {"interview_id": "E3", "t_from": 7, "t_to": 8},
]}
# Um tema cujo nome ja esta no codebook (veio do contexto_pesquisa.md ou de
# uma rodada anterior): tem de REAPROVEITAR o codigo, nunca duplicar.
tema_repetido = {"id": "tema_003", "name": "  PAGAMENTO atrasado ", "passages": [
    {"interview_id": "E4", "t_from": 0, "t_to": 0},
]}
antes_codes, antes_codings = len(codes), len(codings)
resumo = coding.apply_themes_as_codes(codes, codings, [theme, tema_b, tema_repetido],
                                      ranges_for, quote_for)
assert resumo["temas"] == 3
assert resumo["criadas"] == 3 and resumo["ja_tinham"] == 3   # o tema ja aplicado nao repete
assert resumo["codigos_novos"] == 1, "«Pagamento atrasado» ja existia no codebook: reaproveita"
assert len(codes) == antes_codes + 1 and len(codings) == antes_codings + 3
assert resumo["nomes"] == ["Remuneração", "Recepção", "Pagamento atrasado"]
# idempotente: rodar de novo nao cria nada
repetido = coding.apply_themes_as_codes(codes, codings, [theme, tema_b, tema_repetido],
                                        ranges_for, quote_for)
assert repetido["criadas"] == 0 and repetido["codigos_novos"] == 0
assert len(codings) == antes_codings + 3
print("PASS: apply_themes_as_codes")

# --- o indice de chaves nao muda o resultado, so o custo ---
c_lento: list[dict] = []
c_rapido: list[dict] = []
indice: set = set()
for i in range(50):
    coding.add_coding(c_lento, "E1", i, i, "code_x")
    coding.add_coding(c_rapido, "E1", i, i, "code_x", known=indice)
assert len(c_lento) == len(c_rapido) == 50
assert coding.add_coding(c_rapido, "E1", 7, 7, "code_x", known=indice) is None, "indice pega a duplicata"
assert indice == coding.coding_keys(c_rapido)
print("PASS: coding_keys / known")

# --- texto plano e offsets ---
turns = [
    {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "human_label": "Entrevistador", "text": "Bom dia,  como foi?"},
    {"start": 4.0, "end": 9.5, "speaker": "SPEAKER_01", "human_label": "Entrevistado", "text": "Foi difícil 😀 no começo."},
    {"start": 9.5, "end": 12.0, "speaker": "SPEAKER_00", "human_label": "", "text": ""},
]
content, spans = coding.render_document(turns)
assert content.startswith("[00:00:00] Entrevistador: Bom dia, como foi?\n\n[00:00:04] Entrevistado: ")
assert "😀" not in content and "�" in content, "fora do BMP vira U+FFFD (offsets JS == Python)"
# o ROTULO tambem: um falante batizado com emoji deslocaria todos os offsets no leitor
rot_content, rot_spans = coding.render_document([
    {"start": 0.0, "human_label": "Maria 😀", "text": "Bom dia."},
    {"start": 4.0, "human_label": "Entrevistado", "text": "Foi difícil."}])
assert "😀" not in rot_content and rot_content.startswith("[00:00:00] Maria �: ")
assert len(rot_content) == len(rot_content.encode("utf-16-le")) // 2, "offsets Python == UTF-16 (JS)"
assert rot_content[rot_spans[1][0]:rot_spans[1][1]] == "Foi difícil."
assert content[spans[0][0]:spans[0][1]] == "Bom dia, como foi?"
assert content[spans[1][0]:spans[1][1]] == "Foi difícil � no começo."
assert spans[2][0] == spans[2][1] and content.endswith("[00:00:09] SPEAKER_00: ")   # sem rotulo humano, o id do falante
assert coding.render_document([{"start": 0, "text": "x"}])[0] == "[00:00:00] ?: x"
assert coding.render_document([]) == ("", [])
print("PASS: render_document")

# --- .qualilab: invariante quote == content[span] ---
codes_x: list[dict] = []
cod_x: list[dict] = []
cx1 = coding.add_code(codes_x, "Dificuldade")
cx2 = coding.add_code(codes_x, "Cordialidade", parent_id=cx1["id"])
coding.add_coding(cod_x, "E1", 1, 1, cx1["id"], quote="Entrevistado: Foi difícil 😀 no começo.")
coding.add_coding(cod_x, "E1", 0, 1, cx2["id"])
coding.add_coding(cod_x, "E1", 0, 9, cx2["id"])          # turno fora da faixa: ignorada
coding.add_coding(cod_x, "E9", 0, 0, cx2["id"])          # entrevista fora do escopo: ignorada
coding.add_coding(cod_x, "E1", 0, 0, "code_fantasma")    # codigo inexistente: ignorada
docs = [{"interview_id": "E1", "name": "Entrevista 1", "turns": turns}]
q = coding.build_qualilab("Projeto X", docs, codes_x, cod_x, author="Rogério")
assert set(q) >= {"_meta", "documents", "categories", "doc_values", "codes", "codings", "memos"}
assert q["_meta"]["name"] == "Projeto X" and q["documents"][0]["name"] == "Entrevista 1"
assert [c["depth"] for c in q["codes"]] == [0, 1] and q["codes"][1]["parent_id"] == cx1["id"]
assert 0 <= q["codes"][0]["hue"] <= 11 and q["codes"][0]["hue_deg"] == cx1["hue_deg"]
assert len(q["codings"]) == 2, [c["id"] for c in q["codings"]]
doc = q["documents"][0]
for cd in q["codings"]:
    assert cd["document_id"] == doc["id"] and cd["quote"] == doc["content"][cd["span_start"]:cd["span_end"]]
    assert cd["author_name"] == "Transcritório" and cd["layer"] == "individual"
assert q["codings"][0]["quote"] == "Foi difícil � no começo."
assert q["codings"][1]["quote"].startswith("Bom dia, como foi?\n\n[00:00:04] Entrevistado: Foi difícil")
json.dumps(q, ensure_ascii=False)   # serializavel
print("PASS: build_qualilab — invariante e filtros")

# --- ancoragem: a transcricao mudou depois da codificacao ---
# o quote guardado vem da passagem do indice (com rotulo em linha, cortado
# em 600 chars) e o span cobre o turno inteiro: ancorado mesmo assim
assert coding.is_anchored("Entrevistado: Foi difícil 😀 no começo.", "Foi difícil � no começo.") is True
assert coding.is_anchored("Foi difícil", "[00:00:04] Entrevistado: Foi difícil no começo.") is True
assert coding.is_anchored("", "qualquer coisa") is True, "coding sem quote nao tem como ser conferida"
assert coding.is_anchored("o pagamento atrasou", "Me conte como foi?") is False
# quote cortado em 600 chars e quote de um PEDACO de turno longo continuam ancorados
longo = "Entrevistado: " + " ".join(f"palavra{i}" for i in range(200))
assert coding.is_anchored(longo[:600], " ".join(f"palavra{i}" for i in range(200))) is True
assert coding.is_anchored("palavra50 palavra51 palavra52 palavra53 palavra54 palavra55 palavra56",
                          " ".join(f"palavra{i}" for i in range(200))) is True
# dois turnos: o rotulo do segundo aparece no meio do texto plano, e o carimbo de tempo some
assert coding.is_anchored("Entrevistador: Bom dia, como foi? Entrevistado: Foi difícil.",
                          "Bom dia, como foi?\n\n[00:00:04] Entrevistado: Foi difícil.") is True
# o falante foi REBATIZADO entre codificar e exportar: continua ancorado
assert coding.is_anchored("SPEAKER_00: Bom dia, como foi? SPEAKER_01: Foi difícil.",
                          "Bom dia, como foi?\n\n[00:00:04] Entrevistado: Foi difícil.") is True
assert coding.is_anchored("Maria Silva: Bom dia, como foi?", "Bom dia, como foi?") is True
# um bloco dividido antes desloca os indices: o span fica valido e aponta para OUTRA fala
turns_editados = [{"start": 0.0, "end": 2.0, "human_label": "Entrevistador", "text": "Bom dia,"},
                  {"start": 2.0, "end": 4.0, "human_label": "Entrevistador", "text": "como foi?"},
                  {"start": 4.0, "end": 9.5, "human_label": "Entrevistado", "text": "Foi difícil no começo."}]
docs_e = [{"interview_id": "E1", "turns": turns_editados}]
placed, lost = coding.plan_codings(docs_e, codes_x, cod_x)
# perdidas: a deslocada (span valido apontando para outra fala) e a de turno fora da faixa;
# a de entrevista fora do escopo e a de codigo inexistente nao contam como perda
assert [c["id"] for c in lost] == [cod_x[0]["id"], cod_x[2]["id"]], [c["id"] for c in lost]
assert len(placed) == 1 and placed[0]["coding"]["id"] == cod_x[1]["id"]
qe = coding.build_qualilab("X", docs_e, codes_x, cod_x)
assert len(qe["codings"]) == 1
assert all(c["quote"] != "como foi?" for c in qe["codings"]), "citacao errada nao pode sair"
assert coding.csv_rows(docs_e, codes_x, cod_x)[1][3] == "Cordialidade"
print("PASS: ancoragem — transcricao mudou depois da codificacao")

# --- entrevistas com codificacao (base do escopo do export) ---
assert coding.coded_interview_ids(cod_x) == ["E1", "E9"]
assert coding.coded_interview_ids([]) == []

# --- CSV ---
rows = coding.csv_rows(docs, codes_x, cod_x)
assert rows[0] == ["entrevista", "inicio", "fim", "codigos", "trecho"]
assert rows[1][:4] == ["E1", "00:00:00", "00:00:09", "Cordialidade"] and rows[2][:4] == ["E1", "00:00:04", "00:00:09", "Dificuldade"]
assert len(rows) == 3   # E9 e o turno fora da faixa nao entram; codigo fantasma nao entra
print("PASS: csv_rows")

# --- persistencia + export em disco ---
from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    config = dict(DEFAULT_CONFIG)
    config["project_root"] = str(root)
    paths = make_paths(config, base_dir=root)
    ensure_directories(paths)
    assert coding.load_codebook(paths) == [] and coding.load_codings(paths) == []
    coding.save_codebook(paths, codes_x)
    coding.save_codings(paths, cod_x)
    assert coding.codebook_path(paths).parent.name == "08_codificacao"
    assert [c["name"] for c in coding.load_codebook(paths)] == ["Dificuldade", "Cordialidade"]
    assert len(coding.load_codings(paths)) == 5
    coding.codings_path(paths).write_text("{corrompido", encoding="utf-8")
    assert coding.load_codings(paths) == []
    coding.save_codings(paths, cod_x)
    out = root / "export" / "proj.qualilab"
    counts = coding.export_qualilab(paths, ["E1"], out, "Projeto X", lambda iid: turns, titles={"E1": "Entrevista 1"})
    # a coding de turno fora da faixa (E1 0-9) conta como perda, nunca some calada
    assert counts == {"documents": 1, "codes": 2, "codings": 2, "desancoradas": 1}
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["documents"][0]["name"] == "Entrevista 1"
    csv_out = root / "export" / "proj.csv"
    assert coding.export_csv(paths, ["E1"], csv_out, lambda iid: turns) == {"linhas": 2, "desancoradas": 1}
    with csv_out.open(encoding="utf-8-sig", newline="") as handle:
        # separador ';': o Excel em pt-BR abre .csv pelo separador de listas do sistema
        assert next(csv.reader(handle, delimiter=";")) == ["entrevista", "inicio", "fim", "codigos", "trecho"]
    # transcricao editada depois da codificacao: a perda e CONTADA, nunca silenciosa
    counts2 = coding.export_qualilab(paths, ["E1"], out, "Projeto X", lambda iid: turns_editados)
    assert counts2["codings"] == 1 and counts2["desancoradas"] == 2
    assert coding.export_csv(paths, ["E1"], csv_out, lambda iid: turns_editados)["desancoradas"] == 2
print("PASS: persistencia e exportacao em disco")

print("PASS: toy_coding")
