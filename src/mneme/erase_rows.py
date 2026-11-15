"""erase_rows.py: the rows an erase deletes, and the audit entries it leaves.

`subjects` reads each row in the plan. `delete_and_audit` deletes them in a
shuffled order (derived layers first, turns last) and appends one audit entry
per row: a random erase ref, never the content-derived id, and a commitment
`sha256("mneme.erase.v1" || 0x00 || salt || utf8(text))` whose 32-byte salt is
never stored. `forget_names` deletes what else names an erased memory: the
salts of its blinded history, its audit alias (audit_blind.py) and its merge
links. `residue_texts` is what the receipt's kept-row checks compare against.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass

from . import audit_blind, audit_writer
from .erase_index import cited

COMMITMENT_TAG = b"mneme.erase.v1"


@dataclass
class Subject:
    table: str
    id: str
    layer: str
    text: str
    content_sha256: str
    origin: str = ""
    sources: tuple[str, ...] = ()

    def locators(self) -> tuple[str, ...]:
        """The origin's ref and source digest: erased too, and never boilerplate."""
        try:
            origin = json.loads(self.origin) if self.origin else {}
        except ValueError:
            return ()
        if not isinstance(origin, dict):
            return ()
        return tuple(str(origin[k]) for k in ("ref", "sha256") if origin.get(k))


def subjects(conn: sqlite3.Connection, plan: dict) -> list[Subject]:
    out = []
    for memory_id in plan["memories"]:
        r = conn.execute("SELECT layer, text, content_sha256, source_ids FROM memories "
                         "WHERE id=?", (memory_id,)).fetchone()
        out.append(Subject("memories", memory_id, r[0], r[1], r[2], sources=cited(r[3])))
    for turn_id in plan["turns"]:
        r = conn.execute("SELECT text, content_sha256, origin FROM turns WHERE id=?",
                         (turn_id,)).fetchone()
        out.append(Subject("turns", turn_id, "L0", r[0], r[1], r[2] or ""))
    return out


def residue_texts(conn: sqlite3.Connection, subjects, plan: dict) -> list[str]:
    """The erased texts that a kept row may not repeat.

    A scenario or persona row is the text of the atoms it cites, one per line.
    When such a row is erased because one atom went, its other lines are atoms
    the store keeps, and they are not residue: those lines are left out."""
    erased, out = set(plan["memories"]), []
    for subject in subjects:
        if subject.layer not in ("L2", "L3"):
            out.append(subject.text)
            continue
        kept = {row[0] for source in subject.sources if source not in erased
                for row in conn.execute("SELECT text FROM memories WHERE id=?", (source,))}
        out.append("\n".join(line for line in subject.text.split("\n")
                             if line.removeprefix("- ") not in kept))
    return out


def commitment(text: str) -> tuple[bytes, str]:
    salt = secrets.token_bytes(32)
    body = COMMITMENT_TAG + b"\x00" + salt + text.encode("utf-8")
    return salt, hashlib.sha256(body).hexdigest()


def delete_and_audit(conn: sqlite3.Connection, subjects, reason: str) -> list[dict]:
    order = {"L3": 0, "L2": 1, "L1": 2, "L0": 3}
    shuffled = list(subjects)
    secrets.SystemRandom().shuffle(shuffled)
    shuffled.sort(key=lambda s: (s.table == "turns", order.get(s.layer, 4)))
    openings = []
    for subject in shuffled:
        conn.execute(f"DELETE FROM {subject.table} WHERE id=?", (subject.id,))
        salt, before = commitment(subject.text)
        ref = "er_" + secrets.token_hex(12)
        audit_writer.append(conn, "erase", ref, subject.layer, before, "", reason)
        openings.append({"erase_ref": ref, "subject_id": subject.id,
                         "layer": subject.layer, "salt": salt.hex()})
    return openings


def identifiers(subjects, plan: dict) -> list[str]:
    """Values that could confirm a guess of an erased text if a reason kept them."""
    values = [plan["plan_sha256"], *plan["merged_away"]]
    for subject in subjects:
        values += [subject.id, subject.content_sha256, *subject.locators()]
    return values


def delete_merges(conn: sqlite3.Connection, plan: dict) -> int:
    memories, dropped, turns = set(plan["memories"]), set(plan["merged_away"]), set(plan["turns"])
    doomed = [d for d, kept, raw in conn.execute(
                  "SELECT dropped_id, kept_id, source_ids FROM merges")
              if kept in memories or d in dropped or set(cited(raw)) & turns]
    for dropped_id in doomed:
        conn.execute("DELETE FROM merges WHERE dropped_id=?", (dropped_id,))
    return len(doomed)


def forget_names(conn: sqlite3.Connection, plan: dict) -> int:
    """Delete the salts, audit aliases and merge links of the plan's memories.

    Returns the number of salts deleted. No commit."""
    salts = audit_blind.delete_salts(conn, plan["memories"])
    audit_blind.drop_aliases(conn, [*plan["memories"], *plan["merged_away"]])
    delete_merges(conn, plan)
    return salts
