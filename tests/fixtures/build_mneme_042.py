"""Rebuild mneme-0.4.2.sql with the released mneme 0.4.2 code.

The fixture is a database as mneme 0.4.2 left it, kept as an SQL dump so it
stays reviewable and outside the `*.db` ignore rule. It holds one update, one
supersede and one 0.4.2 row-level forget, whose raw turn t3 stayed behind.

    git archive v0.4.2 src | tar -x -C <dir>
    python tests/fixtures/build_mneme_042.py <dir> tests/fixtures/mneme-0.4.2.sql

Every text in it is planted test data.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile


def build(source_root: str, out_sql: str) -> None:
    sys.path.insert(0, os.path.join(source_root, "src"))
    import mneme
    from mneme import AgentMemory

    assert mneme.__version__ == "0.4.2", mneme.__version__
    with tempfile.TemporaryDirectory() as scratch:
        db = os.path.join(scratch, "mneme-0.4.2.db")
        memory = AgentMemory(db)
        memory.remember("s", [
            {"id": "t1", "role": "user", "text": "My name is Dana. I live in Denver."},
            {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
            {"id": "t3", "role": "user", "text": "I work as a night nurse at the clinic."},
        ])
        atoms = {r["text"]: r["id"] for r in memory.store.memories(layer="L1")}
        memory.update(atoms["I live in Denver."], "I live in Denver, CO.",
                      reason="more precise")
        memory.supersede(atoms["I prefer dark roast coffee."], "I prefer green tea.",
                         reason="changed taste")
        memory.forget(atoms["I work as a night nurse at the clinic."], reason="user asked")
        memory.close()
        conn = sqlite3.connect(db)
        try:
            dump = "\n".join(conn.iterdump()) + "\n"
        finally:
            conn.close()
    with open(out_sql, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
