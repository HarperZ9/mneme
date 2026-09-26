"""erase_reason.py: refuse an erase reason that would keep what the erase removes.

The reason is stored verbatim in the audit log, which is append-only. So a
reason may not repeat an erased text (erase_text.repeats_in), and it may not
carry a value that can confirm a guess of that text: the content-derived id
or content hash of an erased row, an origin digest, or the plan digest. Any
12-character run of such a value counts, in any letter case, so a prefix such
as the first 12 hex digits of an id is refused too.

The check catches verbatim repeats only. A reason can still describe the text
in other words, spell it with separators, or translate it.
"""
from __future__ import annotations

from .erase_text import norm, repeats_in, windows

ID_RUN = 12


class ErasedTextInReasonError(ValueError):
    """The reason would store erased text, or a value derived from it, verbatim."""


def _names_identifier(said: str, identifiers) -> bool:
    for value in identifiers:
        value = norm(str(value))
        if len(value) < ID_RUN:
            continue
        if any(value[i:i + ID_RUN] in said for i in range(len(value) - ID_RUN + 1)):
            return True
    return False


def check_reason(reason: str, texts, identifiers=()) -> None:
    """Refuse a reason holding an erased text, a 16-character run of one, or
    a 12-character run of an erased row's id or digest."""
    said = norm(reason)
    said_windows = windows(said)
    for text in texts:
        if repeats_in(said, said_windows, norm(text)):
            raise ErasedTextInReasonError(
                "the reason repeats text that this erase removes; reasons are "
                "stored verbatim in the audit log, so give the reason without it")
    if _names_identifier(said, identifiers):
        raise ErasedTextInReasonError(
            "the reason names an id or digest of a row this erase removes; such a "
            "value can confirm a guess of the erased text, so leave it out")
