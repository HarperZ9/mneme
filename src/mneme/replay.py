"""Produce assessment-bound Crucible replay packs from Mneme state.

Descriptors are declarative identities only. This module neither imports
Crucible nor executes commands; it validates a decoded Crucible template,
re-runs Mneme's drift oracle, and returns decoded JSON-compatible data.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .compose import _deviation, _grounding_record, _measurement_contract, _record_hash
from .drift import check_memory
from .receipt import ProvenanceFormatError, decode_provenance, memory_hash

_ASSESSMENT_FIELDS = ("thesis_id", "assessment_seal", "measurement_seal")
_DESCRIPTOR_FIELDS = frozenset({
    "schema", "oracle", "memory_id", "grounding_sha256",
    "measurement_contract_sha256",
})
_MEASUREMENT_FIELDS = frozenset({
    "claim_id", "claim_sha256", "deviation", "tolerance", "method",
    "measured_at", "evidence",
})
_MEASUREMENT_SEAL_BASE_FIELDS = (
    "claim_id", "claim_sha256", "deviation", "tolerance", "method",
    "measured_at", "evidence",
)
_REPLAY_BINDING_FIELDS = frozenset({
    "schema", "descriptor_count", "skipped_count", "sha256",
})


class ReplayBindingError(ValueError):
    """The replay template no longer names one unambiguous Mneme subject."""


def _object(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReplayBindingError(f"{what} must be an object")
    return value


def _assessment(value: object) -> dict:
    assessment = _object(value, "assessment binding")
    for field in _ASSESSMENT_FIELDS:
        if not isinstance(assessment.get(field), str) or not assessment[field]:
            raise ReplayBindingError(
                f"assessment binding needs non-empty {field}")
    return {field: assessment[field] for field in _ASSESSMENT_FIELDS}


def _descriptor(value: object, row_number: int) -> Mapping[str, object]:
    descriptor = _object(value, f"replay row {row_number} descriptor")
    if set(descriptor) != _DESCRIPTOR_FIELDS:
        raise ReplayBindingError(
            f"replay row {row_number} descriptor fields must be exactly "
            f"{sorted(_DESCRIPTOR_FIELDS)}")
    if descriptor["schema"] != "mneme.recheck/1":
        raise ReplayBindingError(
            f"replay row {row_number} unsupported recheck schema")
    if descriptor["oracle"] != "mneme:drift/v1":
        raise ReplayBindingError(
            f"replay row {row_number} unsupported recheck oracle")
    for field in ("memory_id", "grounding_sha256", "measurement_contract_sha256"):
        if not isinstance(descriptor[field], str) or not descriptor[field]:
            raise ReplayBindingError(
                f"replay row {row_number} descriptor {field} must be non-empty")
    for field in ("grounding_sha256", "measurement_contract_sha256"):
        value = descriptor[field]
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ReplayBindingError(
                f"replay row {row_number} descriptor {field} must be lowercase SHA-256")
    return descriptor


def _expected_measurement(value: object, row_number: int) -> Mapping[str, object]:
    expected = _object(value, f"replay row {row_number} expected_measurement")
    if set(expected) != _MEASUREMENT_FIELDS:
        raise ReplayBindingError(
            f"replay row {row_number} expected_measurement fields must be exactly "
            f"{sorted(_MEASUREMENT_FIELDS)}")
    if not isinstance(expected["claim_id"], str) or not expected["claim_id"]:
        raise ReplayBindingError(
            f"replay row {row_number} expected_measurement claim_id must be non-empty")
    if not isinstance(expected["claim_sha256"], str) or not expected["claim_sha256"]:
        raise ReplayBindingError(
            f"replay row {row_number} expected_measurement claim_sha256 must be non-empty")
    if not isinstance(expected["method"], str) or not expected["method"]:
        raise ReplayBindingError(
            f"replay row {row_number} expected_measurement method must be non-empty")
    evidence = expected["evidence"]
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        raise ReplayBindingError(
            f"replay row {row_number} expected_measurement evidence must be strings")
    return expected


def _measurement_seal(rows: list[Mapping[str, object]]) -> str:
    """Reproduce Crucible's pinned descriptor-bearing measurement seal.

    This small stdlib implementation prevents row omission without importing
    Crucible or treating any executable as part of Mneme's replay boundary.
    """
    fields = (_MEASUREMENT_SEAL_BASE_FIELDS + ("recheck",)
              if any("recheck" in row for row in rows)
              else _MEASUREMENT_SEAL_BASE_FIELDS)
    objects = [{field: row.get(field) for field in fields} for row in rows]
    objects.sort(key=lambda value: json.dumps(
        value, sort_keys=True, ensure_ascii=False))
    canonical = json.dumps(objects, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compact_canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _replay_set_sha(rows: list[Mapping[str, object]]) -> str:
    """Hash only the disclosed descriptor/measurement pairs, retaining multiplicity."""
    objects = [{
        "recheck": row["recheck"],
        "expected_measurement": row["expected_measurement"],
    } for row in rows]
    objects.sort(key=_compact_canonical)
    canonical = _compact_canonical(objects)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_binding(value: object,
                    rows: list[Mapping[str, object]]) -> dict:
    binding = _object(value, "replay binding")
    if set(binding) != _REPLAY_BINDING_FIELDS:
        raise ReplayBindingError(
            f"replay binding fields must be exactly {sorted(_REPLAY_BINDING_FIELDS)}")
    if binding["schema"] != "crucible.replay-set/1":
        raise ReplayBindingError("unsupported replay binding schema")
    descriptor_count = binding["descriptor_count"]
    if type(descriptor_count) is not int or descriptor_count != len(rows):
        raise ReplayBindingError(
            "replay binding descriptor_count does not match disclosed replay rows")
    skipped_count = binding["skipped_count"]
    if type(skipped_count) is not int or skipped_count < 0:
        raise ReplayBindingError(
            "replay binding skipped_count must be a non-negative integer")
    sha256 = binding["sha256"]
    if (not isinstance(sha256, str) or len(sha256) != 64
            or any(char not in "0123456789abcdef" for char in sha256)):
        raise ReplayBindingError("replay binding sha256 must be lowercase SHA-256")
    try:
        expected_sha = _replay_set_sha(rows)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise ReplayBindingError("replay binding rows are not canonically encodable") from exc
    if sha256 != expected_sha:
        raise ReplayBindingError(
            "replay binding sha256 mismatch: replay rows were omitted or tampered")
    return dict(binding)


def replay_crucible(store, template: Mapping[str, object]) -> dict:
    """Re-run all rows in a decoded Crucible template against ``store``.

    A single explicit SQLite read transaction pins the target grounding and
    every subsequent drift-oracle read to one snapshot. Without it, a writer
    could change the subject between descriptor validation and replay.
    """
    connection = store.conn
    owns_snapshot = not connection.in_transaction
    if owns_snapshot:
        connection.execute("BEGIN")
    try:
        return _replay_crucible_snapshot(store, template)
    finally:
        if owns_snapshot:
            connection.rollback()


def _replay_crucible_snapshot(store, template: Mapping[str, object]) -> dict:
    """Validate and replay while the caller holds one SQLite snapshot.

    Claim hashes, timestamps, tolerances, methods, and evidence are copied from
    the assessment-bound expected measurement. Only deviation is recomputed.
    """
    template = _object(template, "replay template")
    has_replay_binding = "replay_binding" in template
    if "schema" in template:
        if template["schema"] != "crucible.replay-template/1":
            raise ReplayBindingError("unsupported replay template schema")
    elif has_replay_binding:
        raise ReplayBindingError("unsupported replay template schema")
    if "measurement_seal_rows" in template:
        raise ReplayBindingError(
            "measurement_seal_rows context is private and not accepted")
    assessment = _assessment(template.get("assessment"))
    rows = template.get("replays")
    if not isinstance(rows, list) or not rows:
        raise ReplayBindingError("replay template needs a non-empty replays list")

    seen: set[str] = set()
    validated = []
    sealed_rows = []
    binding_rows = []
    for row_number, value in enumerate(rows, 1):
        row = _object(value, f"replay row {row_number}")
        # A Crucible template always carries its blank output slot. Requiring it
        # distinguishes a complete template row from an ad-hoc descriptor dump.
        _object(row.get("measurement"), f"replay row {row_number} measurement")
        claim = _object(row.get("claim"), f"replay row {row_number} claim")
        descriptor = _descriptor(row.get("recheck"), row_number)
        expected = _expected_measurement(row.get("expected_measurement"), row_number)

        memory_id = descriptor["memory_id"]
        if memory_id in seen:
            raise ReplayBindingError(f"replay row {row_number} duplicate memory_id {memory_id!r}")
        seen.add(memory_id)

        if claim.get("id") != memory_id:
            raise ReplayBindingError(
                f"replay row {row_number} claim/descriptor binding mismatch")
        if expected["claim_id"] != claim.get("id") or expected["claim_sha256"] != claim.get("sha256"):
            raise ReplayBindingError(
                f"replay row {row_number} claim/measurement binding mismatch")

        portable = {
            "claim": expected["claim_id"],
            "deviation": expected.get("deviation"),
            "tolerance": expected["tolerance"],
            "method": expected["method"],
            "evidence": list(expected["evidence"]),
        }
        if _record_hash(_measurement_contract(portable)) != descriptor["measurement_contract_sha256"]:
            raise ReplayBindingError(
                f"replay row {row_number} measurement contract binding mismatch")

        target = store.memory(memory_id)
        if target is None:
            raise ReplayBindingError(
                f"replay row {row_number} target memory {memory_id!r} not found")
        try:
            target_source_ids, _ = decode_provenance(
                target["source_ids"], target["source_hashes"])
            reproduced_target_hash = memory_hash(
                target["text"], target_source_ids, target["criterion"])
            if reproduced_target_hash != target["content_sha256"]:
                raise ReplayBindingError(
                    f"replay row {row_number} grounding binding mismatch: "
                    "target memory hash does not reproduce")
            grounding_sha = _record_hash(_grounding_record(target))
        except ReplayBindingError:
            raise
        except (KeyError, TypeError, ValueError, ProvenanceFormatError) as exc:
            raise ReplayBindingError(
                f"replay row {row_number} grounding binding is malformed") from exc
        if grounding_sha != descriptor["grounding_sha256"]:
            raise ReplayBindingError(
                f"replay row {row_number} grounding binding mismatch")

        descriptor_copy = dict(descriptor)
        expected_copy = dict(expected)
        expected_copy["evidence"] = list(expected["evidence"])
        sealed_rows.append({**expected_copy, "recheck": descriptor_copy})
        binding_rows.append({
            "recheck": descriptor_copy,
            "expected_measurement": expected_copy,
        })
        validated.append((memory_id, descriptor_copy, expected_copy))

    replay_binding = None
    if has_replay_binding:
        replay_binding = _replay_binding(
            template.get("replay_binding"), binding_rows)
    else:
        try:
            reproduced_measurement_seal = _measurement_seal(sealed_rows)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ReplayBindingError(
                "assessment measurement seal binding is not canonically encodable"
            ) from exc
        if reproduced_measurement_seal != assessment["measurement_seal"]:
            raise ReplayBindingError(
                "assessment measurement seal binding mismatch: replay rows were "
                "omitted or tampered")

    replayed = []
    for memory_id, descriptor, expected in validated:
        verdict = check_memory(store, memory_id)
        measurement = dict(expected)
        measurement["evidence"] = list(expected["evidence"])
        measurement["deviation"] = _deviation(verdict.verdict)
        replayed.append({
            "recheck": descriptor,
            "measurement": measurement,
            "mneme_verdict": verdict.verdict,
            "mneme_reason": verdict.reason,
        })

    pack = {
        "schema": "crucible.replay-pack/1",
        "assessment": assessment,
        "replays": replayed,
    }
    if replay_binding is not None:
        pack["replay_binding"] = replay_binding
    return pack
