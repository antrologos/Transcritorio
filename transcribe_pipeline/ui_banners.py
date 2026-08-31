"""BannerArea (Programa R, R2): slot unico de banner com prioridade.

Antes, tres banners (vozes / separacao falhou / trocas suspeitas)
empilhavam acima da tabela de blocos — ate tres faixas simultaneas
brigando por atencao. O dossie RD define UM slot: cada banner declara
o que QUER (set_wanted) e a area mostra apenas o de maior prioridade;
os demais esperam a vez.

A area e burra: recebe widgets prontos e nao conhece a janela. As
fachadas _update_*_banner da janela continuam sendo o unico lugar que
decide QUANDO cada banner quer aparecer.
"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class BannerArea(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._banners: dict[str, tuple[int, QWidget]] = {}
        self._wanted: dict[str, bool] = {}

    def add_banner(self, chave: str, widget: QWidget, prioridade: int) -> None:
        """Registra um banner (prioridade menor = mais urgente)."""
        self._banners[chave] = (prioridade, widget)
        self._wanted[chave] = False
        widget.setVisible(False)
        self._layout.addWidget(widget)

    def set_wanted(self, chave: str, wanted: bool) -> None:
        """Declara se o banner quer aparecer; a area decide quem aparece."""
        if chave not in self._banners:
            return
        self._wanted[chave] = bool(wanted)
        self._apply()

    def wanted(self, chave: str) -> bool:
        return bool(self._wanted.get(chave))

    def _apply(self) -> None:
        vencedor: str | None = None
        melhor = None
        for chave, (prioridade, _w) in self._banners.items():
            if self._wanted[chave] and (melhor is None or prioridade < melhor):
                melhor = prioridade
                vencedor = chave
        for chave, (_p, widget) in self._banners.items():
            widget.setVisible(chave == vencedor)
        self.setVisible(vencedor is not None)
