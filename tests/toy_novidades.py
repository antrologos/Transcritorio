"""Toy: as novidades da versao existem e sao curtas — 2026-09-05.

Quem instala pela primeira vez explora o programa. Quem JA usa, nao: a
pessoa aprendeu um caminho, ele funciona, e ela nunca mais abre os menus.
Sem uma superficie de novidades, um recurso feito ontem simplesmente nao
existe para ela.

A assercao mais importante aqui tem efeito colateral de proposito:
**subir a versao sem escrever as novidades quebra o CI**. A guarda e
contra quem escreve o codigo, nao contra o usuario.

Puro: so `novidades` e `__version__`. Sem Qt.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import __version__, novidades  # noqa: E402

TABELA = novidades.NOVIDADES
versoes = [v for v, _ in TABELA]

# ------------------------------------------------------- a versao de agora
assert __version__ in versoes, (
    f"a versao {__version__} nao tem entrada em novidades.py. Escreva de 3 a 6 "
    f"linhas dizendo o que mudou e ONDE achar cada coisa — quem ja usa o app "
    f"nao descobre sozinho. Versoes na tabela: {versoes}")
print(f"PASS: a versao {__version__} diz o que mudou")

assert len(versoes) == len(set(versoes)), f"versao repetida: {versoes}"
for versao, itens in TABELA:
    assert 3 <= len(itens) <= 6, f"{versao}: {len(itens)} itens (o esperado e de 3 a 6)"
    for item in itens:
        assert 40 <= len(item) <= 220, f"{versao}: item de {len(item)} caracteres: {item[:60]}"
        assert item.endswith("."), f"{versao}: item sem ponto final: {item[:60]}"
print(f"PASS: {len(versoes)} versoes, de 3 a 6 linhas cada, todas do tamanho de uma faixa")

# A versao de AGORA e a que vai para a faixa e para a aba: ao menos uma
# linha dela tem de dizer ONDE achar a coisa — "existe agora" sem "fica
# aqui" nao ajuda ninguem. Versoes antigas ficam so como registro, e
# muitas delas mudaram comportamentos que aparecem sozinhos, sem caminho.
atuais = dict(TABELA)[__version__]
assert any(("→" in item) or ("tecla" in item.lower())
           or re.search(r"\b(Alt|Ctrl|F\d)\b", item) for item in atuais), \
    f"{__version__}: nenhuma linha diz onde achar (caminho de menu ou tecla)"
print("PASS: a versao de agora aponta o caminho ou a tecla de pelo menos uma novidade")

# --------------------------------------------------------- a mecanica
uma, outra = versoes[0], versoes[-1]
assert novidades.pendentes(uma, None) == (), "instalacao nova nao recebe aviso"
assert novidades.pendentes(uma, "") == ()
assert novidades.pendentes(uma, uma) == (), "mesma versao, nada pendente"
assert novidades.pendentes("9.9.9", uma) == (), "build fora da tabela: silencio"
# `vista` desconhecida (veio de uma beta antiga, de outro canal): mostra o
# que mudou NESTA versao. Devolver vazio aqui travava o recurso para sempre
# na maquina — sem faixa, `vista` nunca era atualizada (revisao 2026-09-07).
de_fora = novidades.pendentes(uma, "0.0.1-desconhecida")
assert de_fora == tuple(e for e in TABELA if e[0] == uma), de_fora
if len(versoes) > 1:
    devidas = novidades.pendentes(uma, outra)
    assert len(devidas) == len(versoes) - 1, devidas
    assert devidas[0][0] == uma, "a mais nova vem primeiro"
    # Voltar para uma versao antiga nao pode fazer chover novidade.
    assert novidades.pendentes(outra, uma) == ()
print("PASS: pendentes so fala quando tem certeza")

# --------------------------------------------------------- os dois textos
resumo = novidades.resumo(TABELA[:1])
assert resumo and len(resumo) < 200 and "e mais" in resumo, resumo
texto = novidades.texto_markdown(__version__)
assert texto.startswith("# O que mudou na versão"), texto[:60]
assert "## Antes disso" in texto or len(versoes) == 1
desconhecida = novidades.texto_markdown("9.9.9")
assert "9.9.9" in desconhecida, "versao fora da tabela ainda tem de dizer algo"
print("PASS: resumo de uma linha e a pagina da aba Novidades")

# --------------------------------------------------------------- lingua
# Mesmas regras de tests/toy_ui_textos.py — isto vai para a tela.
PROIBIDOS = re.compile(
    r"\b(QC|manifesto|canonical|merge|fundir|Infocitizen)\b"
    r"|menu\s+(Transcrever|Arquivo)\b"
    r"|\bArquivo\s*(→|->|>)\s", re.I)
IA_SOLTA = re.compile(r"\bIA\b")
SEM_ACENTO = re.compile(
    r"\b(transcricao|transcricoes|midia|midias|rotulo|rotulos|nao|voce|"
    r"exportacao|documentacao|revisao|separacao|audio|audios|video|videos|"
    r"proxima|proximo|usuario|possivel|instalacao|analise|analises)\b", re.I)
MOJIBAKE = re.compile(r"[ÃÂ][\x80-\xbf€™œ£§]|Ã[a-z]?Â")

problemas: list[str] = []
for versao, itens in TABELA:
    for item in itens:
        plano = unicodedata.normalize("NFC", item)
        for nome, regra in (("proibido", PROIBIDOS), ("IA solta", IA_SOLTA),
                            ("sem acento", SEM_ACENTO), ("mojibake", MOJIBAKE)):
            if regra.search(plano):
                problemas.append(f"{versao} ({nome}): {plano[:70]}")
        if "..." in plano:
            problemas.append(f"{versao} (reticencias ASCII): {plano[:70]}")
assert not problemas, "guia verbal violado:\n  " + "\n  ".join(problemas)
print("PASS: as novidades seguem o mesmo guia verbal da interface")

assert "PySide6" not in sys.modules, "novidades.py tem de continuar puro"
print("PASS: toy_novidades")
