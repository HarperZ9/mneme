"""erase_text.py: when one text repeats an erased text.

Texts are compared after NFKC normalization, case folding and collapsing
whitespace. Two predicates follow from that:

- `repeats_whole`: a row repeats an erased text when it contains the whole
  normalized text. A text under 16 characters must equal the row instead,
  because a short text ("yes", "I am Bo.") turns up inside unrelated rows.
- `repeats_in`: free text such as an audit reason repeats an erased text
  when it holds any 16-character run of it, or the whole text as a word when
  it is shorter than 16 characters.
- `shares_token`: free text holds a token of an erased text as a whole word:
  a whitespace-separated token of 8 or more characters, or of 6 or more with
  a digit ("FAKE-KEY-7Q2Z9", a PIN, an account number). A 16-character run
  misses a short secret inside a long text; this does not. With
  `digits_only`, only tokens with a digit count, which keeps ordinary long
  words ("tomorrow") from marking an earlier audit reason as residue.
- `contains_short`: a row holds a short erased text (8 to 15 characters) as a
  whole word, so a short secret inside a longer kept row is found.

All of them catch verbatim repeats only. A paraphrase, a translation, or a
text with letters spaced apart does not match.
"""
from __future__ import annotations

import re
import unicodedata

RUN = 16
TOKEN = 8
TOKEN_WITH_DIGIT = 6
SHORT_FLOOR = 8
_EDGE = ".,;:!?\"'()[]{}<>"


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
    return _word(erased, said)


def _word(needle: str, said: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", said) is not None


def tokens(erased: str, *, digits_only: bool = False) -> set[str]:
    """The tokens of a normalized text that a reason may not repeat."""
    found = set()
    for raw in erased.split():
        token = raw.strip(_EDGE)
        digit = any(c.isdigit() for c in token)
        if (len(token) >= TOKEN_WITH_DIGIT and digit) or (
                len(token) >= TOKEN and not digits_only):
            found.add(token)
    return found


def shares_token(said: str, erased: str, *, digits_only: bool = False) -> bool:
    """`said` and `erased` are normalized; True when `said` holds a token of it."""
    return any(_word(t, said) for t in tokens(erased, digits_only=digits_only))


def contains_short(erased: str, row: str) -> bool:
    """A short erased text (SHORT_FLOOR..RUN-1 characters) inside a longer row."""
    return SHORT_FLOOR <= len(erased) < RUN and erased != row and _word(erased, row)
