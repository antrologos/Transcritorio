"""Toy test: perguntar as entrevistas (fase 2.7) — partes puras."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.llm_worker import (
    SEM_RESPOSTA,
    format_trechos,
    validate_answer,
)

# --- format_trechos: numeracao e campos ---
trechos = [
    {"interview_id": "D06R", "inicio": "00:13:39", "label": "Entrevistada 1", "text": " o bônus  atrasou "},
    {"interview_id": "D08R", "inicio": "00:22:09", "label": "ENTREVISTADO", "text": "tinha um prazo pra cair"},
]
formatted = format_trechos(trechos)
assert formatted.splitlines()[0] == "[1] (D06R, 00:13:39, Entrevistada 1) o bônus atrasou"
assert "[2] (D08R" in formatted
print("PASS: format_trechos")

# --- validate_answer: citacao obrigatoria OU recusa exata ---
assert validate_answer("O pagamento atrasou [1] e havia um prazo [2].", 2) is True
assert validate_answer("O pagamento atrasou.", 2) is False           # sem citacao
assert validate_answer("Citando [3] inexistente.", 2) is False       # fora do intervalo
assert validate_answer(SEM_RESPOSTA, 2) is True                       # recusa honesta
assert validate_answer(f"  {SEM_RESPOSTA.lower()}  ", 0) is True
print("PASS: validate_answer")

# --- validate_answer reconhece a recusa COM acentos (o Qwen escreve assim) ---
assert validate_answer("Isso não aparece nas entrevistas disponíveis.", 3) is True
assert validate_answer("ISSO NÃO APARECE NAS ENTREVISTAS DISPONÍVEIS", 3) is True
print("PASS: validate_answer com acentos")

# --- visao geral: secoes do resumo, lotes, formato, citacoes [ID] (puras) ---
from transcribe_pipeline.llm_worker import (  # noqa: E402
    batch_resumos,
    cited_interviews,
    format_resumos,
    split_resumo_sections,
)

md = ("# Titulo\n\n## Resumo\nA entrevistada fala do pagamento.\nSegunda linha.\n\n"
      "## Índice temático\n- [00:01:00] Pagamento — atraso\n- [00:05:00] Treinamento — curto\n\n"
      "## Observações\nTensão com supervisor.\n")
secs = split_resumo_sections(md)
assert secs["resumo"] == "A entrevistada fala do pagamento.\nSegunda linha."
assert secs["indice"].startswith("- [00:01:00] Pagamento") and "Treinamento" in secs["indice"]
assert secs["observacoes"] == "Tensão com supervisor."
assert split_resumo_sections("## resumo\nx\n## Indice Tematico\ny")["indice"] == "y"  # sem acento, outra caixa
assert split_resumo_sections("") == {"resumo": "", "indice": "", "observacoes": ""}
resumos = [{"interview_id": f"E{i}", "resumo": "r" * 3000, "indice": "i" * 1000} for i in range(5)]
lotes = batch_resumos(resumos, max_chars=9000)
assert [len(l) for l in lotes] == [2, 2, 1] and sum(len(l) for l in lotes) == 5, [len(l) for l in lotes]
bloco = format_resumos([{"interview_id": "A05R", "titulo": "Denise", "resumo": "fala X", "indice": "- t1"}])
assert bloco.startswith("=== [A05R] (Denise) ===\nfala X\nTemas: - t1")
assert cited_interviews("Duas falam disso [A05R][B10R] e [A05R] de novo; [3] nao e id.", ["A05R", "B10R", "C1"]) == ["A05R", "B10R"]
assert cited_interviews("nada", ["A05R"]) == []
print("PASS: visao geral (puras)")

# --- ask: question_kind, build_trechos, answer_worth_trying (importa ask; deps do app) ---
try:
    from transcribe_pipeline.ask import answer_worth_trying, build_trechos, question_kind
    for q in ("do que falam as entrevistas?", "Quais são os principais temas?", "Sobre o que tratam?",
              "em geral, como foi?", "me dê uma visão geral", "resuma as entrevistas", "no conjunto, o que aparece"):
        assert question_kind(q) == "global", q
    for q in ("problemas com o pagamento", "Como foi a recepção em campo?", "quem falou de greve?", ""):
        assert question_kind(q) == "trechos", q
    built = build_trechos([
        {"interview_id": "X", "start": 819.9, "end": 830.0, "t_from": 3, "t_to": 5, "text": "t",
         "similarity": 0.4, "score": 0.9, "z": 2.5},
    ])
    assert built[0]["n"] == 1 and built[0]["inicio"] == "00:13:39" and built[0]["t_to"] == 5
    assert built[0]["similarity"] == 0.4 and built[0]["score"] == 0.9
    assert answer_worth_trying({"reranked": True, "sections": [{"key": "responde", "hits": [1]}]}) is True
    assert answer_worth_trying({"reranked": True, "sections": [{"key": "relacionado", "hits": [1], "weak": True}]}) is False
    assert answer_worth_trying({"reranked": False, "sections": [{"key": "proximo", "hits": [1], "weak": False}]}) is True
    assert answer_worth_trying({"reranked": False, "sections": [{"key": "relacionado", "hits": [1], "weak": True}]}) is False
    assert answer_worth_trying({"reranked": True, "sections": []}) is False
    print("PASS: question_kind / build_trechos / answer_worth_trying")

    # --- _run_worker: resultado pelo stdout (@RESULT) vence; arquivo e reserva ---
    # (o llm-venv criado do Python da Microsoft Store grava em pasta virtualizada
    #  que o app nao ve — 2026-09-03; o @RESULT chega sempre pelo stdout)
    import json as _json
    import tempfile as _tempfile
    from transcribe_pipeline.ask import _run_worker
    tmp = Path(_tempfile.mkdtemp(prefix="toy_ask_worker_"))
    out = tmp / "r.json"
    progressos: list[dict] = []
    cmd = [sys.executable, "-c",
           "print('@PROGRESS {\"event\": \"x\", \"progress\": 50, \"message\": \"meio\"}');"
           "print('@RESULT {\"resposta\": \"via stdout [1]\", \"valida\": true}')"]
    got = _run_worker(cmd, out, [], progressos.append, None)
    assert got == {"resposta": "via stdout [1]", "valida": True}, got
    assert progressos and progressos[0]["progress"] == 10 + 50 * 85 // 100
    # so o arquivo (worker antigo): reserva funciona
    out2 = tmp / "r2.json"
    cmd2 = [sys.executable, "-c",
            f"import json, pathlib; pathlib.Path({str(out2)!r}).write_text(json.dumps({{'resposta': 'via arquivo'}}), encoding='utf-8')"]
    assert _run_worker(cmd2, out2, [], None, None) == {"resposta": "via arquivo"}
    assert not out2.exists()  # limpeza no finally
    # falha do worker -> None
    assert _run_worker([sys.executable, "-c", "raise SystemExit(3)"], tmp / "r3.json", [], None, None) is None
    print("PASS: _run_worker (stdout @RESULT, reserva por arquivo, falha)")
except ImportError as exc:
    print(f"SKIP: ask ({exc})")

print("PASS: toy_ask")
