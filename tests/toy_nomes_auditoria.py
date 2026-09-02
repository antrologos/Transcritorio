"""Toy: qualquer nome de arquivo valido no SO funciona — correcoes da auditoria
de 2026-09-02 (lote mecanico + ids que colidem).

A) Apagar transcricao / Lixeira: "Sonia" NAO leva os derivados de "Sonia.Venancio".
D) [ ] no nome sao literais no rglob (glob.escape).
K) qc.find_raw_json nao herda o JSON de outra entrevista ("Entrevista 1" x "10").
L) Parser YAML preserva " #" em itens de lista ("Entrevista #3.m4a").
J) Atalho PowerShell: apostrofo no nome do usuario Windows.
E) Renomear so a caixa da midia migra os metadados.
F) --ids sem caixa.
2) Mesmo nome de arquivo em pastas diferentes = entrevistas diferentes;
   ids iguais so na caixa nao colidem; mesma pasta (.mp3 + .m4a) segue duplicata.
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import app_service, qc  # noqa: E402
from transcribe_pipeline.config import _load_simple_yaml  # noqa: E402
from transcribe_pipeline.install_tools import _shortcut_script  # noqa: E402
from transcribe_pipeline.manifest import MediaFile, build_manifest, disambiguate_free_ids, selected_rows  # noqa: E402
from transcribe_pipeline.project_store import metadata_path, read_file_metadata, sync_file_metadata, write_file_metadata  # noqa: E402
from transcribe_pipeline.utils import is_interview_artifact  # noqa: E402

# ---------------------------------------------------------------- A) dono = id mais longo
ids = {"Sonia", "Sonia.Venancio", "entrevista_1", "entrevista_10"}
assert is_interview_artifact("Sonia.review.json", "Sonia", ids)
assert is_interview_artifact("Sonia.exclusive.json", "Sonia", ids)
assert is_interview_artifact("Sonia_nvivo.tsv", "Sonia", ids)
assert not is_interview_artifact("Sonia.Venancio.review.json", "Sonia", ids), "levaria a revisao editada de outra entrevista"
assert not is_interview_artifact("Sonia.Venancio.json", "Sonia", ids)
assert not is_interview_artifact("Sonia.Venancio_nvivo.tsv", "Sonia", ids)
assert is_interview_artifact("Sonia.Venancio.review.json", "Sonia.Venancio", ids)
assert not is_interview_artifact("entrevista_10.json", "entrevista_1", ids)   # regra antiga continua
assert is_interview_artifact("Sonia.Venancio.json", "Sonia")                   # sem known_ids: comportamento antigo
assert is_interview_artifact("entrevista maria.review.json", "Entrevista Maria")  # rename so de caixa: derivados antigos seguem
print("PASS: A) is_interview_artifact com known_ids")

# ---------------------------------------------------------------- L) YAML " #" em lista
cfg = _load_simple_yaml(
    "audio_files:\n  - C:/Gravacoes/Entrevista #3.m4a\n  - take#3.wav\n"
    "asr_model: large-v3 # comentario\nproject_root: .\n"
)
assert cfg["audio_files"] == ["C:/Gravacoes/Entrevista #3.m4a", "take#3.wav"], cfg["audio_files"]
assert cfg["asr_model"] == "large-v3"
print("PASS: L) parser preserva ' #' em itens de lista")

# ---------------------------------------------------------------- J) apostrofo no atalho
script = _shortcut_script(r"C:\Users\D'Avila\x\pythonw.exe", "Transcritório", "-m x", r"C:\Users\D'Avila\i.ico")
assert "D''Avila" in script and "D'Avila" not in script.replace("D''Avila", ""), script
print("PASS: J) apostrofo escapado no PowerShell")

# ---------------------------------------------------------------- F) --ids sem caixa
rows = [{"interview_id": "A01P_0608", "selected": "true"}, {"interview_id": "Entrevista Maria", "selected": "true"}]
assert [r["interview_id"] for r in selected_rows(rows, ["a01p_0608"])] == ["A01P_0608"]
assert [r["interview_id"] for r in selected_rows(rows, ["entrevista maria"])] == ["Entrevista Maria"]
assert selected_rows(rows, ["nada"]) == []
print("PASS: F) selected_rows sem caixa")

# ---------------------------------------------------------------- 2) ids que colidem (pura)
def mf(p: str, iid: str | None = None) -> MediaFile:
    path = Path(p)
    return MediaFile(iid or path.stem, path.parent.name, path, path.suffix.lower(), "A")

base = Path(tempfile.gettempdir()) / "toy_nomes_auditoria_ids"
media = [
    mf(str(base / "Maria" / "Entrevista.m4a")),
    mf(str(base / "Jose" / "Entrevista.m4a")),
    mf(str(base / "Pasta2" / "entrevista.m4a")),
    mf(str(base / "A" / "Gravacao.mp3")),
    mf(str(base / "A" / "Gravacao.m4a")),      # mesma pasta: mesma gravacao
    mf(str(base / "X" / "A01P_0608_A.m4a"), "A01P_0608"),
    mf(str(base / "Y" / "A01P_0608_V.mp4"), "A01P_0608"),  # par codificado: intacto
]
disambiguate_free_ids(media)
por_caminho = {str(m.path.relative_to(base)).replace("\\", "/"): m.interview_id for m in media}
assert por_caminho["Jose/Entrevista.m4a"] == "Entrevista", por_caminho          # primeira pasta (ordem do caminho)
assert por_caminho["Maria/Entrevista.m4a"] == "Entrevista (Maria)", por_caminho
assert por_caminho["Pasta2/entrevista.m4a"] == "entrevista (Pasta2)", por_caminho
assert por_caminho["A/Gravacao.mp3"] == por_caminho["A/Gravacao.m4a"] == "Gravacao"
assert por_caminho["X/A01P_0608_A.m4a"] == por_caminho["Y/A01P_0608_V.mp4"] == "A01P_0608"
assert len({m.interview_id.casefold() for m in media}) == 5
# determinista: rodar de novo nao muda nada
antes = [m.interview_id for m in media]
disambiguate_free_ids(media)
assert [m.interview_id for m in media] == antes
# colisao do sufixo: duas pastas "Jose" distintas -> " (2)"
media2 = [mf(str(base / "a" / "Jose" / "Entrevista.m4a")), mf(str(base / "b" / "Jose" / "Entrevista.m4a")), mf(str(base / "c" / "Jose" / "Entrevista.m4a"))]
disambiguate_free_ids(media2)
assert [m.interview_id for m in media2] == ["Entrevista", "Entrevista (Jose)", "Entrevista (Jose) (2)"], [m.interview_id for m in media2]
print("PASS: 2) disambiguate_free_ids (pura)")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    ctx = app_service.create_project(tmp / "p.transcricao", "toy")
    paths = ctx.paths
    T = paths.output_root

    # ---------------------------------------------------------------- 2) build_manifest com arquivos reais (sem ffprobe)
    midia = tmp / "midia"
    for rel in ("Maria/Entrevista.m4a", "Jose/Entrevista.m4a", "Pasta2/entrevista.m4a", "A/Gravacao.mp3", "A/Gravacao.m4a"):
        f = midia / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\x00" * 16)
    cfg2 = dict(ctx.config)
    cfg2.update({"audio_roots": [str(midia)], "audio_files": [], "manifest_probe_audio": False})
    linhas = build_manifest(cfg2, paths)
    sel = sorted(r["interview_id"] for r in linhas if r["selected"] == "true")
    assert sel == ["Entrevista", "Entrevista (Maria)", "Gravacao", "entrevista (Pasta2)"], sel
    dup = [r for r in linhas if r["selected"] != "true"]
    assert len(dup) == 1 and dup[0]["interview_id"] == "Gravacao", dup
    wavs = {r["wav_path"] for r in linhas if r["selected"] == "true"}
    assert len(wavs) == 4 and len({w.casefold() for w in wavs}) == 4, wavs  # nomes distintos mesmo sem caixa
    print("PASS: 2) build_manifest separa pastas e mantem duplicata na mesma pasta")

    # ESTABILIDADE (revisao 2026-09-02): quem ja tem id mantem o id. Projeto so
    # com Maria/Entrevista.m4a (id "Entrevista", ja transcrita); entra
    # Ana/Entrevista.m4a, que ordena ANTES -> Maria continua "Entrevista".
    from transcribe_pipeline.manifest import write_manifest
    for f in list(midia.rglob("*")):
        if f.is_file() and f.parent.name != "Maria":
            f.unlink()
    linhas1 = build_manifest(cfg2, paths, previous_rows=[])
    assert [r["interview_id"] for r in linhas1] == ["Entrevista"], linhas1
    write_manifest(linhas1, paths.manifest_dir / "manifest.csv")
    (midia / "Ana").mkdir()
    (midia / "Ana" / "Entrevista.m4a").write_bytes(b"\x00" * 16)
    linhas2 = build_manifest(cfg2, paths)                 # le o manifest anterior sozinho
    ids2 = {str(Path(r["source_path"]).parent.name): r["interview_id"] for r in linhas2}
    assert ids2 == {"Maria": "Entrevista", "Ana": "Entrevista (Ana)"}, ids2
    write_manifest(linhas2, paths.manifest_dir / "manifest.csv")
    linhas3 = build_manifest(cfg2, paths)                 # idempotente
    assert {str(Path(r["source_path"]).parent.name): r["interview_id"] for r in linhas3} == ids2
    # e o mesmo via a pura, com `known`
    m = [mf(str(midia / "Ana" / "Entrevista.m4a")), mf(str(midia / "Maria" / "Entrevista.m4a"))]
    disambiguate_free_ids(m, {str((midia / "Maria" / "Entrevista.m4a").resolve()).casefold(): "Entrevista"})
    assert [x.interview_id for x in m] == ["Entrevista (Ana)", "Entrevista"], [x.interview_id for x in m]
    print("PASS: 2) ids estaveis entre atualizacoes da lista")

    # ---------------------------------------------------------------- K) qc.find_raw_json exato
    (paths.asr_dir / "json").mkdir(parents=True, exist_ok=True)
    (paths.asr_dir / "json" / "Entrevista 10.json").write_text("{}", encoding="utf-8")
    assert qc.find_raw_json(paths, "Entrevista 1") is None, "herdou o JSON de 'Entrevista 10'"
    (paths.asr_dir / "json" / "Entrevista 1.json").write_text("{}", encoding="utf-8")
    assert qc.find_raw_json(paths, "Entrevista 1").name == "Entrevista 1.json"
    (paths.asr_dir / "sub").mkdir()
    (paths.asr_dir / "sub" / "Entrevista [2].json").write_text("{}", encoding="utf-8")
    assert qc.find_raw_json(paths, "Entrevista [2]").name == "Entrevista [2].json"
    print("PASS: K) find_raw_json exato e com colchetes")

    # ---------------------------------------------------------------- A+D) apagar / lixeira
    ids2 = ["Entrevista [2]", "Entrevista [2]0", "Sonia", "Sonia.Venancio"]
    rows2 = [{"interview_id": i, "selected": "true", "wav_path": ""} for i in ids2]
    ctx2 = dataclasses.replace(ctx, rows=rows2, metadata={i: {} for i in ids2})
    paths.canonical_dir.mkdir(parents=True, exist_ok=True)
    edits = paths.review_dir / "edits"
    edits.mkdir(parents=True, exist_ok=True)
    arquivos = {
        "a": paths.asr_dir / "Entrevista [2].json",
        "b": paths.canonical_dir / "Entrevista [2].canonical.json",
        "c": paths.asr_dir / "Entrevista [2]0.json",
        "s": paths.asr_dir / "Sonia.json",
        "sv": edits / "Sonia.Venancio.review.json",
        "s2": edits / "Sonia.review.json",
    }
    for p in arquivos.values():
        p.write_text("{}", encoding="utf-8")
    lixo = {Path(x["original"]).name for x in app_service.collect_trash_files(ctx2, ["Entrevista [2]"])}
    assert {"Entrevista [2].json", "Entrevista [2].canonical.json"} <= lixo, lixo
    assert "Entrevista [2]0.json" not in lixo, lixo
    lixo_s = {Path(x["original"]).name for x in app_service.collect_trash_files(ctx2, ["Sonia"])}
    assert "Sonia.json" in lixo_s and "Sonia.review.json" in lixo_s and "Sonia.Venancio.review.json" not in lixo_s, lixo_s
    n, ctx3 = app_service.delete_transcription_outputs(ctx2, ["Entrevista [2]", "Sonia"])
    assert not arquivos["a"].exists() and not arquivos["b"].exists() and not arquivos["s"].exists()
    assert arquivos["c"].exists(), "apagou 'Entrevista [2]0' junto"
    assert arquivos["sv"].exists(), "apagou a revisao editada de 'Sonia.Venancio'"
    assert n >= 4, n
    print("PASS: A+D) apagar/lixeira com colchetes e prefixo-com-ponto")

    # ---------------------------------------------------------------- E) rename so de caixa
    src = "midia/entrevista maria.m4a"
    write_file_metadata(metadata_path(paths), {"entrevista maria": {"file_id": "entrevista maria", "source_path": src, "language": "es", "title": "Maria"}})
    rows3 = [{"interview_id": "Entrevista Maria", "selected": "true", "source_path": src}]
    synced = sync_file_metadata(paths, ctx.config, rows3, ctx.project)
    assert set(synced) == {"Entrevista Maria"}, set(synced)
    assert synced["Entrevista Maria"]["language"] == "es" and synced["Entrevista Maria"]["title"] == "Maria", synced
    assert read_file_metadata(metadata_path(paths))["Entrevista Maria"]["language"] == "es"
    # caminho DIFERENTE com o mesmo nome sem caixa nao migra (outra midia)
    rows4 = [{"interview_id": "ENTREVISTA MARIA", "selected": "true", "source_path": "outra/ENTREVISTA MARIA.m4a"}]
    synced = sync_file_metadata(paths, ctx.config, rows4, ctx.project)
    assert synced["ENTREVISTA MARIA"].get("language", "") != "es"
    print("PASS: E) rename so de caixa migra os metadados")

print("PASS: toy_nomes_auditoria")
