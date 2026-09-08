"""Toy: a ponte REAL busca -> temas (Parte B, 2026-09-03), com encoder falso.

Os outros toys de temas usam vetores sintéticos ou dublês de `discover`; este
exercita `search.build_indexes` -> `themes.collect_passages` ->
`themes.discover` de verdade, que é onde moram os defeitos caros:

- a ordem das passagens tem de casar com a ordem dos vetores concatenados;
- cancelar no meio NÃO pode sobrescrever `temas_projeto.json` (o usuário
  perderia os temas anteriores, com os nomes que deu, achando que nada mudou);
- um `.npy` corrompido tem de ser refeito, não deixar a entrevista sumir da
  busca e dos temas em silêncio.

Encoder monkeypatchado (vetor determinístico por texto): nenhum modelo baixado.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = tempfile.mkdtemp(prefix="toy_themes_index_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp) / "models")

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - CI minimo sem numpy
    print(f"SKIP: numpy ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import search, themes  # noqa: E402
from transcribe_pipeline.config import DEFAULT_CONFIG, ensure_directories, make_paths  # noqa: E402
from transcribe_pipeline.review_store import save_review_transcript  # noqa: E402

DIM = 8
# "favor" só aparece nas perguntas de roteiro: dá ao texto do entrevistador
# uma direção própria, para o teste distinguir o vetor de cada escopo.
EIXOS = {"pagamento": 0, "treinamento": 1, "bairro": 2, "favor": 3, "termo": 4}


def fake_embed(texts, encoder, kind="passage"):
    """Vetor por PALAVRA-CHAVE presente no texto (determinístico e separável)."""
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, text in enumerate(texts):
        baixo = str(text).lower()
        for palavra, eixo in EIXOS.items():
            if palavra in baixo:
                out[i, eixo] += 1.0
        out[i, DIM - 1] += 0.05 * (i % 3)            # ruidinho estavel
        norma = float(np.linalg.norm(out[i]))
        out[i] = out[i] / norma if norma else out[i]
    return out


search.load_encoder = lambda key=None: object()      # type: ignore[assignment]
search.embed_texts = fake_embed                      # type: ignore[assignment]
search.encoder_cached = lambda key=None: True        # type: ignore[assignment]

root = Path(_tmp) / "proj"
config = dict(DEFAULT_CONFIG)
config["project_root"] = str(root)
paths = make_paths(config, base_dir=root)
ensure_directories(paths)

FALAS = {
    "pagamento": "o pagamento atrasou e a remuneração demorou muito a cair na conta",
    "treinamento": "o treinamento durou cinco dias com os supervisores e o manual",
    "bairro": "o bairro é grande e as ruas do setor eram difíceis de percorrer",
}
IDS = ["E1", "E2", "E3"]
for iid in IDS:
    turns = []
    t = 0.0
    # abertura só do entrevistador (termo de consentimento): vira um trecho
    # SEM nenhuma fala do entrevistado — tem de sumir quando ele é o escopo
    for n in range(3):
        turns.append({"id": f"t{len(turns)}", "start": t, "end": t + 30.0,
                      "human_label": "Entrevistador",
                      "text": " ".join([f"leio o termo de consentimento parte {n}"] * 12)})
        t += 30.0
    for repeticao in range(4):
        for assunto, fala in FALAS.items():
            # pergunta de roteiro (sempre igual) + resposta: e o par que o
            # filtro por falante precisa separar
            turns.append({"id": f"t{len(turns)}", "start": t, "end": t + 5.0,
                          "human_label": "Entrevistador",
                          "text": f"me fala sobre {assunto} por favor"})
            t += 5.0
            turns.append({"id": f"t{len(turns)}", "start": t, "end": t + 20.0,
                          "human_label": "Entrevistado",
                          "text": f"{fala} (parte {repeticao + 1} de {assunto})"})
            t += 20.0
    save_review_transcript(paths, iid, {"transcript": {"turns": turns}})

# --- indexar de verdade e conferir a costura passagens x vetores ---
assert search.build_indexes(paths, IDS) == 0
passages, vectors, _desc = themes.collect_passages(paths, IDS)
assert len(passages) == vectors.shape[0] > 0, (len(passages), vectors.shape)
# cada vetor tem de ser o do SEU texto (a concatenacao nao pode embaralhar)
esperado = fake_embed([p["text"] for p in passages], None)
assert np.allclose(vectors, esperado, atol=2e-3), "vetores fora de ordem em relacao as passagens"
assert {p["interview_id"] for p in passages} == set(IDS)
print(f"PASS: costura passagens x vetores ({len(passages)} passagens)")

# --- descoberta real ---
resultado = themes.discover(paths, IDS, n_themes=3)
assert themes.themes_path(paths).exists()
assert len(resultado["themes"]) >= 2, [t["name"] for t in resultado["themes"]]
assert resultado["n_passages"] == len(passages)
assert sorted(resultado["interview_ids"]) == IDS
todos_termos = {t for tema in resultado["themes"] for t in tema["terms"]}
assert todos_termos & {"pagamento", "treinamento", "bairro"}, todos_termos
print(f"PASS: discover real ({len(resultado['themes'])} temas)")

# --- cancelar NAO pode sobrescrever os temas anteriores ---
themes.rename_theme(resultado, resultado["themes"][0]["id"], "Nome que o usuário deu")
themes.save_themes(paths, resultado)
antes = themes.themes_path(paths).read_text(encoding="utf-8")
for iid in IDS:                                   # invalidar os indices
    search.index_path(paths, iid).unlink()
cancelado = themes.discover(paths, IDS, n_themes=3, should_cancel=lambda: True)
assert cancelado.get("cancelado") is True and cancelado["themes"] == []
assert themes.themes_path(paths).read_text(encoding="utf-8") == antes, \
    "cancelar sobrescreveu os temas (o nome do usuario se perderia)"
assert themes.load_themes(paths)["themes"][0]["name"] == "Nome que o usuário deu"
print("PASS: cancelar preserva os temas anteriores")

# --- .npy corrompido volta a ser indexado (nao some em silencio) ---
assert search.build_indexes(paths, IDS) == 0      # refaz o que foi invalidado acima
search.vectors_path(paths, "E2").write_bytes(b"lixo que nao e npy")
assert search.index_is_fresh(paths, "E2") is False, "indice ilegivel nao pode passar por fresco"
assert len(search.load_indexes(paths, IDS)) == 2
assert search.build_indexes(paths, IDS) == 0
assert search.index_is_fresh(paths, "E2") is True
p2, v2, _d2 = themes.collect_passages(paths, IDS)
assert len(p2) == len(passages) and {p["interview_id"] for p in p2} == set(IDS)
print("PASS: .npy corrompido e refeito")

# --- quem entra: vetores so com a fala escolhida, com cache ---
todas, _v, _d0 = themes.collect_passages(paths, IDS)
so_entrevistado, v_ent, descartadas = themes.collect_passages(paths, IDS, speakers=["Entrevistado"])
assert len(so_entrevistado) < len(todas), "o trecho só do entrevistador tinha de sumir"
assert v_ent.shape[0] == len(so_entrevistado) > 0
# nenhuma passagem so do entrevistador sobrevive
turns_por_id = {iid: search.load_source_turns(paths, iid) for iid in IDS}
for p in so_entrevistado:
    texto = search.passage_scope_text(turns_por_id[p["interview_id"]], p, {"Entrevistado"})
    assert texto.strip(), p
# e o vetor e o da FALA ESCOLHIDA, nao o do trecho inteiro
textos = [search.passage_scope_text(turns_por_id[p["interview_id"]], p, {"Entrevistado"})
          for p in so_entrevistado]
esperado = fake_embed(textos, None)
assert np.allclose(v_ent, esperado, atol=2e-3), "o vetor nao saiu do texto restrito"
assert themes.scope_cache_path(paths).exists() and themes.scope_signature_path(paths).exists()
# trecho com pouca (ou nenhuma) fala escolhida sai do agrupamento, mas volta
# em "sem tema definido": continua visivel e codificavel
assert descartadas and len(so_entrevistado) + len(descartadas) == len(todas)
for p in descartadas:
    texto = search.passage_scope_text(turns_por_id[p["interview_id"]], p, {"Entrevistado"})
    assert len(texto.split()) < themes.MIN_SCOPE_WORDS, (len(texto.split()), p)
com_escopo_out = themes.discover(paths, IDS, n_themes=3, speakers=["Entrevistado"])
chaves_outros = {(o["interview_id"], o["t_from"], o["t_to"]) for o in com_escopo_out["outros"]}
assert all((p["interview_id"], p["t_from"], p["t_to"]) in chaves_outros for p in descartadas), \
    "trecho descartado por pouca fala tem de aparecer em «sem tema definido»"
print(f"PASS: escopo por falante ({len(todas)} -> {len(so_entrevistado)} trechos, "
      f"{len(descartadas)} para «sem tema definido»)")

# cache: a segunda chamada nao chama o encoder de novo
chamou = {"n": 0}
_embed_real = search.embed_texts


def _contando(texts, encoder, kind="passage"):
    chamou["n"] += 1
    return _embed_real(texts, encoder, kind)


search.embed_texts = _contando            # type: ignore[assignment]
_p2, v2, _d3 = themes.collect_passages(paths, IDS, speakers=["Entrevistado"])
assert chamou["n"] == 0, "o cache do escopo nao foi usado"
assert np.allclose(v2, v_ent, atol=2e-3)
# trocar quem entra invalida o cache
_p3, v3, _d4 = themes.collect_passages(paths, IDS, speakers=["Entrevistador"])
assert chamou["n"] == 1, "mudar a escolha tem de refazer os vetores"
assert v3.shape[0] != v_ent.shape[0] or not np.allclose(v3, v_ent, atol=1e-3)
# editar uma transcricao tambem invalida (utime deixa a data explicitamente nova)
fonte_e1 = search.source_path_for(paths, "E1")
novo = fonte_e1.stat().st_mtime + 5.0
os.utime(fonte_e1, (novo, novo))
search.build_indexes(paths, IDS)           # reindexa E1 (1 chamada)
themes.collect_passages(paths, IDS, speakers=["Entrevistador"])
assert chamou["n"] == 3, f"editar a transcricao tem de refazer indice e escopo (n={chamou['n']})"
search.embed_texts = _embed_real           # type: ignore[assignment]
print("PASS: cache do escopo (reusa, e refaz quando muda)")

# --- discover com escopo grava quem valeu ---
com_escopo = themes.discover(paths, IDS, n_themes=3, speakers=["Entrevistado"])
assert com_escopo["speakers"] == ["Entrevistado"]
assert com_escopo["n_passages"] == len(so_entrevistado)
assert themes.load_themes(paths)["speakers"] == ["Entrevistado"]
sem_escopo = themes.discover(paths, IDS, n_themes=3)
assert sem_escopo["speakers"] is None and sem_escopo["n_passages"] == len(todas)
print("PASS: discover guarda a escolha de falantes")

# --- .npy com menos linhas que passagens tambem e refeito ---
with search.vectors_path(paths, "E3").open("wb") as handle:
    np.save(handle, np.zeros((1, DIM), dtype=np.float16), allow_pickle=False)
assert search.index_is_fresh(paths, "E3") is False
print("PASS: .npy com tamanho errado e refeito")

print("PASS: toy_themes_index")
