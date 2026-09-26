"""erase_text.py: when one text repeats an erased text.

Texts are compared after NFKC normalization, case folding and collapsing
whitespace. Two predicates follow from that:

- `repeats_whole`: a row repeats an erased text when it contains the whole
  normalized text. A text under 16 characters must equal the row instead,
  because a short text ("yes", "I am Bo.") turns up inside unrelated rows.
- `repeats_in`: free text such as an audit reason repeats an erased text
  when it holds any 16-character run of it, or the whole text as a word when
  it is shorter than 16 characters.

Both catch verbatim repeats only. A paraphrase, a translation, or a text with
letters spaced apart does not match.
"""
from __future__ import annotations

import re
import unicodedata

RUN = 16


def norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def repeats_whole(erased: str, row: str) -> bool:
    """`erased` and `row` are normalized; True when the row repeats the text."""
    if not erased:
        return False
    if len(erased) >= RUN:
        return erased in row
    return erased == row


def windows(said: str) -> set[str]:
    return {said[i:i + RUN] for i in range(len(said) - RUN + 1)}


def repeats_in(said: str, said_windows: set[str], erased: str) -> bool:
    """`said` and `erased` are normalized; `said_windows` is windows(said)."""
    if not erased:
        return False
    if len(erased) >= RUN:
        return any(w in erased for w in said_windows)
    # a short text must appear whole, not inside a longer word ("hi" in "this")
    return re.search(rf"(?<!\w){re.escape(erased)}(?!\w)", said) is not None
