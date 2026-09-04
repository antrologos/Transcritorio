"""Gate: toda chamada `self.metodo(...)` existe — 2026-09-04.

Nasceu de um defeito real, achado no log de um usuário: `refresh_docs_panel`
chamava `self._export_target_dir()`, método que nunca existiu (o certo é
`_results_folder_for_user`, renomeado sem propagar a chamada). Resultado:
`AttributeError` NÃO TRATADO toda vez que a aba Documentos era atualizada com
uma entrevista aberta. Nenhum teste pegou, porque a suíte não passa por esse
caminho — e um arquivo de 14 mil linhas não se cobre a mão.

Como funciona: lê o código com `ast` para achar todo `self.X(...)`, e resolve
os nomes válidos na CLASSE DE VERDADE (`dir(classe)`, que já traz tudo o que
vem do Qt por herança) mais os atributos atribuídos em `self.x = ...`. Sem
lista de permissões para manter.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from transcribe_pipeline import review_studio_qt as modulo
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

ALVO = RAIZ / "transcribe_pipeline" / "review_studio_qt.py"
arvore = ast.parse(ALVO.read_text(encoding="utf-8"), filename=str(ALVO))

classes_ast: dict[str, ast.ClassDef] = {
    no.name: no for no in ast.walk(arvore) if isinstance(no, ast.ClassDef)
}


def atribuidos(classe: ast.ClassDef) -> set[str]:
    """`self.x = ...` conta: `self.callback()` sobre atributo é legítimo."""
    nomes: set[str] = set()
    for no in ast.walk(classe):
        if (isinstance(no, ast.Attribute) and isinstance(no.value, ast.Name)
                and no.value.id == "self" and isinstance(no.ctx, (ast.Store, ast.Del))):
            nomes.add(no.attr)
    return nomes


problemas: list[str] = []
analisadas = 0
sem_classe_real: list[str] = []

for nome, classe in classes_ast.items():
    real = getattr(modulo, nome, None)
    if real is None or not isinstance(real, type):
        # Classe interna que o módulo não expõe: sem a classe real não dá para
        # separar herança do Qt de erro de digitação. Fica registrada.
        sem_classe_real.append(nome)
        continue
    analisadas += 1
    conhecidos = set(dir(real)) | atribuidos(classe)
    for no in ast.walk(classe):
        if not isinstance(no, ast.Call):
            continue
        alvo = no.func
        if not (isinstance(alvo, ast.Attribute) and isinstance(alvo.value, ast.Name)
                and alvo.value.id == "self"):
            continue
        if alvo.attr not in conhecidos:
            problemas.append(f"review_studio_qt.py:{no.lineno} — {nome}.{alvo.attr}() "
                             f"é chamado mas não existe")

if problemas:
    for p in problemas:
        print("  " + p)
assert not problemas, f"{len(problemas)} chamada(s) a método inexistente (ver acima)"
assert analisadas >= 10, f"só {analisadas} classes analisadas — o módulo mudou de forma?"
print(f"PASS: toy_metodos_existem ({analisadas} classes conferidas"
      + (f"; {len(sem_classe_real)} internas não expostas ficaram de fora" if sem_classe_real else "")
      + ")")
