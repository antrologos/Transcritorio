"""Toy: nenhum teste constroi a janela principal com o HOME real — 2026-09-07.

Construir a ReviewStudioWindow tem efeito colateral em disco: a semente das
novidades grava em app_settings.json (runtime.app_data_dir). Um unico teste
sem isolamento basta para escrever no app_settings REAL de quem roda a
suite — e foi o que aconteceu em 2026-09-05: smoke_phases_end_to_end
construia a janela na linha ~81 e so definia TRANSCRITORIO_HOME na ~308,
para outra secao. Resultado: "novidades_versao_vista" gravado na maquina
do autor, e a faixa de novidades que ele nunca viu.

Gate ESTATICO (le os fontes, nao roda nada): em todo tests/*.py que
constroi ReviewStudioWindow(, a primeira atribuicao a TRANSCRITORIO_HOME
tem de vir ANTES da primeira construcao. Nao pega tudo (um helper que
constroi a janela em outro modulo escaparia), mas pega o caso que
aconteceu e o mais provavel de acontecer de novo.

Puro: so leitura de arquivos. Sem Qt.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTES = Path(__file__).resolve().parent
CONSTROI = re.compile(r"\bReviewStudioWindow\s*\(")
ISOLA = re.compile(r"""environ\s*\[\s*["']TRANSCRITORIO_HOME["']\s*\]\s*=|setdefault\(\s*["']TRANSCRITORIO_HOME["']""")

culpados: list[str] = []
conferidos = 0
for arquivo in sorted(TESTES.glob("*.py")):
    if arquivo.name == Path(__file__).name:
        continue
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    primeira_janela = next((i for i, ln in enumerate(linhas, 1) if CONSTROI.search(ln)
                            and not ln.lstrip().startswith("#")), None)
    if primeira_janela is None:
        continue
    conferidos += 1
    primeira_isolacao = next((i for i, ln in enumerate(linhas, 1) if ISOLA.search(ln)
                              and not ln.lstrip().startswith("#")), None)
    if primeira_isolacao is None:
        culpados.append(f"{arquivo.name}: constroi a janela na linha {primeira_janela} "
                        "e NUNCA isola TRANSCRITORIO_HOME")
    elif primeira_isolacao > primeira_janela:
        culpados.append(f"{arquivo.name}: constroi a janela na linha {primeira_janela}, "
                        f"mas so isola TRANSCRITORIO_HOME na linha {primeira_isolacao}")

assert conferidos >= 20, f"o gate deixou de enxergar os testes de janela ({conferidos})"
assert not culpados, (
    "teste que grava no app_settings REAL de quem roda a suite:\n  "
    + "\n  ".join(culpados)
    + "\nDefina os.environ['TRANSCRITORIO_HOME'] = tempfile.mkdtemp() ANTES de "
      "construir a janela (padrao de toy_atalhos_estudio.py).")
print(f"PASS: {conferidos} testes constroem a janela principal, todos com o HOME isolado antes")
print("PASS: toy_testes_isolam_home")
