"""Toy R4-c2 (U1.4 segunda metade): ETA relativa na Fila de processamento.

Cobre format_eta (faixas de tempo) e o helper novo eta_text_for_job:
a coluna Estimativa so mostra tempo restante para job RODANDO com
estimativa gravada — Concluido/Falha/Pendente ficam vazios (antes o
dialogo mostrava o horario absoluto de termino, ininteligivel; e um
'estimando…' eterno em job concluido seria mentira).
Sem PySide6: as funcoes sao puras (skip se o import da GUI falhar).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from transcribe_pipeline.review_studio_qt import eta_text_for_job, format_eta
except ImportError as exc:  # CI minimo sem PySide6
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

# ---------------------------------------------------------------- format_eta
assert format_eta(None) == "estimando…"
assert format_eta(0) == "estimando…"
assert format_eta(-5) == "estimando…"
assert format_eta(59) == "cerca de 59s"
assert format_eta(60) == "cerca de 1min 00s"
assert format_eta(3599) == "cerca de 59min 59s"
assert format_eta(3600) == "cerca de 1h 00min"
assert format_eta(7321) == "cerca de 2h 02min"

# ---------------------------------------------------------- eta_text_for_job
agora = datetime(2026, 8, 31, 12, 0, 0)  # naive local, como o relay grava

# Rodando com estimativa naive local (formato real do job_step.relay)
rodando = {"status": "Rodando",
           "estimated_finish_at": (agora + timedelta(seconds=90)).isoformat()}
assert eta_text_for_job(rodando, agora) == "cerca de 1min 30s"

# Estimativa vencida (re-estimando): nunca numero negativo
vencida = {"status": "Rodando",
           "estimated_finish_at": (agora - timedelta(seconds=30)).isoformat()}
assert eta_text_for_job(vencida, agora) == "estimando…"

# Sem estimativa ainda (inicio do step): vazio, nao 'estimando…' cru
assert eta_text_for_job({"status": "Rodando", "estimated_finish_at": ""}, agora) == ""
assert eta_text_for_job({"status": "Rodando"}, agora) == ""

# Estados nao-rodando: SEMPRE vazio, mesmo com resto de estimativa gravada
for status in ("Concluido", "Concluído", "Falha", "Pendente", "Na fila", ""):
    job = {"status": status,
           "estimated_finish_at": (agora + timedelta(seconds=300)).isoformat()}
    assert eta_text_for_job(job, agora) == "", status

# Timestamp ilegivel: vazio, nunca excecao
assert eta_text_for_job({"status": "Rodando", "estimated_finish_at": "ontem"}, agora) == ""

# Formato tz-aware (defensivo — formato antigo/futuro): converte e calcula
aware = {"status": "Rodando",
         "estimated_finish_at": (agora + timedelta(seconds=120))
         .astimezone().astimezone(timezone.utc).isoformat()}
texto_aware = eta_text_for_job(aware, agora)
assert texto_aware.startswith("cerca de "), texto_aware

print("PASS: toy_format_eta (format_eta + eta_text_for_job)")
