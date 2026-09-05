"""O que mudou, para quem JA usa o Transcritório.

Quem instala pela primeira vez descobre o programa explorando. Quem ja
usa, nao: a pessoa aprendeu um caminho, ele funciona, e ela nunca mais
abre os menus para ver se apareceu coisa nova. Sem uma superficie de
novidades, um recurso feito ontem simplesmente nao existe para ela.

Isto NAO substitui o CHANGELOG.md, que continua sendo o registro longo.
Aqui ficam 3 a 6 linhas por versao, em lingua de usuario, e cada uma diz
ONDE achar a coisa — porque "existe agora" sem "fica aqui" nao ajuda.

Modulo puro (sem Qt e sem I/O): viaja em qualquer canal de distribuicao e
e importavel direto pelo teste.
"""
from __future__ import annotations

# Mais NOVA primeiro. A ordem de insercao e o que resolve "esta pessoa
# pulou tres versoes" — sem comparar numeros de versao, que e onde essas
# coisas costumam quebrar (0.3.0b1 contra 0.3.0, por exemplo).
NOVIDADES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("0.3.0b1", (
        "A tecla F1 abre a lista de tudo o que o programa faz, com o atalho de cada "
        "comando e onde ele fica. Dá para buscar por nome ou por tecla, e imprimir "
        "(Ajuda → Atalhos e comandos).",
        "Ajuda → Como usar o Transcritório traz o manual dentro do programa, do "
        "projeto novo até a exportação. Funciona sem internet.",
        "Alt+P passa o fim de um bloco para o bloco seguinte, com o falante de lá — "
        "é o conserto de quando a separação de vozes errou a fronteira. Antes eram "
        "cinco gestos; agora são dois. Alt+Shift+P faz o contrário.",
        "Juntar blocos de falantes diferentes deixou de ser recusado: agora junta, "
        "adota o falante do bloco de cima e diz qual ficou.",
        "Em computadores sem placa de vídeo, o lote ficou mais rápido, e a estimativa "
        "de tempo parou de dizer que separar as vozes era a etapa demorada — não é.",
    )),
    ("0.2.8", (
        "Durante um lote, uma faixa na lista explica o que está acontecendo, e clicar "
        "numa ação de análise diz por que ela está esperando, em vez de nada acontecer.",
        "A separação de vozes em lote passou a usar um processo só para o lote inteiro, "
        "em vez de um por arquivo — bem mais rápido em computadores com placa de vídeo.",
        "Ao fim de um lote, uma faixa avisa quais entrevistas ainda estão com vozes por "
        "identificar, e leva direto à pergunta de quem é cada voz.",
    )),
)


def _versoes() -> list[str]:
    return [versao for versao, _itens in NOVIDADES]


def pendentes(atual: str, vista: str | None):
    """As novidades entre a versao ja vista e a de agora.

    Devolve vazio — de proposito — em todo caso duvidoso: primeira
    execucao (nada visto), mesma versao, ou versao que nao esta na tabela
    (builds de desenvolvimento entre releases). Melhor nao avisar do que
    avisar errado.
    """
    if not vista:
        return ()
    versoes = _versoes()
    if atual not in versoes or vista not in versoes:
        return ()
    aqui, antes = versoes.index(atual), versoes.index(vista)
    if antes <= aqui:
        return ()  # a versao vista e a mesma ou mais nova
    return NOVIDADES[aqui:antes]


def resumo(entradas) -> str:
    """Uma linha para a faixa: a novidade principal e quantas mais."""
    itens = [item for _versao, lista in entradas for item in lista]
    if not itens:
        return ""
    primeiro = itens[0].split(" (")[0].rstrip(".")
    if len(itens) == 1:
        return primeiro
    return f"{primeiro} — e mais {len(itens) - 1}"


def texto_markdown(atual: str) -> str:
    """A aba "Novidades" da janela de ajuda."""
    versoes = dict(NOVIDADES)
    partes: list[str] = []
    itens = versoes.get(atual)
    if itens:
        partes.append(f"# O que mudou na versão {atual}")
        partes.append("")
        partes.extend(f"- {item}" for item in itens)
    else:
        partes.append("# Novidades")
        partes.append("")
        partes.append(
            f"Esta é a versão {atual}. A lista de mudanças desta versão ainda não foi "
            "escrita aqui; ela está no histórico completo, no site.")
    anteriores = [(v, lista) for v, lista in NOVIDADES if v != atual]
    if anteriores:
        partes.append("")
        partes.append("## Antes disso")
        for versao, lista in anteriores:
            partes.append("")
            partes.append(f"**Versão {versao}**")
            partes.append("")
            partes.extend(f"- {item}" for item in lista)
    return "\n".join(partes)
