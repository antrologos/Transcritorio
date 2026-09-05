"""Toy: o manual embutido existe, ensina o caminho e fala a lingua da UI — 2026-09-05.

Ate 2026-09-05 o menu Ajuda tinha um item "Documentação" que procurava um
README_transcricoes.md que NENHUMA parte do codigo gera: no caso comum ele
respondia "a documentacao nao foi encontrada nesta pasta". O item de menu
mais obvio para quem esta perdido era um beco.

O manual agora viaja no wheel (assets/manual.md) e abre sem internet. Como
e prosa escrita a mao, ele precisa de duas guardas: esta, que confere a
lingua e a estrutura, e a de toy_help_window, que confere se os caminhos
de menu citados existem de verdade na barra de menus.

Puro: so leitura de arquivo e expressoes regulares. Sem Qt.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MANUAL = RAIZ / "transcribe_pipeline" / "assets" / "manual.md"

assert MANUAL.exists(), f"o manual embutido sumiu: {MANUAL}"
texto = MANUAL.read_text(encoding="utf-8")
assert len(texto) > 1500, "manual curto demais para ensinar o caminho inteiro"
print(f"PASS: manual embutido presente ({len(texto)} caracteres)")

# ------------------------------------------------------------- estrutura
# A jornada inteira, na ordem. Um manual que nao chega ao fim deixa a
# pessoa com arquivos que ela nao sabe onde foram parar.
titulos = [ln.strip() for ln in texto.splitlines() if ln.startswith("## ")]
assert len(titulos) >= 7, titulos
esperados = ["projeto", "grava", "Transcrever", "voz", "Revisar", "Analisar", "Exportar"]
for palavra in esperados:
    assert any(palavra.lower() in t.lower() for t in titulos), \
        f"o manual nao tem secao sobre {palavra!r}: {titulos}"
print(f"PASS: {len(titulos)} secoes cobrindo a jornada do comeco ao fim")

# A frase unica que existia em Ajuda > Fluxo de trabalho foi ABSORVIDA
# aqui — o item de menu saiu, o conteudo nao podia sair junto.
primeiro = texto.split("## ")[0]
for passo in ("Adicionar mídia", "Transcrever", "Salvar transcrição", "Exportar"):
    assert passo in primeiro, f"o caminho basico perdeu {passo!r}"
print("PASS: o caminho basico abre o manual (era a frase do Fluxo de trabalho)")

# O ciclo do Estudio e o que a pessoa mais precisa aprender: e onde ela
# passa as horas. Os atalhos do conserto de fronteira sao os menos
# descobriveis do app.
for tecla in ("F4", "F3", "Alt+D", "Alt+J", "Alt+P", "Alt+E", "Ctrl+Z", "F1"):
    assert tecla in texto, f"o manual nao ensina {tecla}"
print("PASS: o manual ensina o ciclo de revisao pelo teclado")

# A promessa central do projeto tem de estar escrita.
assert "computador" in texto and "internet" in texto, \
    "o manual precisa dizer que o processamento e local"
print("PASS: o manual diz que nada sai do computador")

# --------------------------------------------------------------- lingua
# Mesmas regras de tests/toy_ui_textos.py (secao "regras"). Copiadas em
# vez de importadas: aquele teste constroi um QApplication inteiro.
PROIBIDOS = re.compile(
    r"\b(QC|manifesto|canonical|merge|fundir|Infocitizen)\b"
    r"|menu\s+(Transcrever|Arquivo)\b"
    r"|\bTranscrever\s*(→|->|&gt;|>)"
    r"|\bArquivo\s*(→|->|&gt;|>)\s"
    r"|Reprocessar falantes"
    r"|Atualizar transcri[cç][aã]o edit[aá]vel",
    re.I)
MOJIBAKE = re.compile(r"[ÃÂ][\x80-\xbf€™œ£§]|Ã[a-z]?Â")
IA_SOLTA = re.compile(r"\bIA\b")
SEM_ACENTO = re.compile(
    r"\b(transcricao|transcricoes|midia|midias|rotulo|rotulos|"
    r"exportacao|exportacoes|documentacao|creditos|comecar|exclusao|"
    r"configuracao|configuracoes|nao|voce|atencao|concluido|concluida|"
    r"revisao|separacao|identificacao|audio|audios|"
    r"video|videos|ultima|ultimo|proxima|proximo|numero|pagina|"
    r"usuario|usuarios|orfao|orfaos|espaco|indisponivel|possivel|"
    r"aceleracao|instalacao|permissao|privilegios)\b",
    re.I)

violacoes: list[str] = []
for numero, linha in enumerate(texto.splitlines(), 1):
    plano = unicodedata.normalize("NFC", linha)
    for nome, regra in (("proibido", PROIBIDOS), ("mojibake", MOJIBAKE),
                        ("IA solta", IA_SOLTA), ("sem acento", SEM_ACENTO)):
        if regra.search(plano):
            violacoes.append(f"linha {numero} ({nome}): {plano[:70]}")
    # Reticencias ASCII: so fora dos links, onde "..." nao aparece mesmo.
    if "..." in plano:
        violacoes.append(f"linha {numero} (reticencias ASCII): {plano[:70]}")
assert not violacoes, "guia verbal violado no manual:\n  " + "\n  ".join(violacoes)
print("PASS: o manual segue o mesmo guia verbal da interface")

# O link do site tem de ser o do projeto, e um so — manual com link morto
# e pior que manual sem link.
links = re.findall(r"https?://[^\s)]+", texto)
assert links, "o manual precisa apontar para o manual completo no site"
for link in links:
    assert link.startswith("https://antrologos.github.io/Transcritorio/"), link
print(f"PASS: {len(links)} link(s), todos para o site do Transcritório")

print("PASS: toy_manual")
sys.exit(0)
