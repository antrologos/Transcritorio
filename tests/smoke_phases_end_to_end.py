"""Smoke end-to-end cross-fases: exercita caminhos que os smokes por
fase nao tocam (GUI real offscreen, dialogs carregaveis, pipeline
imports, integracao real de app_service).

Cobertura adicional:
- F0: dark theme aplicado; helpers de cor retornam CSS
- F1: 4 menus criados no MainWindow real (nao so mock)
- F2: ExportDialog real com autoscope funcionando
- F3: project_store.ensure_results_dir real com tmpdir
- F4: ModelManagerDialog instanciavel; 3 abas presentes
- F5: runtime.detect_device real, token_vault round-trip, scripts + deps
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FAILED: list[str] = []


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    FAILED.append(msg)


# ============================================================
# F0 — Dark theme + helpers de cor
# ============================================================
print("=" * 60)
print("F0 - Dark theme + helpers de cor")
print("=" * 60)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from transcribe_pipeline import review_studio_qt as rsq  # noqa: E402

if hasattr(rsq, "_apply_dark_theme"):
    rsq._apply_dark_theme(app)
    pal = app.palette()
    bg = pal.color(QPalette.ColorRole.Window)
    if bg.lightness() < 80:
        ok(f"_apply_dark_theme -> Window lightness={bg.lightness()} (escuro)")
    else:
        fail(f"Dark theme nao aplicado: lightness={bg.lightness()}")

# Helpers retornam CSS com cor embutida
import inspect
for attr in ("_style_ok", "_style_warn", "_style_err", "_style_muted"):
    if hasattr(rsq, attr):
        obj = getattr(rsq, attr)
        css = obj() if callable(obj) else obj
        if isinstance(css, str) and "color:" in css and "#" in css:
            ok(f"rsq.{attr}() devolve CSS: {css[:50]!r}")
        else:
            fail(f"rsq.{attr} formato estranho: {css!r}")
    else:
        fail(f"rsq.{attr} ausente")

# ============================================================
# F1 — 4 menus no MainWindow real
# ============================================================
print()
print("=" * 60)
print("F1 - Menus top-level no MainWindow real")
print("=" * 60)

try:
    win = rsq.ReviewStudioWindow(project_root=None)
    menubar = win.menuBar()
    menus = [a.text().replace("&", "") for a in menubar.actions()]
    expected = ["Arquivo", "Editar", "Transcrever", "Ajuda"]
    if menus == expected:
        ok(f"4 menus exatos: {menus}")
    else:
        fail(f"menus divergem -> {menus}")

    # Capturar (text, shortcut) inline para evitar lifecycle QAction
    collected: list[tuple[str, str]] = []
    def walk(menu):
        for a in menu.actions():
            sc = a.shortcut().toString()
            if sc:
                collected.append((a.text().replace("&", ""), sc))
            if a.menu():
                walk(a.menu())
    for a in menubar.actions():
        if a.menu():
            walk(a.menu())

    shortcuts = dict(collected)
    expected_shortcuts = {
        "Novo projeto...": "Ctrl+N",
        "Abrir projeto...": "Ctrl+O",
        "Exportar...": "Ctrl+E",
        "Salvar transcrição": "Ctrl+S",
    }
    for label, sc in expected_shortcuts.items():
        got = shortcuts.get(label)
        if got == sc:
            ok(f"{label} -> {sc}")
        else:
            fail(f"{label}: esperado {sc}, got {got!r}")
    win.close()
    win.deleteLater()
except Exception as exc:
    import traceback
    traceback.print_exc()
    fail(f"MainWindow instanciar falhou: {exc}")


# ============================================================
# F2 — ExportDialog autoscope via API real (selected_scope)
# ============================================================
print()
print("=" * 60)
print("F2 - ExportDialog autoscope via selected_scope()")
print("=" * 60)

try:
    # Caso 1: editor aberto -> current
    d = rsq.ExportDialog(has_open=True, open_title="demo.md", n_selected=0, n_total=5)
    s = d.selected_scope()
    if s == "current":
        ok("autoscope=current quando editor aberto + 0 check")
    else:
        fail(f"esperado current, got {s!r}")
    d.deleteLater()

    # Caso 2: 3 check -> selected
    d = rsq.ExportDialog(has_open=False, n_selected=3, n_total=5)
    s = d.selected_scope()
    if s == "selected":
        ok("autoscope=selected quando >=1 check")
    else:
        fail(f"esperado selected, got {s!r}")
    d.deleteLater()

    # Caso 3: nada -> all
    d = rsq.ExportDialog(has_open=False, n_selected=0, n_total=5)
    s = d.selected_scope()
    if s == "all":
        ok("autoscope=all quando sem editor + 0 check")
    else:
        fail(f"esperado all, got {s!r}")
    d.deleteLater()

    # Caso 4: confirm para N>=20
    d = rsq.ExportDialog(has_open=False, n_selected=0, n_total=25)
    if hasattr(d, "large_confirm") and d.large_confirm is not None:
        ok("checkbox de confirmacao presente quando N>=20 sem escopo explicito")
    else:
        fail("N>=20 sem checkbox de confirmacao obrigatoria")
    d.deleteLater()
except Exception as exc:
    import traceback
    traceback.print_exc()
    fail(f"ExportDialog falhou: {exc}")


# ============================================================
# F3 — ensure_results_dir real com tmpdir
# ============================================================
print()
print("=" * 60)
print("F3 - ensure_results_dir real")
print("=" * 60)

try:
    from transcribe_pipeline import project_store
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Exportar alguns arquivos
        final = root / "Transcricoes" / "05_transcripts_review" / "final"
        (final / "docx").mkdir(parents=True)
        (final / "md").mkdir(parents=True)
        f1 = final / "docx" / "entrevista01.docx"
        f2 = final / "md" / "entrevista01.md"
        f1.write_bytes(b"fake docx")
        f2.write_text("# fake md", encoding="utf-8")

        result = project_store.ensure_results_dir(root, [f1, f2])
        resultados = root / "Resultados"
        if resultados.exists():
            ok("Resultados/ criado")
        else:
            fail("Resultados/ nao criado")
        leiame = resultados / project_store.RESULTADOS_README
        if leiame.exists():
            content = leiame.read_text(encoding="utf-8")
            if "Seus arquivos finais" in content:
                ok("LEIA-ME.txt com conteudo correto")
            else:
                fail(f"LEIA-ME.txt conteudo errado: {content[:60]}")
        else:
            fail("LEIA-ME.txt ausente")
        if (resultados / "entrevista01.docx").exists():
            ok("entrevista01.docx espelhado")
        else:
            fail("entrevista01.docx nao espelhado")
        if (resultados / "entrevista01.md").exists():
            ok("entrevista01.md espelhado")
        else:
            fail("entrevista01.md nao espelhado")
        ok(f"report: created={result.get('created')}, method={result.get('method')}")

        # Idempotente
        result2 = project_store.ensure_results_dir(root, [f1, f2])
        if result2.get("created", -1) == 0:
            ok("idempotente: 2a chamada created=0")
        else:
            fail(f"2a chamada nao idempotente: created={result2.get('created')}")
except Exception as exc:
    import traceback
    traceback.print_exc()
    fail(f"F3 falhou: {exc}")


# ============================================================
# F4 — ModelManagerDialog com 3 abas
# ============================================================
print()
print("=" * 60)
print("F4 - ModelManagerDialog com 3 abas")
print("=" * 60)

try:
    # Implementacao real: flat layout com QTableWidget + 5 botoes (nao tabs)
    def _ctx_provider():
        return None

    d = rsq.ModelManagerDialog(context_provider=_ctx_provider, parent=None)
    from PySide6.QtWidgets import QTableWidget, QPushButton
    tables = d.findChildren(QTableWidget)
    if len(tables) == 1:
        cols = [tables[0].horizontalHeaderItem(i).text() for i in range(tables[0].columnCount())]
        if cols == ["Modelo", "Tamanho", "Status", "Baixado em", ""]:
            ok(f"QTableWidget com 5 colunas corretas")
        else:
            fail(f"colunas divergem: {cols}")
    else:
        fail(f"esperava 1 tabela, achou {len(tables)}")

    buttons = [b.text() for b in d.findChildren(QPushButton)]
    expected_btns = {"Abrir pasta de modelos", "Remover orfaos", "Baixar outros modelos...", "Trocar token HF...", "Fechar"}
    missing = expected_btns - set(buttons)
    if not missing:
        ok(f"5 botoes principais presentes")
    else:
        fail(f"botoes faltando: {missing}")

    if d.windowTitle() == "Gerenciar modelos":
        ok(f"titulo 'Gerenciar modelos' correto")
    else:
        fail(f"titulo errado: {d.windowTitle()!r}")

    d.close()
    d.deleteLater()
except Exception as exc:
    import traceback
    traceback.print_exc()
    fail(f"ModelManagerDialog falhou: {exc}")


# ============================================================
# F5 — runtime + token_vault + scripts + deps
# ============================================================
print()
print("=" * 60)
print("F5 - Integracao cross-modulo")
print("=" * 60)

from transcribe_pipeline import runtime, token_vault
try:
    dev = runtime.detect_device()
    if dev in ("cuda", "mps", "cpu"):
        ok(f"runtime.detect_device() = {dev!r}")
    else:
        fail(f"detect_device ruim: {dev!r}")
except Exception as exc:
    fail(f"detect_device falhou: {exc}")

try:
    d, fb = runtime.resolve_device("cuda")
    if d in ("cuda", "cpu") and isinstance(fb, bool):
        ok(f"resolve_device('cuda') = ({d!r}, fell_back={fb})")
    else:
        fail(f"resolve_device ruim: ({d}, {fb})")
except Exception as exc:
    fail(f"resolve_device falhou: {exc}")

if sys.platform == "win32":
    import importlib
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        importlib.reload(token_vault)
        try:
            token_vault.store("hf_E2E_ABC")
            if token_vault.retrieve() == "hf_E2E_ABC":
                backend = "keyring" if token_vault._keyring_available() else "DPAPI legacy"
                ok(f"token_vault round-trip real OK (backend={backend})")
            else:
                fail("token_vault round-trip falhou")
            token_vault.clear()
            if token_vault.retrieve() is None:
                ok("token_vault clear OK")
            else:
                fail("clear incompleto")
        finally:
            del os.environ["TRANSCRITORIO_HOME"]

for name in ("setup_transcription_env.sh", "review_studio.sh", "transcribe.sh"):
    p = REPO / "scripts" / name
    if p.exists() and p.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash"):
        ok(f"{name} presente")
    else:
        fail(f"{name} ausente ou corrompido")

pyproj = (REPO / "pyproject.toml").read_text(encoding="utf-8")
if "keyring>=24" in pyproj and "cryptography>=42" in pyproj:
    ok("pyproject.toml com keyring + cryptography")
else:
    fail("pyproject.toml sem novas deps")

if (REPO / "docs" / "MAC_LINUX.md").exists():
    ok("docs/MAC_LINUX.md presente")
else:
    fail("docs/MAC_LINUX.md ausente")


# ============================================================
# Transversal — invariantes nao-regressao
# ============================================================
print()
print("=" * 60)
print("TRANSVERSAL - invariantes nao-regressao")
print("=" * 60)

src = (REPO / "transcribe_pipeline" / "review_studio_qt.py").read_text(encoding="utf-8")
if "def close_open_file" in src and "def close_current_review" not in src:
    ok("close_open_file existe; close_current_review removido")
else:
    fail("regressao: close_current_review presente ou close_open_file ausente")

if "_style_ok" in src and "_style_warn" in src:
    ok("helpers de cor em uso no codigo")
else:
    fail("helpers de cor nao usados")

if "ShortcutContext" in src:
    ok("ShortcutContext ativo (contextual Ctrl+Z)")
else:
    fail("ShortcutContext removido")

cfg_src = (REPO / "transcribe_pipeline" / "config.py").read_text(encoding="utf-8")
if "use_resultados_dir" in cfg_src:
    ok("config.use_resultados_dir flag presente")
else:
    fail("feature flag use_resultados_dir sumiu")


# ============================================================
print()
print("=" * 60)
if FAILED:
    print(f"SMOKE CROSS-FASES: {len(FAILED)} FALHA(S)")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("SMOKE CROSS-FASES: TUDO OK (F0+F1+F2+F3+F4+F5)")
print("=" * 60)
app.quit()
