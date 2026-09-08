"""Toy: temas das entrevistas (Parte B, 2026-09-03) — puro, com vetores sinteticos.

Tres grupos bem separados + ruido; o agrupamento acha os tres, junta centros
quase iguais, deixa passagem de borda em dois temas (pertencimento multiplo),
manda grupos minusculos para "Outros", nomeia por termos caracteristicos e
aplica nomes da AI sem sobrescrever os do usuario.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    print(f"SKIP: numpy ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import themes as th  # noqa: E402

# --- tokens: sem stopwords, sem rotulos, sem acentos ---
assert th.tokens("Entrevistado: O pagamento atrasou muito, né? A remuneração também.") == ["pagamento", "atrasou", "remuneracao"]
assert th.tokens("SPEAKER_00: tá bom, uhum") == []
print("PASS: tokens")

# --- default_k ---
assert th.default_k(10) == 6 and th.default_k(2000) == 32 and th.default_k(100000) == 40
print("PASS: default_k")

# --- pertencimento multiplo (mecanismo puro): a ate `slack` do melhor, acima do piso ---
Vm = np.array([[1, 0, 0], [0, 1, 0], [0.7071, 0.7071, 0], [0.9, 0.1, 0], [0.05, 0.05, 0.1]], dtype=np.float32)
Cm = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)
mm = th.assign_members(Vm, Cm, slack=0.03, floor=0.3)
assert [i for i, _ in mm[0]] == [0, 3, 2] and [i for i, _ in mm[1]] == [1, 2], mm   # o 2 esta nos dois; o 4 em nenhum
assert mm[0][0][1] >= mm[0][-1][1]
assert th.assign_members(Vm, np.zeros((0, 3), dtype=np.float32)) == []
print("PASS: assign_members — pertencimento multiplo e piso")

# --- vetores sinteticos: 3 grupos em torno de eixos + 2 pontos de ruido ---
rng = np.random.default_rng(1)
dim = 16


def around(axis: int, n: int, spread: float = 0.08):
    base = np.zeros(dim); base[axis] = 1.0
    pts = base + rng.normal(0, spread, size=(n, dim))
    return pts / np.linalg.norm(pts, axis=1, keepdims=True)


A = around(0, 12); B = around(1, 10); C = around(2, 8)
noise = around(7, 1, 0.05); noise2 = around(9, 1, 0.05)
V = np.vstack([A, B, C, noise, noise2]).astype(np.float32)
labels_true = ["A"] * 12 + ["B"] * 10 + ["C"] * 8 + ["n1", "n2"]

texts = {"A": "o pagamento atrasou e a remuneração demorou a cair", "B": "o treinamento durou cinco dias com os supervisores",
         "C": "a recepção nas casas foi boa, ofereciam café", "n1": "a chuva forte", "n2": "o ônibus lotado"}
passages = [{"interview_id": f"E{i % 4}", "t_from": i, "t_to": i, "start": float(i), "end": float(i) + 1.0,
             "text": f"Entrevistado: {texts[l]}"} for i, l in enumerate(labels_true)]

centers, members = th.cluster_vectors(V, n_themes=3)
# tres temas grandes (8-12 cada), sem o ruido
sizes = sorted(len(m) for m in members if len(m) >= 3)
assert sizes == [8, 10, 12], [len(m) for m in members]
assert not any(i in (30, 31) for m in members for i, _ in m), "ruido nao pertence a tema"
# pedir mais temas do que ha: o excedente sao grupos pequenos (viram Outros), os 3 grandes sobrevivem
_c5, members5 = th.cluster_vectors(V, n_themes=5)
assert sum(1 for m in members5 if len(m) >= 6) >= 3, [len(m) for m in members5]
# pedir menos: dois temas, nada quebra
_c2, members2 = th.cluster_vectors(V, n_themes=2)
assert len(members2) == 2 and sum(len(m) for m in members2) >= 28, [len(m) for m in members2]
print("PASS: cluster_vectors — 3 temas, ruido de fora, n_themes acima e abaixo")

result = th.build_themes(passages, V, n_themes=3)
themes = result["themes"]
assert len(themes) == 3, [(t["n_passages"], t["terms"]) for t in themes]
assert [t["id"] for t in themes] == ["tema_001", "tema_002", "tema_003"]
assert all(t["n_interviews"] >= 2 for t in themes)
big = max(themes, key=lambda t: t["n_passages"])
assert big["n_passages"] >= 11 and "pagamento" in big["terms"] and big["name_source"] == "termos"
assert big["name"].startswith("pagamento") or "pagamento" in big["name"]
# ruido foi para Outros
assert {o["text"] for o in result["outros"]} >= {"Entrevistado: a chuva forte", "Entrevistado: o ônibus lotado"}
# passagens ordenadas por centralidade e com texto
assert big["passages"][0]["similarity"] >= big["passages"][-1]["similarity"] and big["passages"][0]["text"]
print("PASS: build_themes — temas, termos, Outros")

# --- c-TF-IDF: termo comum a todos os temas nao lidera ---
terms = th.ctfidf_terms([["pagamento", "atraso", "censo"], ["treinamento", "dias", "censo"], ["recepcao", "cafe", "censo"]])
assert terms[0][0] == "pagamento" or terms[0][0] == "atraso"
assert all("censo" != t[0] for t in terms)
# NA ESCALA REAL (temas de centenas de passagens, vocabulario compartilhado): o termo
# proprio do tema tem de vencer o generico comum a todos — com o idf pela contagem de
# TEMAS (em vez da frequencia total) o generico ganhava.
proprios = [["pagamento", "atraso", "salario"], ["treinamento", "prova", "curso"],
            ["bairro", "rua", "setor"], ["recusa", "porta", "vizinho"], ["greve", "protesto", "sindicato"]]
genericos = ["trabalho", "pessoal", "tempo", "cidade", "coisa", "parte", "forma", "questao"]
grandes = []
for proprio in proprios:
    doc = []
    for _ in range(200):                      # 200 passagens de 30 tokens por tema
        doc += [proprio[i % 3] for i in range(4)] + [genericos[i % 8] for i in range(26)]
    grandes.append(doc)
escala = th.ctfidf_terms(grandes)
acertos = sum(1 for proprio, t in zip(proprios, escala) if t and t[0] in proprio)
assert acertos == 5, escala          # com o idf antigo: 0 de 5
# metade da lista tem de ser do proprio tema (o generico ainda aparece: e c-TF-IDF,
# e aqui 87% dos tokens sao compartilhados — o que importa e quem LIDERA)
assert all(len(set(t) & set(proprio)) >= 2 for proprio, t in zip(proprios, escala)), escala
print("PASS: ctfidf_terms (inclusive em temas grandes com vocabulario comum)")

# --- renomear, juntar, nomes da AI (nao sobrescreve o usuario) ---
assert th.rename_theme(result, "tema_002", "Treinamento dos recenseadores", "Duração e qualidade") is True
t2 = next(t for t in result["themes"] if t["id"] == "tema_002")
assert t2["name"] == "Treinamento dos recenseadores" and t2["name_source"] == "usuario"
assert th.apply_names(result, [{"id": "tema_002", "nome": "Outro", "descricao": "x"}, {"id": "tema_001", "nome": "Pagamento e atrasos", "descricao": "Atrasos na remuneração"}]) == 1
assert t2["name"] == "Treinamento dos recenseadores"
t1 = next(t for t in result["themes"] if t["id"] == "tema_001")
assert t1["name"] == "Pagamento e atrasos" and t1["name_source"] == "ai"
n_before = len(result["themes"])
before_total = t1["n_passages"] + next(t for t in result["themes"] if t["id"] == "tema_003")["n_passages"]
assert th.merge_themes(result, "tema_001", "tema_003") is True
assert len(result["themes"]) == n_before - 1 and t1["n_passages"] <= before_total
assert th.merge_themes(result, "tema_001", "tema_999") is False
batches = th.naming_batches(result, per_batch=1)
assert len(batches) == len(result["themes"]) and batches[0][0]["id"] == "tema_001" and batches[0][0]["passages"]
# a AI pode inventar id, devolver nome vazio ou lixo: nada disso muda um tema
antes = [(t["id"], t["name"], t["name_source"]) for t in result["themes"]]
assert th.apply_names(result, [{"id": "tema_999", "nome": "Inventado"}, {"id": "tema_001", "nome": "   "},
                               {"nome": "sem id"}, {"id": "tema_001", "descricao": "sem nome"}]) == 0
assert [(t["id"], t["name"], t["name_source"]) for t in result["themes"]] == antes
assert th.apply_names(result, []) == 0
print("PASS: rename / merge / apply_names / naming_batches")

# --- vazio ---
assert th.build_themes([], np.zeros((0, 0), dtype=np.float32)) == {"themes": [], "outros": []}
print("PASS: vazio")

print("PASS: toy_themes")
