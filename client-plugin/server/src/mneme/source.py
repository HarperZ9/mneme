"""High-level source-turn planning for partition-aware Mneme ingestion.

The low-level Store API remains intentionally mutable: tests and trusted tools
can still overwrite a turn to exercise drift. AgentMemory and Gather ingestion
use this planner before any write so user/session-owned source identities do not
silently collide in the shared L0 table.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from .receipt import ProvenanceFormatError, content_hash, decode_provenance, memory_hash
from .store import Store

INTERNAL_SOURCE_PREFIX = "src:v1:"
NATIVE_SOURCE_NAMESPACE = "mneme.source-turn/v1"
GATHER_SOURCE_NAMESPACE = "mneme.gather-source/v1"
PARTITION_ORIGIN_KEY = "mneme_source_partition"
PARTITION_ORIGIN_SCHEMA = "mneme.source-partition/1"


@dataclass(frozen=True, slots=True)
class PlannedTurn:
    id: str
    raw_id: str
    role: str
    text: str
    origin: dict | None
    write: bool

    def extraction_row(self, *, role: str | None = None) -> dict:
        return {"id": self.id, "role": role or self.role, "text": self.text}


def source_turn_id(session: str, user: str, raw_id: str, *,
                   namespace: str = NATIVE_SOURCE_NAMESPACE,
                   identity_parts: Sequence[str] = ()) -> str:
    """Return the high-level L0 source id for one partition.

    The empty/default user is the legacy raw-id namespace. It remains readable
    and writable for existing callers, except that new writes may not mint IDs
    in Mneme's reserved internal prefix.
    """
    if user == "":
        if raw_id.startswith(INTERNAL_SOURCE_PREFIX):
            raise ValueError(
                f"turn id {raw_id!r} uses reserved internal source id prefix "
                f"{INTERNAL_SOURCE_PREFIX!r}")
        return raw_id
    return INTERNAL_SOURCE_PREFIX + content_hash(
        namespace, user, session, raw_id, *identity_parts)[:16]


def partitioned_origin(session: str, user: str, raw_id: str, *,
                       namespace: str = NATIVE_SOURCE_NAMESPACE,
                       identity_parts: Sequence[str] = (),
                       origin: dict | None = None) -> dict | None:
    """Attach non-secret partition metadata to new named-user source turns."""
    if user == "":
        return origin
    out = dict(origin or {})
    out[PARTITION_ORIGIN_KEY] = {
        "schema": PARTITION_ORIGIN_SCHEMA,
        "namespace": namespace,
        "partition_sha256": content_hash(
            namespace, user, session, raw_id, *identity_parts),
    }
    return out


def plan_source_turns(store: Store, session: str, turns: Sequence[dict], *,
                      user: str = "",
                      namespace: str = NATIVE_SOURCE_NAMESPACE) -> list[PlannedTurn]:
    """Validate and plan a batch of source turns before writing any L0 row."""
    planned: list[PlannedTurn] = []
    by_id: dict[str, tuple[str, str, str, str]] = {}
    for index, turn in enumerate(turns):
        candidate = _normalize_candidate(session, index, turn)
        identity_parts = tuple(str(part) for part in candidate.get("identity_parts", ()))
        raw_id = candidate["raw_id"]
        stored_origin = partitioned_origin(
            session,
            user,
            raw_id,
            namespace=namespace,
            identity_parts=identity_parts,
            origin=candidate["origin"],
        )
        stored_id = source_turn_id(
            session,
            user,
            raw_id,
            namespace=namespace,
            identity_parts=identity_parts,
        )
        legacy_id = _legacy_source_id_if_unambiguous(
            store,
            session,
            user,
            raw_id,
            candidate["role"],
            candidate["text"],
            candidate["origin"],
        )
        if legacy_id is not None:
            stored_id = legacy_id
            stored_origin = candidate["origin"]
        signature = _signature(session, candidate["role"],
                               candidate["text"], stored_origin)
        prior_signature = by_id.get(stored_id)
        if prior_signature is not None:
            if prior_signature != signature:
                raise ValueError(
                    f"conflicting turn id {raw_id!r} in one memory batch")
            continue
        write = _turn_requires_write(
            store,
            stored_id,
            raw_id,
            signature,
            user=user,
            session=session,
            namespace=namespace,
            identity_parts=identity_parts,
            legacy_id=legacy_id,
        )
        by_id[stored_id] = signature
        planned.append(PlannedTurn(
            id=stored_id,
            raw_id=raw_id,
            role=candidate["role"],
            text=candidate["text"],
            origin=stored_origin,
            write=write,
        ))
    return planned


def preflight_memory_writes(store: Store, atoms: Sequence[tuple[str, object]], *,
                            criterion: str, user: str) -> None:
    """Reject memory id collisions before the caller writes source turns."""
    for memory_id, atom in atoms:
        source_id = getattr(atom, "source_id")
        text = getattr(atom, "text")
        sha = memory_hash(text, [source_id], criterion)
        prior = store.memory(memory_id)
        if prior is None:
            continue
        if prior["user"] != user:
            raise ValueError(
                f"memory id {memory_id!r} already owned by user "
                f"{prior['user']!r}; refusing cross-tenant overwrite")
        if prior["content_sha256"] != sha:
            raise ValueError(
                f"memory id {memory_id!r} exists with different content; "
                f"route a content change through update()")


def _normalize_candidate(session: str, index: int, turn: dict) -> dict:
    if not isinstance(turn, dict):
        raise ValueError(f"turn {index} must be an object")
    for field in ("role", "text"):
        if field not in turn:
            raise ValueError(f"turn {index} missing required field {field!r}")
        if not isinstance(turn[field], str):
            raise ValueError(f"turn {index} field {field!r} must be a string")
    raw_id = str(
        turn.get("id")
        or content_hash(session, str(index), turn["role"], turn["text"])[:16]
    )
    origin = turn.get("origin")
    if origin is not None and not isinstance(origin, dict):
        raise ValueError(f"turn {index} field 'origin' must be an object")
    return {
        "raw_id": raw_id,
        "role": turn["role"],
        "text": turn["text"],
        "origin": origin,
        "identity_parts": tuple(turn.get("identity_parts", ())),
    }


def _turn_requires_write(store: Store, turn_id: str, raw_id: str,
                         signature: tuple[str, str, str, str], *,
                         user: str, session: str, namespace: str,
                         identity_parts: Sequence[str],
                         legacy_id: str | None) -> bool:
    existing = store.turn(turn_id)
    if existing is None:
        return True
    if _row_signature(existing) != signature:
        raise ValueError(
            f"conflicting turn id {raw_id!r} in this source partition")
    if user and legacy_id is None and not _existing_turn_matches_partition(
            existing,
            session,
            user,
            raw_id,
            namespace=namespace,
            identity_parts=identity_parts,
    ):
        raise ValueError(
            f"source turn id {turn_id!r} already exists outside this "
            "user/session partition")
    return False


def _signature(session: str, role: str, text: str,
               origin: dict | None) -> tuple[str, str, str, str]:
    return session, role, text, _origin_json(origin)


def _row_signature(row) -> tuple[str, str, str, str]:
    return row["session"], row["role"], row["text"], _row_origin_json(row)


def _origin_json(origin: dict | None) -> str:
    if not origin:
        return ""
    return json.dumps(
        origin,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _row_origin_json(row) -> str:
    raw = row["origin"] or ""
    if not raw:
        return ""
    try:
        origin = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if not isinstance(origin, dict):
        return raw
    return _origin_json(origin)


def _existing_turn_matches_partition(row, session: str, user: str, raw_id: str, *,
                                     namespace: str,
                                     identity_parts: Sequence[str]) -> bool:
    try:
        origin = json.loads(row["origin"]) if row["origin"] else {}
    except (TypeError, json.JSONDecodeError):
        return False
    marker = origin.get(PARTITION_ORIGIN_KEY)
    return marker == partitioned_origin(
        session,
        user,
        raw_id,
        namespace=namespace,
        identity_parts=identity_parts,
        origin={},
    )[PARTITION_ORIGIN_KEY]


def _legacy_source_id_if_unambiguous(store: Store, session: str, user: str,
                                     raw_id: str, role: str, text: str,
                                     origin: dict | None) -> str | None:
    if user == "" or raw_id.startswith(INTERNAL_SOURCE_PREFIX):
        return None
    row = store.turn(raw_id)
    if row is None:
        return None
    if _row_signature(row) != _signature(session, role, text, origin):
        return None
    expected_hash = content_hash(role, text)
    if _other_current_user_cites_source(store, session, user, raw_id):
        return None
    if _current_user_cites_source(store, session, user, raw_id, expected_hash):
        return raw_id
    return None


def _current_user_cites_source(store: Store, session: str, user: str,
                               source_id: str, expected_hash: str) -> bool:
    for memory in store.memories(session=session, user=user):
        if _memory_cites_source(memory, source_id, expected_hash):
            return True
    return False


def _other_current_user_cites_source(store: Store, session: str, user: str,
                                     source_id: str) -> bool:
    for memory in store.memories(session=session):
        if memory["user"] == user:
            continue
        if _memory_cites_source(memory, source_id, None):
            return True
    return False


def _memory_cites_source(memory, source_id: str,
                         expected_hash: str | None) -> bool:
    try:
        source_ids, source_hashes = decode_provenance(
            memory["source_ids"],
            memory["source_hashes"],
        )
    except ProvenanceFormatError:
        return False
    if source_id not in source_ids:
        return False
    return expected_hash is None or source_hashes.get(source_id) == expected_hash
