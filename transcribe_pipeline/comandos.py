"""Catalogo de comandos: o que o app faz, lido do PROPRIO menu.

Por decisao do Programa R, a barra de menus e o catalogo completo do
Transcritorio, e cada acao ja carrega um tooltip curado (guardado pelo
gate tests/toy_ui_textos.py). Entao a tela de consulta nao precisa de uma
segunda lista escrita a mao — ela e GERADA daqui, percorrendo o menu vivo.
Nao existe o que desatualizar.

Modulo PURO: nenhum import de PySide6, na mesma disciplina de ui_tokens
(o toy confere isso ao final). A varredura e escrita contra um protocolo
por duck-typing — isSeparator/isVisible/text/toolTip/shortcuts/menu/
property — e nao contra QMenu, para que o teste possa alimenta-la com
objetos falsos e cobrir todos os casos sem construir uma janela.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Separador do caminho de menu. E o mesmo simbolo ja usado nos textos da
# UI ("Ferramentas → Gerenciar modelos…" aparece em varias mensagens),
# mas aqui em versao fina porque o caminho e hierarquia, nao um passo.
SETA = " › "  # ›

# Sinonimos de tecla para a busca: quem procura o atalho de voltar digita
# "esquerda" ou cola "←", e o Qt escreve "Alt+Left".
_SETAS = {
    "left": ("←", "esquerda"),
    "right": ("→", "direita"),
    "up": ("↑", "cima"),
    "down": ("↓", "baixo"),
}


@dataclass(frozen=True)
class Comando:
    """Uma linha do catalogo."""

    caminho: str                      # "Editar › Bloco e reprodução"
    rotulo: str                       # "Juntar com próximo"
    atalhos: tuple[str, ...]          # ("Alt+J",) — forma portavel, para busca e cola
    atalhos_exibidos: tuple[str, ...]  # forma nativa (no macOS vira ⌥J)
    dica: str                         # "" quando a acao nao tem tooltip proprio
    chave: bool = False               # liga/desliga (isCheckable)


def normalizar(texto: str) -> str:
    """Sem acento e sem caixa — para a busca casar 'atalho' com 'Atalho'."""
    decomposto = unicodedata.normalize("NFD", texto or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return sem_acento.casefold()


def _limpar_rotulo(texto: str) -> str:
    """Tira o '&' de mnemonico do Qt, preservando '&&' literal."""
    return (texto or "").replace("&&", "\x00").replace("&", "").replace("\x00", "&")


def _sem_enfeite(texto: str) -> str:
    """Rotulo cru, para comparar com o tooltip que o Qt inventa.

    Sem tooltip proprio o Qt devolve o texto da acao SEM o mnemonico e SEM
    as reticencias do fim — "Verificar atualizações…" vira tooltip
    "Verificar atualizações". Comparar sem essa limpeza deixava passar uma
    "explicacao" que so repete o nome.
    """
    limpo = _limpar_rotulo(texto).strip()
    for fim in ("…", "..."):
        if limpo.endswith(fim):
            limpo = limpo[: -len(fim)]
    return limpo.strip()


def _escrever(seq, fmt) -> str:
    return seq.toString() if fmt is None else seq.toString(fmt)


def _dica_de(acao) -> str:
    """O tooltip ORIGINAL da acao.

    update_action_states reescreve o tooltip acrescentando o motivo de a
    acao estar cinza (_set_action), guardando o original na property
    'tooltip_base' — e o original e que serve de explicacao no catalogo.
    Quando ninguem definiu tooltip, o Qt devolve toolTip() == text(); a
    explicacao entao e vazia, e nao o proprio nome repetido.
    """
    texto = acao.text() or ""
    base = acao.property("tooltip_base")
    dica = str(base) if base else (acao.toolTip() or "")
    # A comparacao vale TAMBEM para o tooltip_base: _set_action guarda ali
    # o que toolTip() devolvia — e numa acao sem tooltip proprio isso e o
    # proprio nome. Sem esta linha, "Desfazer" ganharia a explicacao
    # "Desfazer".
    if _sem_enfeite(dica) == _sem_enfeite(texto):
        return ""
    return dica


def percorrer_menu(barra, caminho: str = "", *, fmt_portavel=None,
                   fmt_nativo=None) -> list[Comando]:
    """Todos os comandos do menu, em ordem de leitura.

    Os formatos de atalho chegam de fora (QKeySequence.SequenceFormat)
    para o modulo continuar sem Qt; sem eles, usa-se toString() cru.
    """
    achados: list[Comando] = []
    for acao in barra.actions():
        if acao.isSeparator():
            continue
        if not acao.isVisible():
            # busy_menu_hint_action: existe no menu Analisar so enquanto
            # um lote roda. Estado, nao comando.
            continue
        submenu = acao.menu()
        if submenu is not None:
            # Listas montadas em tempo de execucao (Projetos recentes)
            # nao sao comandos a aprender. Marcadas com uma property, e
            # nao pelo nome: a proxima lista dinamica ja nasce coberta.
            if acao.property("catalogo_ignorar"):
                continue
            rotulo_sub = _limpar_rotulo(acao.text())
            adiante = f"{caminho}{SETA}{rotulo_sub}" if caminho else rotulo_sub
            achados.extend(percorrer_menu(submenu, adiante,
                                          fmt_portavel=fmt_portavel,
                                          fmt_nativo=fmt_nativo))
            continue
        atalhos = tuple(_escrever(s, fmt_portavel) for s in acao.shortcuts())
        exibidos = tuple(_escrever(s, fmt_nativo) for s in acao.shortcuts())
        achados.append(Comando(
            caminho=caminho,
            rotulo=_limpar_rotulo(acao.text()),
            atalhos=tuple(a for a in atalhos if a),
            atalhos_exibidos=tuple(a for a in exibidos if a),
            dica=_dica_de(acao),
            chave=bool(acao.isCheckable()),
        ))
    return achados


def _palheiro(cmd: Comando) -> str:
    """Tudo por onde a busca pode achar este comando, ja normalizado."""
    partes = [cmd.caminho, cmd.rotulo, cmd.dica]
    partes.extend(cmd.atalhos)
    partes.extend(cmd.atalhos_exibidos)
    for atalho in cmd.atalhos:
        # "altp" acha "Alt+P": quem digita a tecla raramente digita o '+'.
        partes.append(atalho.replace("+", "").replace(" ", ""))
        baixo = atalho.casefold()
        for nome, sinonimos in _SETAS.items():
            if nome in baixo:
                partes.extend(sinonimos)
    return normalizar(" ".join(partes))


def filtrar(comandos, termo: str) -> list[Comando]:
    """Comandos que casam com TODAS as palavras do termo."""
    alvos = normalizar(termo).split()
    if not alvos:
        return list(comandos)
    return [c for c in comandos
            if all(alvo in _palheiro(c) for alvo in alvos)]


def texto_da_cola(comandos, versao: str = "", data: str = "") -> str:
    """A lista em texto puro, para copiar, salvar ou imprimir.

    So a PRIMEIRA linha da explicacao: os tooltips das acoes de AI tem
    seis linhas e arrebentariam o alinhamento de uma cola de parede.
    """
    linhas: list[str] = ["Transcritório — atalhos e comandos"]
    marca = " · ".join(p for p in (f"versão {versao}" if versao else "", data) if p)
    if marca:
        linhas.append(marca)
    linhas.append("")

    lista = list(comandos)
    largura = max((len(c.rotulo) for c in lista), default=0)
    largura = min(max(largura, 12), 44)
    atalho_w = max((len(" / ".join(c.atalhos)) for c in lista), default=0)
    atalho_w = min(max(atalho_w, 6), 20)

    caminho_atual = None
    for cmd in lista:
        if cmd.caminho != caminho_atual:
            caminho_atual = cmd.caminho
            linhas.append("")
            linhas.append((caminho_atual or "Outros comandos").upper())
        atalho = " / ".join(cmd.atalhos) or "—"
        primeira = cmd.dica.split("\n")[0].strip()
        linha = f"  {cmd.rotulo:<{largura}}  {atalho:<{atalho_w}}"
        if primeira:
            linha = f"{linha}  {primeira}"
        linhas.append(linha.rstrip())
    linhas.append("")
    return "\n".join(linhas)
