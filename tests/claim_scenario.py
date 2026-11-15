"""claim_scenario.py: one store that has been through every public write path.

The claim tests plant a marker in a user's turn, then run the operations that
copy a memory's text or ids somewhere: extraction, scenarios, a persona, an
update, a supersede, consolidation, recall, drift, the audit view, an
`inspect --out` page, and a replay snapshot left behind by a stopped process.
`roots` is every directory mneme can write under in a test: the database
directory, the export directory, the snapshot root and the redirected OS temp
directory (conftest.py).
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from mneme import AgentMemory, cli, snapshot_dir


@dataclass
class Scene:
    db: Path
    exports: Path
    marker: str
    roots: list[Path]
    user: str = "alice"
    ids: list[str] = field(default_factory=list)       # content-derived ids and digests

    def open(self) -> AgentMemory:
        return AgentMemory(self.db)

    def marker_memories(self, memory: AgentMemory) -> list[str]:
        return [r["id"] for r in memory.store.memories(include_superseded=True)
                if self.marker in r["text"]]

    def current_marker_memory(self, memory: AgentMemory) -> str:
        return next(r["id"] for r in memory.store.memories()
                    if self.marker in r["text"] and r["layer"] == "L1")


def _remember(memory: AgentMemory, scene: Scene) -> str:
    memory.remember("home", [
        {"role": "user", "text": f"I live at {scene.marker} street in Denver."},
        {"role": "assistant", "text": "Noted, thanks."},
    ], user=scene.user)
    memory.remember("work", [
        {"role": "user", "text": "I always use a standing desk at work."},
    ], user=scene.user)
    memory.remember("other", [
        {"role": "user", "text": "I prefer oolong tea in the morning."},
    ], user="bob")
    return scene.current_marker_memory(memory)


def _record_ids(memory: AgentMemory, scene: Scene) -> None:
    for memory_id in scene.marker_memories(memory):
        row = memory.store.memory(memory_id)
        scene.ids += [memory_id, row["content_sha256"]]
        for source in json.loads(row["source_ids"]):
            turn = memory.store.turn(source)
            if turn is not None:
                scene.ids += [source, turn["content_sha256"]]
    scene.ids = sorted({i for i in scene.ids if len(i) >= 16})


def leave_dead_snapshot(scene: Scene, dead_pid: int) -> Path:
    """A replay snapshot whose creating process has exited, as a crash leaves it."""
    source = sqlite3.connect(scene.db)
    directory = snapshot_dir.private_dir(
        snapshot_dir.store_dir(snapshot_dir.store_id_of(source), scene.db))
    left = directory / f"mneme-replay-{dead_pid}-crashed0.db"
    copy = sqlite3.connect(left)
    source.backup(copy)
    copy.close()
    source.close()
    return left


def build(tmp_path: Path, snapshot_root: Path, marker: str, *, wal: bool = False,
          dead_pid: int | None = None, export: bool = True) -> Scene:
    store_dir, exports = tmp_path / "store", tmp_path / "exports"
    store_dir.mkdir()
    exports.mkdir()
    scene = Scene(db=store_dir / "mem.db", exports=exports, marker=marker,
                  roots=[tmp_path, snapshot_root.parent.parent,
                         Path(tempfile.gettempdir())])
    memory = AgentMemory(scene.db, embed="ngram")
    if wal:
        assert memory.store.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    first = _remember(memory, scene)
    _record_ids(memory, scene)                 # the first version's ids and digests
    memory.build_scenarios("home", user=scene.user)
    memory.persona("home", user=scene.user)
    memory.recall(marker, user=scene.user)
    memory.navigate(marker, user=scene.user)
    memory.drift()
    memory.provenance(first)
    memory.provenance_chain(first)
    memory.update(first, f"I live at {marker} avenue in Denver.", reason="street type")
    memory.supersede(first, f"I now live at {marker} road in Boulder.", reason="moved")
    memory.history(contains="live")
    memory.entity_graph()
    memory.consolidate("home")
    memory.audit()
    _record_ids(memory, scene)
    memory.close()
    if export:
        assert cli.main(["--state", str(scene.db), "inspect",
                         "--out", str(exports / "page.html")]) == 0
    if dead_pid is not None:
        leave_dead_snapshot(scene, dead_pid)
    return scene
