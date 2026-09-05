"""Toy: o catalogo de comandos lido do menu — 2026-09-05.

O Estudio tem 19 acoes com atalho e o app inteiro passa de 60 comandos, e
ate hoje nao havia NENHUMA tela onde consulta-los (zero ocorrencias de
"Atalhos" na UI). A tela nova nao mantem uma segunda lista: ela e gerada
da barra de menus viva, que por regra do projeto ja e o catalogo completo.

Este toy cobre a varredura inteira SEM Qt, com objetos falsos — por isso o
modulo comandos.py e puro, e a ultima linha aqui confere que continua puro.

Puro: so `comandos`. Sem PySide6.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import comandos  # noqa: E402


# --------------------------------------------------------------- dublês
class FalsoAtalho:
    """Imita QKeySequence: toString() cru e toString(formato)."""

    def __init__(self, portavel: str, nativo: str = "") -> None:
        self._portavel = portavel
        self._nativo = nativo or portavel

    def toString(self, fmt=None) -> str:
        return self._nativo if fmt == "nat" else self._portavel


class FalsaAcao:
    def __init__(self, texto="", dica=None, atalhos=(), submenu=None,
                 separador=False, visivel=True, checavel=False, props=None):
        self._texto = texto
        self._dica = dica
        self._atalhos = list(atalhos)
        self._submenu = submenu
        self._separador = separador
        self._visivel = visivel
        self._checavel = checavel
        self._props = dict(props or {})

    def isSeparator(self): return self._separador
    def isVisible(self): return self._visivel
    def text(self): return self._texto
    # Regra do Qt: sem tooltip proprio, toolTip() devolve o proprio text().
    def toolTip(self): return self._texto if self._dica is None else self._dica
    def shortcuts(self): return list(self._atalhos)
    def menu(self): return self._submenu
    def isCheckable(self): return self._checavel
    def property(self, nome): return self._props.get(nome)


class FalsoMenu:
    def __init__(self, acoes): self._acoes = list(acoes)
    def actions(self): return list(self._acoes)


# --------------------------------------------------------------- a arvore
bloco = FalsoMenu([
    FalsaAcao("Juntar com próximo", "Junta este bloco ao seguinte. (Alt+J)",
              [FalsoAtalho("Alt+J", "⌥J")]),
    FalsaAcao(separador=True),
    FalsaAcao("Voltar 5 segundos", "Volta 5 segundos. (Alt+←)",
              [FalsoAtalho("Ctrl+Left", "⌃←"), FalsoAtalho("Alt+Left", "⌥←")]),
])
recentes = FalsoMenu([FalsaAcao("D:/projeto-de-ontem"), FalsaAcao("D:/outro")])
raiz = FalsoMenu([
    FalsaAcao("Editar", submenu=FalsoMenu([
        FalsaAcao("Desfazer", None, [FalsoAtalho("Ctrl+Z", "⌘Z")]),
        FalsaAcao("Bloco e reprodução", submenu=bloco),
    ])),
    FalsaAcao("Projetos recentes", submenu=recentes,
              props={"catalogo_ignorar": True}),
    FalsaAcao("Aguardando o lote…", "só enquanto roda", visivel=False),
    FalsaAcao("Separar falantes", "Identifica quem está falando.", checavel=True),
    FalsaAcao("Transcrever selecionados",
              props={"tooltip_base": "O tooltip original."},
              dica="O tooltip original.\n(Selecione um arquivo na lista.)"),
    # Acao sem tooltip proprio que JA passou por _set_action: o
    # tooltip_base guardado e o que toolTip() devolvia, isto e, o proprio
    # nome. Nao pode virar explicacao.
    FalsaAcao("Renomear rótulo…", props={"tooltip_base": "Renomear rótulo…"}),
    # O tooltip que o Qt inventa vem SEM as reticencias do nome. Sem
    # normalizar, "Verificar atualizações" passaria por explicacao.
    FalsaAcao("Verificar atualizações…", "Verificar atualizações"),
])

achados = comandos.percorrer_menu(raiz, fmt_portavel="port", fmt_nativo="nat")
por_rotulo = {c.rotulo: c for c in achados}

# --- separador, invisivel e lista dinamica ficam de fora ------------------
assert "Aguardando o lote…" not in por_rotulo, "acao invisivel nao e comando"
assert not [c for c in achados if c.rotulo.startswith("D:/")], \
    "Projetos recentes e lista dinamica, nao catalogo"
assert len(achados) == 7, [c.rotulo for c in achados]
print("PASS: separador, acao invisivel e submenu dinamico ficam fora")

# --- o caminho do menu ensina ONDE achar ---------------------------------
assert por_rotulo["Juntar com próximo"].caminho == "Editar › Bloco e reprodução"
assert por_rotulo["Desfazer"].caminho == "Editar"
assert por_rotulo["Separar falantes"].caminho == "", "acao de menu-raiz sem caminho"
print("PASS: caminho de menu montado na recursao")

# --- atalhos nas duas formas ---------------------------------------------
voltar = por_rotulo["Voltar 5 segundos"]
assert voltar.atalhos == ("Ctrl+Left", "Alt+Left"), voltar.atalhos
assert voltar.atalhos_exibidos == ("⌃←", "⌥←"), voltar.atalhos_exibidos
assert por_rotulo["Separar falantes"].atalhos == (), "sem atalho e caso normal"
assert por_rotulo["Separar falantes"].chave is True, "checavel = liga/desliga"
print("PASS: atalhos em forma portavel e nativa; acao sem atalho entra igual")

# --- a explicacao ---------------------------------------------------------
# Sem tooltip proprio o Qt devolve o proprio texto: explicacao vazia, e nao
# o nome repetido (mesma regra que toy_ui_textos ja aplica).
assert por_rotulo["Desfazer"].dica == "", por_rotulo["Desfazer"].dica
# Com a acao cinza, update_action_states anexa o motivo ao tooltip e guarda
# o original em tooltip_base — o catalogo mostra o ORIGINAL.
assert por_rotulo["Transcrever selecionados"].dica == "O tooltip original."
assert por_rotulo["Renomear rótulo…"].dica == "", \
    "tooltip_base pode guardar o proprio nome; isso nao e explicacao"
assert por_rotulo["Verificar atualizações…"].dica == "", \
    "o tooltip que o Qt inventa perde as reticencias; ainda e o proprio nome"
print("PASS: explicacao vem do tooltip original, e e vazia quando nao ha")

# --- busca ----------------------------------------------------------------
def achou(termo):
    return [c.rotulo for c in comandos.filtrar(achados, termo)]


assert achou("") == [c.rotulo for c in achados], "termo vazio devolve tudo"
assert achou("REPRODUCAO") == ["Juntar com próximo", "Voltar 5 segundos"], \
    "busca sem acento e sem caixa, inclusive pelo caminho do menu"
assert achou("altj") == ["Juntar com próximo"], "tecla sem o '+' tem de achar"
assert achou("alt+j") == ["Juntar com próximo"]
assert achou("esquerda") == ["Voltar 5 segundos"], "Left casa com 'esquerda'"
assert achou("←") == ["Voltar 5 segundos"], "Left casa com a seta desenhada"
assert achou("juntar bloco") == ["Juntar com próximo"], "palavras somam (E, nao OU)"
assert achou("jabuticaba") == []
print("PASS: busca por texto, por caminho, por tecla e por seta")

# --- a cola imprimivel ----------------------------------------------------
cola = comandos.texto_da_cola(achados, versao="0.3.0b1", data="2026-09-05")
assert "EDITAR › BLOCO E REPRODUÇÃO" in cola, cola
assert "Alt+J" in cola and "versão 0.3.0b1" in cola
assert "—" in cola, "comando sem atalho aparece com travessao"
# So a primeira linha da explicacao: os tooltips de AI tem 6 linhas.
assert "(Selecione um arquivo na lista.)" not in cola
for linha in cola.splitlines():
    assert linha == linha.rstrip(), "sem espaco sobrando no fim da linha"
print("PASS: cola agrupada por menu, com uma linha de explicacao por comando")

# --- pureza ---------------------------------------------------------------
assert "PySide6" not in sys.modules, \
    "comandos.py tem de continuar puro (sem Qt), como ui_tokens"
print("PASS: comandos.py continua sem Qt")

print("PASS: toy_comandos")
