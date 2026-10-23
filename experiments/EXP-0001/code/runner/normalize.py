"""Text normalization for the leak and marker checks."""

from __future__ import annotations

import unicodedata

_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x0000])


def normalize(text: str) -> str:
    """NFKC, then drop zero-width characters and NUL; NBSP becomes a plain space (NFKC does that)."""
    return unicodedata.normalize("NFKC", text).replace(" ", " ").translate(_ZERO_WIDTH)
