"""Translation helpers (stub, replaced by the M6-01 implementation).

Signatures are fixed by docs/ARCHITECTURE.md; other modules may already
``from .i18n import tr, tr_noop``.
"""

from __future__ import annotations


def tr(text: str) -> str:
    """Translate ``text`` into the current plugin language."""
    return text


def tr_noop(text: str) -> str:
    """Mark ``text`` for translation without translating it now."""
    return text
