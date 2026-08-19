"""Persian text normalization.

Symbol names arrive from different sources spelled differently: Arabic yeh
(ي U+064A) vs Persian yeh (ی U+06CC), Arabic kaf (ك U+0643) vs Persian kaf
(ک U+06A9), and zero-width non-joiners scattered through company names.

Joining a snapshot to a universe list without folding these produces no error
-- just an empty join, which is far worse than a crash. Every symbol crossing
a module boundary goes through normalize().
"""

import re

# Arabic -> Persian letter folding.
FOLD = {
    "ي": "ی",  # ARABIC YEH        -> FARSI YEH
    "ى": "ی",  # ALEF MAKSURA      -> FARSI YEH
    "ك": "ک",  # ARABIC KAF        -> KEHEH
    "ة": "ه",  # TEH MARBUTA       -> HEH
    "أ": "ا",  # ALEF WITH HAMZA   -> ALEF
    "إ": "ا",
    "آ": "ا",  # ALEF WITH MADDA   -> ALEF
    "ؤ": "و",  # WAW WITH HAMZA    -> WAW
    "ئ": "ی",  # YEH WITH HAMZA    -> FARSI YEH
}

# Zero-width and directional marks that carry no meaning for matching.
INVISIBLE = dict.fromkeys(map(ord, "​‌‍‎‏﻿"), None)

# Arabic-Indic and Persian digits -> ASCII.
DIGITS = {}
for offset, base in ((0x0660, "0"), (0x06F0, "0")):
    for i in range(10):
        DIGITS[chr(offset + i)] = chr(ord(base) + i)

_TRANSLATION = str.maketrans({**FOLD, **DIGITS})

_WHITESPACE = re.compile(r"\s+")


def normalize(text):
    """Fold a Persian string to a canonical, joinable form."""

    if text is None:
        return ""

    if not isinstance(text, str):
        text = str(text)

    text = text.translate(INVISIBLE)
    text = text.translate(_TRANSLATION)
    text = _WHITESPACE.sub(" ", text)

    return text.strip()


def normalize_symbol(symbol):
    """Normalize a ticker (نماد). Spaces are never meaningful inside one."""

    return normalize(symbol).replace(" ", "")


def is_rights(symbol, name=""):
    """حق تقدم -- a rights issue, not the underlying share."""

    symbol = normalize_symbol(symbol)
    name = normalize(name)

    return symbol.endswith("ح") or name.startswith("ح ") or name.startswith("ح.")
