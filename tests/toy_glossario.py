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

print("PASS: toy_glossario")
