"""Toy test: glossario de nomes (lote 6a) — nucleo puro, sem GLiNER.

Regressao contra o gabarito da PoC 2.0.b (54 mencoes reais, 6 nomes
corrompidos pelo ASR conhecidos). Roda no CI minimo: nada de torch,
transformers ou gliner.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import glossario as gl

# --- normalize_key / phonetic_key / consonant_skeleton ---
assert gl.normalize_key("  Viçosa!  ") == "vicosa"
assert gl.normalize_key("CEFET-RJ") == "cefetrj"
assert gl.normalize_key("") == ""
# c antes de e/i vira s: "Celso" e "Censo" ficam a uma letra
assert gl.phonetic_key("Celso") == "selso"
assert gl.phonetic_key("Censo") == "senso"
# lh/nh sao fonemas proprios — nao podem virar l/n
assert gl.phonetic_key("Penha") != gl.phonetic_key("Pena")
assert gl.phonetic_key("Rocinha") != gl.phonetic_key("Rocina")
assert gl.consonant_skeleton("BGA") == gl.consonant_skeleton("IBGE") == "bg"
print("PASS: normalizacao e fonetica")

# --- similarity: garbles altos, distincoes reais preservadas ---
assert gl.similarity("Celso", "Censo") >= gl.SIMILARITY_THRESHOLD
assert gl.similarity("BGA", "IBGE") >= 0.85          # sigla mutilada (esqueleto)
assert gl.similarity("Vistosa", "Viçosa") >= gl.SIMILARITY_THRESHOLD
assert gl.similarity("Cefete", "CEFET") >= gl.SIMILARITY_THRESHOLD
assert gl.similarity("Bangu", "Brasil") < 0.5        # nomes sem relacao
assert gl.similarity("Ana", "Ono") < 0.85            # esqueleto curto nao vale
assert gl.similarity("IBGE", "ibge") == 1.0          # caixa nao conta
assert gl.similarity("", "IBGE") == 0.0
print("PASS: similarity")

# --- aggregate_mentions: caixa junta, forma exibida = mais frequente ---
formas = gl.aggregate_mentions([
    {"texto": "IBGE", "tipo": "instituicao", "interview_id": "A", "turn_id": "t1"},
    {"texto": "ibge", "tipo": "instituicao", "interview_id": "A", "turn_id": "t2"},
    {"texto": "IBGE", "tipo": "instituicao", "interview_id": "B", "turn_id": "t3"},
    {"texto": "ok", "tipo": "pessoa", "interview_id": "A", "turn_id": "t4"},  # curto: fora
])
assert len(formas) == 1 and formas[0]["texto"] == "IBGE" and formas[0]["total"] == 3
print("PASS: aggregate_mentions")

# --- group_variants: trava de frequencia protege nomes legitimos ---
def mentions(pairs):
    return [{"texto": t, "tipo": "pessoa", "interview_id": "A", "turn_id": f"t{i}"}
            for t, n in pairs for i in range(n)]

# Maria e Mario tem similaridade 0,90 mas ambos sao frequentes -> separados
grupos = gl.group_variants(gl.aggregate_mentions(mentions([("Maria", 10), ("Mario", 8)])))
assert len(grupos) == 2, [g["canonico"] for g in grupos]
# a mesma similaridade COM minoria clara vira variante (garble tipico)
grupos = gl.group_variants(gl.aggregate_mentions(mentions([("Maria", 20), ("Mario", 1)])))
assert len(grupos) == 1 and grupos[0]["canonico"] == "Maria"
# nome declarado e canonico mesmo sem aparecer, e dispensa a trava
grupos = gl.group_variants(
    gl.aggregate_mentions(mentions([("Realembo", 9)])), known=["Realengo"])
assert len(grupos) == 1 and grupos[0]["canonico"] == "Realengo"
assert grupos[0]["conhecido"] is True
# dois nomes declarados nunca se fundem entre si
grupos = gl.group_variants(
    gl.aggregate_mentions(mentions([("Maria", 5), ("Mario", 1)])), known=["Maria", "Mario"])
assert len({g["canonico"] for g in grupos}) == 2
print("PASS: group_variants")

# --- REGRESSAO com o gabarito real da PoC ---
data = json.loads(
    (Path(__file__).parent / "data" / "gabarito_nomes.json").read_text(encoding="utf-8"))
mencoes, garbles = data["mencoes"], data["garbles_conhecidos"]
assert len(mencoes) == 54 and len(garbles) == 6

def variantes_encontradas(glossario):
    return {v["texto"].lower()
            for e in glossario["entradas"] for v in e["variantes"]}

# sem ajuda humana: os garbles que tem a forma correta no proprio acervo
sem_ajuda = gl.build_glossary(mencoes, known=[])
achados = variantes_encontradas(sem_ajuda)
assert {"bga", "vistosa", "cefete"} <= achados, achados
# com os nomes canonicos declarados: TODOS os 6 garbles viram variantes
com_ajuda = gl.build_glossary(
    mencoes, known=["IBGE", "CEFET", "Realengo", "Viçosa", "Ubá", "Bangu"])
achados = variantes_encontradas(com_ajuda)
faltando = [g for g in garbles if g.lower() not in achados]
assert not faltando, f"garbles nao agrupados: {faltando}"
# e nada absurdo foi fundido: nomes sem relacao seguem separados
canonicos = {e["canonico"] for e in com_ajuda["entradas"]}
assert {"Caio", "Gil", "Jean", "Carlos"} <= canonicos, sorted(canonicos)
print(f"PASS: regressao do gabarito (6/6 garbles, {len(com_ajuda['entradas'])} nomes)")

# --- format_glossary_prompt: so entradas COM variante, com teto ---
bloco = gl.format_glossary_prompt(com_ajuda)
assert "GLOSSARIO DE NOMES" in bloco and "IBGE" in bloco and "BGA" in bloco
assert "Caio" not in bloco                      # sem variante: nao ocupa prompt
assert gl.format_glossary_prompt({"entradas": []}) == ""
curto = gl.format_glossary_prompt(com_ajuda, max_chars=40)
assert len(curto) < len(bloco)
print("PASS: format_glossary_prompt")

# --- relatorio markdown ---
report = gl.format_glossary_report(com_ajuda, "projeto_teste")
assert report.startswith("# Glossario de nomes")
assert "## Grafias a conferir" in report and "IBGE" in report
assert "Nada foi alterado nas transcricoes" in report
print("PASS: format_glossary_report")

# --- caminhos e carga tolerante ---
from transcribe_pipeline.config import ensure_directories, load_config, make_paths
with tempfile.TemporaryDirectory() as tmp:
    paths = make_paths(load_config(None), base_dir=Path(tmp))
    ensure_directories(paths)
    assert gl.load_glossary(paths) == {}          # ausente
    target = gl.glossary_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{quebrado", encoding="utf-8")
    assert gl.load_glossary(paths) == {}          # corrompido: fail-soft
    from transcribe_pipeline.utils import write_json
    write_json(target, com_ajuda)
    assert gl.load_glossary(paths)["entradas"]
    assert gl.glossary_report_path(paths).name == "glossario_do_projeto.md"
print("PASS: caminhos e load_glossary")

# --- correcao de grafia (6b): casamento conservador ---
texto = "Fui ao Meia e comprei meias. Meias e Meia sao diferentes; Ameia tambem."
spans = gl.variant_spans(texto, "Meia")
assert len(spans) == 2, spans                      # so as ocorrencias exatas
assert all(texto[a:b] == "Meia" for a, b in spans)
assert gl.variant_spans(texto, "meia") == []       # caixa importa
assert gl.variant_spans("Ameia Meias", "Meia") == []   # fronteira de palavra
assert gl.variant_spans("Moro em Viçosa hoje", "Viçosa")        # acento casa
assert gl.variant_spans("nada aqui", "Celso") == []
assert gl.variant_spans("Celso", "") == []
# pontuacao e fronteira valida
assert len(gl.variant_spans('Foi no "Meia", sim.', "Meia")) == 1
print("PASS: variant_spans")

# --- apply_spans: de tras para frente, indices nao se deslocam ---
alvo = "O Celso e o Celso de novo"
todos = gl.variant_spans(alvo, "Celso")
assert gl.apply_spans(alvo, todos, "Censo") == "O Censo e o Censo de novo"
# aplicar SO a segunda ocorrencia
assert gl.apply_spans(alvo, [todos[1]], "Censo") == "O Celso e o Censo de novo"
# canonico mais longo que a variante nao corrompe o span seguinte
assert gl.apply_spans("BGA e BGA", gl.variant_spans("BGA e BGA", "BGA"), "IBGE") == "IBGE e IBGE"
print("PASS: apply_spans")

# --- occurrence_excerpt ---
longo = "a" * 200 + " Meia " + "b" * 200
trecho = gl.occurrence_excerpt(longo, gl.variant_spans(longo, "Meia")[0])
assert trecho.startswith("…") and trecho.endswith("…") and "Meia" in trecho
curto = gl.occurrence_excerpt("Oi Meia tchau", gl.variant_spans("Oi Meia tchau", "Meia")[0])
assert curto == "Oi Meia tchau"                    # cabe inteiro: sem reticencias
print("PASS: occurrence_excerpt")

# --- integracao: aplica SO o aprovado, canonico intacto ---
from transcribe_pipeline import review_store as rs
from transcribe_pipeline.utils import read_json, write_json as _wj
with tempfile.TemporaryDirectory() as tmp:
    paths = make_paths(load_config(None), base_dir=Path(tmp))
    ensure_directories(paths)
    canonico_payload = {
        "interview_id": "T01",
        "turns": [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "human_label": "E",
             "text": "Trabalhei no Caxambia e depois no Caxambia de novo."},
            {"start": 5.0, "end": 9.0, "speaker": "SPEAKER_01", "human_label": "P",
             "text": "Comprei meias no Meia da esquina."},
        ],
    }
    _wj(rs.canonical_path(paths, "T01"), canonico_payload)
    canonico_antes = rs.canonical_path(paths, "T01").read_bytes()
    review = rs.load_review_transcript(paths, "T01")
    turns = rs.review_turns(review)
    rs.save_review_transcript(paths, "T01", review)
    turn_id = turns[0]["id"]

    todas = gl.collect_occurrences(paths, ["T01"], "Caxambia", "Caxambi")
    assert len(todas) == 2 and all(o["interview_id"] == "T01" for o in todas)
    assert todas[0]["turn_id"] == turn_id and "Caxambia" in todas[0]["trecho"]
    # aprova SO a primeira
    resultado = gl.apply_corrections(paths, [todas[0]])
    assert resultado == {"blocos": 1, "ocorrencias": 1, "arquivos": 1}, resultado
    depois = rs.review_turns(rs.load_review_transcript(paths, "T01"))
    assert depois[0]["text"] == "Trabalhei no Caxambi e depois no Caxambia de novo."
    assert depois[0]["edited"] is True
    assert depois[1]["text"] == canonico_payload["turns"][1]["text"]   # intocado
    edits = rs.load_review_transcript(paths, "T01").get("edits") or []
    assert any(e.get("action") == "set_text" for e in edits)
    # o canonico e a camada auditavel: NAO pode mudar
    assert rs.canonical_path(paths, "T01").read_bytes() == canonico_antes
    # a palavra comum minuscula continua intacta
    assert "meias" in depois[1]["text"]
    # decisao com span defasado (texto mudou) e simplesmente ignorada
    obsoleta = dict(todas[1], span=(0, 8))
    assert gl.apply_corrections(paths, [obsoleta])["ocorrencias"] == 0
print("PASS: collect_occurrences + apply_corrections")

# --- pending_variants ---
with tempfile.TemporaryDirectory() as tmp:
    paths = make_paths(load_config(None), base_dir=Path(tmp))
    ensure_directories(paths)
    assert gl.pending_variants(paths) == []
    alvo = gl.glossary_path(paths)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    from transcribe_pipeline.utils import write_json as _wj2
    _wj2(alvo, com_ajuda)
    pend = gl.pending_variants(paths)
    assert pend and all({"canonico", "variante", "total"} <= set(p) for p in pend)
print("PASS: pending_variants")

# --- o modelo precisa do repo do tokenizador junto (senao quebra offline) ---
try:
    from transcribe_pipeline import model_manager as mm
    asset = mm.optional_model(gl.NER_ASSET_KEY)
    assert asset.revision, "modelo de nomes sem SHA pinada"
    # Bug real (E2E 2026-08-28): o GLiNER carrega tokenizer/config do
    # encoder base; sem o repo acompanhante em cache, o worker offline
    # falha com "couldn't connect to huggingface.co".
    assert asset.companion_repo, "modelo de nomes sem repo de tokenizador"
    assert asset.companion_revision, "repo do tokenizador sem SHA pinada"
    assert asset.companion_repo in mm._known_repos()  # nunca listado como orfao
    print("PASS: modelo de nomes com tokenizador pinado")
except ImportError as exc:
    print(f"SKIP: registro do modelo ({exc})")

print("PASS: toy_glossario")
