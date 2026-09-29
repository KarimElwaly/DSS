"""Text normalisation shared by the embedder and the fuzzy re-ranker.

Deliberately generic. It is tempting to strip retail boilerplate ("free
shipping", "latest model", ...) with a hand-written stop-phrase list, but that
would tune the matcher to the exact noise our simulator emits and inflate the
accuracy we report. TF-IDF's inverse-document-frequency weighting already
suppresses terms that appear across many listings, which is the same job done
from the data rather than from a guess.
"""

from __future__ import annotations

import re
import unicodedata

#: "128GB" -> "128 gb", "6.1in" -> "6.1 in". Competitor titles attach units to
#: numbers inconsistently, and the capacity is usually the only token that
#: separates two variants of the same model.
_ATTACHED_UNIT = re.compile(r"(\d)\s*(gb|tb|mb|ghz|mhz|mah|wh|mm|cm|in|hz|w|k)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9.]+")
_STRAY_DOT = re.compile(r"(?<!\d)\.|\.(?!\d)")
_MULTISPACE = re.compile(r"\s+")

#: Removed *before* NFKD, which would otherwise expand them into letters --
#: "Product\u2122" becomes "producttm" and stops matching "Product".
_SYMBOLS = str.maketrans("", "", "\u2122\u00ae\u00a9\u2120")


def normalize(text: str) -> str:
    """Lower-case, de-accent, split attached units, collapse punctuation."""
    if not text:
        return ""
    stripped = text.translate(_SYMBOLS)
    folded = unicodedata.normalize("NFKD", stripped).encode("ascii", "ignore").decode("ascii")
    folded = folded.lower()
    folded = _ATTACHED_UNIT.sub(r"\1 \2", folded)
    folded = _NON_ALNUM.sub(" ", folded)
    # Keep decimal points ("6.1 in") but drop sentence punctuation.
    folded = _STRAY_DOT.sub(" ", folded)
    return _MULTISPACE.sub(" ", folded).strip()
