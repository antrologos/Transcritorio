"""A volta guiada: os passos, em texto, e onde cada um aponta.

Nao e um assistente modal. O FirstRunWizard instala componentes; esta
volta ensina a JANELA, e por isso precisa que a janela esteja viva: cada
passo aponta um lugar real (um halo em volta do widget) enquanto explica em
duas ou tres linhas o que ele faz. E oferecida uma vez, por uma faixa, na
primeira abertura com projeto — e fica em Ajuda → Volta guiada para sempre.

Modulo PURO (sem Qt): o teste confere textos e alvos sem construir janela;
a apresentacao mora em ui_tour.py. `alvo` e o NOME do atributo da janela
principal ("menuBar" e o caso especial do metodo). Passos que so fazem
sentido com uma entrevista aberta trazem `precisa_entrevista=True`: com o
Estudio fechado, o halo cai sobre a lista e o texto ganha a frase de
`reserva`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Passo:
    titulo: str
    texto: str
    alvo: str                        # atributo da janela (ou "menuBar")
    aba: int | None = None           # indice em review_tabs, quando o alvo mora numa aba
    precisa_entrevista: bool = False
    alvo_reserva: str = "interview_table"
    reserva: str = "Abra uma entrevista com duplo clique na lista para ver esta parte."


PASSOS: tuple[Passo, ...] = (
    Passo(
        "Comece pelas gravações",
        "Arraste áudios ou vídeos para a janela, ou use este botão. Os arquivos não são "
        "copiados: o projeto só aponta para onde eles estão, e nunca os altera.",
        alvo="_media_button_ref",
    ),
    Passo(
        "Transcrever",
        "Marque na lista o que quer transcrever e clique aqui. Sem nada marcado, ele faz "
        "todos os que ainda não têm texto. A setinha ao lado escolhe o motor e liga ou "
        "desliga a separação de falantes.",
        alvo="transcribe_button",
    ),
    Passo(
        "A lista do projeto",
        "Cada linha é uma gravação. A caixa de marcação escolhe o que transcrever; o duplo "
        "clique abre a entrevista para revisar. F2 renomeia o rótulo, e Del manda para a "
        "Lixeira — com Ctrl+Z para voltar atrás.",
        alvo="interview_table",
    ),
    Passo(
        "O Estúdio de Revisão",
        "A onda sonora em cima, a lista de blocos no meio, o texto do bloco embaixo. Clique "
        "no tempo de um bloco para ouvir dali; corrija direto no texto — trocar de bloco já "
        "salva.",
        alvo="turn_table", aba=0, precisa_entrevista=True,
    ),
    Passo(
        "Sem tirar a mão do teclado",
        "F4 toca e pausa, F3 repete o bloco, Alt+↓ vai ao próximo levando o áudio junto. "
        "Alt+P conserta uma fronteira de falante errada num gesto só. F1 lista todas as "
        "teclas.",
        alvo="text_edit", aba=0, precisa_entrevista=True,
    ),
    Passo(
        "As vozes",
        "Ao abrir uma transcrição nova, o programa toca uma amostra de cada voz e pergunta "
        "de quem é. Aqui você troca o falante de um bloco; Alt+E abre esta lista pelo "
        "teclado.",
        alvo="speaker_combo", aba=0, precisa_entrevista=True,
    ),
    Passo(
        "Documentos e exportação",
        "Tudo o que o programa produz aparece nesta aba e fica na pasta Resultados do "
        "projeto: .docx e .md para ler, legendas, planilha. Exportar… (Ctrl+E) gera os "
        "arquivos finais.",
        alvo="docs_panel", aba=1, precisa_entrevista=True,
    ),
    Passo(
        "Quando precisar de ajuda",
        "F1 lista tudo o que o programa faz, com as teclas. Ajuda → Como usar o "
        "Transcritório traz o manual, sem internet. Ajuda → Novidades desta versão diz o "
        "que mudou. E esta volta fica em Ajuda → Volta guiada.",
        alvo="menuBar",
    ),
)


def total() -> int:
    return len(PASSOS)


def rotulo_contador(indice: int) -> str:
    """'3 de 8' — o que a pessoa ve no painel."""
    return f"{indice + 1} de {total()}"
