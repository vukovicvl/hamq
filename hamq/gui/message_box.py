"""Message boxes whose standard buttons are named in the HamQ language.

Qt names the standard buttons of ``QMessageBox`` ("&Yes", "&No", "OK", "Cancel") in the
language of QGIS and has no Serbian translation of them, so the static
``QMessageBox.question()`` and ``QMessageBox.about()`` show English buttons in a Serbian
HamQ. :class:`MessageBox` has the same two static functions with the buttons named
through :func:`hamq.core.i18n.tr`. A module can import it as ``QMessageBox``: the calls
stay ``QMessageBox.question(...)`` and ``QMessageBox.about(...)``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtWidgets import QMessageBox, QWidget

from ..core.i18n import tr, tr_noop
from ..qgis_io import compat

__all__ = ["BUTTON_TEXTS", "MessageBox", "name_buttons"]

#: Standard buttons and their (untranslated) names; translated when a box is made.
BUTTON_TEXTS: tuple[tuple[Any, str], ...] = (
    (compat.MSGBOX_OK, tr_noop("OK")),
    (compat.MSGBOX_CANCEL, tr_noop("Cancel")),
    (compat.MSGBOX_YES, tr_noop("Yes")),
    (compat.MSGBOX_NO, tr_noop("No")),
)
_ABOUT_ICON_SIZE = QSize(64, 64)  # what QMessageBox.about() uses


def name_buttons(box: QMessageBox, texts: Mapping[Any, str] | None = None) -> None:
    """Name the standard buttons of ``box`` in the HamQ language.

    ``texts`` maps standard buttons to names that are already translated, e.g.
    ``{MSGBOX_YES: tr("Turn")}``; they replace the general names.
    """
    for button, text in BUTTON_TEXTS:
        widget = box.button(button)
        if widget is not None:
            widget.setText(tr(text))
    for button, text in (texts or {}).items():
        widget = box.button(button)
        if widget is not None:
            widget.setText(text)


class MessageBox(QMessageBox):
    """``QMessageBox`` whose static ``question()`` and ``about()`` name their buttons in
    the HamQ language."""

    @staticmethod
    def question(
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: Any = compat.MSGBOX_YES | compat.MSGBOX_NO,
        default_button: Any = compat.MSGBOX_NO,
        *,
        button_texts: Mapping[Any, str] | None = None,
    ) -> Any:
        """Ask like ``QMessageBox.question()``; returns the standard button clicked.

        ``text`` is plain text. Escape means No (or Cancel when the box has it);
        ``MSGBOX_CANCEL`` comes back when the box closed without any button.
        ``button_texts`` names single buttons, e.g. ``{MSGBOX_YES: tr("Turn")}``.
        """
        box = MessageBox(parent)
        try:
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle(title)
            box.setTextFormat(compat.TEXT_PLAIN)
            box.setText(text)
            box.setStandardButtons(buttons)
            name_buttons(box, button_texts)
            if box.button(default_button) is not None:
                box.setDefaultButton(default_button)
            for escape in (compat.MSGBOX_CANCEL, compat.MSGBOX_NO):
                if box.button(escape) is not None:
                    box.setEscapeButton(escape)
                    break
            box.exec()
            clicked = box.clickedButton()
            # exec() returns a plain int: the clicked button tells the answer (PyQt6 enums)
            return box.standardButton(clicked) if clicked is not None else compat.MSGBOX_CANCEL
        finally:
            box.deleteLater()

    @staticmethod
    def about(parent: QWidget | None, title: str, text: str) -> None:
        """Show ``text`` (rich text allowed) like ``QMessageBox.about()``: the window
        icon of ``parent`` and one OK button."""
        box = MessageBox(parent)
        try:
            box.setWindowTitle(title)
            box.setText(text)
            icon = box.windowIcon()
            box.setIconPixmap(icon.pixmap(icon.actualSize(_ABOUT_ICON_SIZE)))
            box.setStandardButtons(compat.MSGBOX_OK)
            name_buttons(box)
            box.exec()
        finally:
            box.deleteLater()
