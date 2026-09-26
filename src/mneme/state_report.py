"""state_report.py: where a mneme database lives, and what it holds.

The default database is `mneme.db` in whatever directory a command runs, so an
owner can collect memory databases across project folders without meaning to,
some of them inside git work trees, where one `git add .` commits the memory.
`mneme status` and `mneme doctor` report the resolved absolute path, whether
it is the default location, the replay snapshot directory, the files and the
row counts. They open the database read-only: they never create a missing
database, migrate an old one or write to it.

`status` describes. `doctor` also re-derives the audit chain and exits 1 when
anything needs the owner's attention. The MCP `mneme.doctor` tool reports the
path facts only and never opens the database, because a lane host runs it as a
readiness probe. Warnings name paths and counts, never memory text.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from . import __version__, audit_writer, schema_guard, snapshot_dir
from .schema import SCHEMA_VERSION

REPORT_SCHEMA = "mneme.state/1"
DEFAULT_STATE = "mneme.db"
SIDECARS = ("-journal", "-wal", "-shm")
DEFAULT_NOTE = ("this is the default database, mneme.db in the current directory: "
                "a command run in another directory uses another database")


def resolve(state: str) -> tuple[str, Path | None]:
    """The kind of a state argument ('file', 'memory', 'temporary') and its path."""
    if state == ":memory:":
        return "memory", None
    if state == "":
        return "temporary", None
    return "file", Path(state).expanduser().resolve()


def _is_git_marker(marker: Path) -> bool:
    """A `.git` directory with HEAD and objects, or a `gitdir:` file (a linked
    work tree or submodule). A bare `.git` folder with neither is no repository,
    so it does not count."""
    try:
        if marker.is_dir():
            return (marker / "HEAD").is_file() and (marker / "objects").is_dir()
        if marker.is_file():
            with marker.open("rb") as handle:
                return handle.read(8) == b"gitdir: "
    except OSError:
        return False
    return False


def git_work_tree(path: Path) -> Path | None:
    """The nearest ancestor directory that is the root of a git work tree."""
    for parent in path.parents:
        if _is_git_marker(parent / ".git"):
            return parent
    return None


def _kind_warning(kind: str) -> str | None:
    if kind == "memory":
        return "the state is :memory:, so nothing is kept after the process exits"
    if kind == "temporary":
        return ("the state path is empty: SQLite opens a private temporary database "
                "and deletes it when the connection closes, so nothing is kept")
    return None


def locate(state: str) -> dict:
    """Path facts and path warnings. Never opens the database."""
    kind, path = resolve(state)
    report = {"state": state, "kind": kind, "state_path": str(path) if path else None,
              "default_location": False, "exists": False, "files": [],
              "git_work_tree": None, "snapshot_dir": str(snapshot_dir.snapshot_root()),
              "warnings": [], "notes": []}
    warning = _kind_warning(kind)
    if warning:
        report["warnings"].append(warning)
        return report
    report["default_location"] = path == (Path.cwd() / DEFAULT_STATE).resolve()
    if report["default_location"]:
        report["notes"].append(DEFAULT_NOTE)
    report["exists"] = path.exists()
    report["files"] = [{"name": p.name, "bytes": p.stat().st_size}
                       for p in (path, *(Path(f"{path}{s}") for s in SIDECARS))
                       if p.is_file()]
    if not report["exists"]:
        report["warnings"].append(f"no database at {path}; the first mneme command "
                                  "that writes creates it here")
    elif not path.is_file():
        report["warnings"].append(f"{path} is not a file, so mneme cannot use it")
    tree = git_work_tree(path)
    if tree is not None:
        report["git_work_tree"] = str(tree)
        report["warnings"].append(
            f"the database is inside the git work tree at {tree}; unless git ignores "
            "it, one `git add .` commits your memory. Move it out, or pass --state "
            "(MNEME_STATE for the MCP server) with a path outside the work tree")
    return report


def _legacy_temp_warning() -> tuple[int, str | None]:
    count = len(snapshot_dir.legacy_temp_snapshots())
    if not count:
        return 0, None
    return count, (f"{count} replay snapshot file(s) from mneme before 0.5.0 are in "
                   f"{tempfile.gettempdir()} (mneme-replay-*.db); they are full copies "
                   "of a memory database that mneme cannot tie to a store, so check "
                   "and delete them yourself")


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)


def _counts(conn: sqlite3.Connection, tables: set[str]) -> dict:
    counts = {"turns": 0, "memories": {}, "audit": 0}
    if "turns" in tables:
        counts["turns"] = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    if "memories" in tables:
        counts["memories"] = dict(conn.execute(
            "SELECT layer, COUNT(*) FROM memories GROUP BY layer ORDER BY layer"))
    if "audit" in tables:
        counts["audit"] = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    return counts


def _read_database(path: Path, verify: bool) -> dict:
    conn = _connect(path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        facts = {"counts": _counts(conn, tables), "store_id": None,
                 "schema": {"stored": None, "high_water": None, "downgrade_seen": None}}
        if "meta" in tables:
            facts["store_id"] = snapshot_dir.store_id_of(conn)
            facts["schema"] = schema_guard.read(conn)
        if verify:
            chain = audit_writer.verify(conn) if "audit" in tables else True
            facts["audit"] = {"entries": facts["counts"]["audit"], "chain_intact": chain}
        return facts
    finally:
        conn.close()


def _database_section(report: dict, path: Path, verify: bool) -> None:
    try:
        facts = _read_database(path, verify)
    except sqlite3.Error as exc:
        report["warnings"].append(f"cannot read {path} as a mneme database: {exc}")
        return
    schema = facts.pop("schema")
    running = int(SCHEMA_VERSION)
    report.update(facts)
    report["schema_version"] = {**schema, "running": running}
    report["warnings"].extend(schema_guard.findings(
        schema["stored"], schema["high_water"], running, schema["downgrade_seen"]))
    if verify and not facts["audit"]["chain_intact"]:
        report["warnings"].append(
            "the audit chain does not re-derive: an entry was edited, reordered or "
            "removed, or the head anchor was changed")


def _snapshot_section(report: dict, path: Path) -> None:
    store_id = report.get("store_id")
    directory = snapshot_dir.store_dir(store_id, path)
    counts = snapshot_dir.store_snapshot_counts(store_id, path)
    legacy, warning = _legacy_temp_warning()
    report["store_snapshot_dir"] = str(directory) if directory else None
    report["snapshots"] = {**counts, "legacy_temp": legacy}
    if counts["orphaned"]:
        report["warnings"].append(
            f"{counts['orphaned']} replay snapshot(s) of this store in {directory} were "
            "left by a process that is gone; the next replay snapshot removes them, "
            "and so does `mneme forget`")
    if warning:
        report["warnings"].append(warning)


def describe(state: str, *, check: str = "status") -> dict:
    """The CLI report. `check='doctor'` also re-derives the audit chain."""
    report = {"schema": REPORT_SCHEMA, "check": check, "version": __version__,
              **locate(state)}
    if report["kind"] == "file" and report["exists"] and Path(report["state_path"]).is_file():
        _database_section(report, Path(report["state_path"]), check == "doctor")
    if report["kind"] == "file":
        _snapshot_section(report, Path(report["state_path"]))
    return report


def mcp_doctor(env_state: str | None) -> dict:
    """Path facts for the MCP doctor. It never opens the database."""
    state = DEFAULT_STATE if env_state is None else env_state
    facts = locate(state)
    _count, warning = _legacy_temp_warning()
    if warning:
        facts["warnings"].append(warning)
    return {"state_path": state, "state_path_absolute": facts["state_path"],
            "state_from_env": env_state is not None,
            **{k: facts[k] for k in ("kind", "default_location", "exists",
                                     "git_work_tree", "snapshot_dir", "warnings",
                                     "notes")}}

