"""Falsifiers for Mneme-owned production of Crucible replay packs."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
import mneme.cli as cli_module
from mneme.cli import main
from mneme.replay import ReplayBindingError, replay_crucible


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _measurement_seal_from_rows(rows: list[dict]) -> str:
    """Independent pin of Crucible's descriptor-bearing measurement seal."""
    fields = (
        "claim_id", "claim_sha256", "deviation", "tolerance", "method",
        "measured_at", "evidence", "recheck",
    )
    objects = []
    for row in rows:
        objects.append({key: row.get(key) for key in fields})
    objects.sort(key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
    canonical = json.dumps(objects, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_binding(rows: list[dict], *, skipped_count: int = 0) -> dict:
    objects = [{
        "recheck": row["recheck"],
        "expected_measurement": row["expected_measurement"],
    } for row in rows]
    objects.sort(key=lambda value: json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    canonical = json.dumps(
        objects, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "schema": "crucible.replay-set/1",
        "descriptor_count": len(objects),
        "skipped_count": skipped_count,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _state(path: Path) -> AgentMemory:
    memory = AgentMemory(path)
    for index in range(1, 6):
        turn_id = f"turn-{index}"
        memory.store.add_turn(
            turn_id,
            "qcr",
            "user",
            f"Source statement {index}" if index < 5 else "Source statement \u03c0",
        )
        memory.store.add_memory(
            f"memory-{index}",
            "L1",
            f"Derived statement {index}",
            [turn_id],
            "fixture/v1",
            "QCR replay fixture",
            session="qcr",
            user="operator",
        )
    return memory


def _template(memory: AgentMemory) -> dict:
    export = memory.to_crucible("qcr")
    rows = []
    for index, measurement in enumerate(export["measurements"], 1):
        claim_id = measurement["claim"]
        claim_sha = _sha(f"claim:{claim_id}")
        expected = {
            "claim_id": claim_id,
            "claim_sha256": claim_sha,
            "deviation": measurement["deviation"],
            "tolerance": measurement["tolerance"],
            "method": measurement["method"],
            "measured_at": 1_700_000_000.0 + index,
            "evidence": measurement["evidence"],
        }
        rows.append({
            "claim": {
                "id": claim_id,
                "sha256": claim_sha,
                "text": f"claim {index}",
                "status": "MATCH",
            },
            "recheck": measurement["recheck"],
            "expected_measurement": expected,
            "measurement": {
                **expected,
                "deviation": None,
                "measured_at": None,
                "evidence": [],
            },
        })
    sealed_rows = [
        {**row["expected_measurement"], "recheck": row["recheck"]}
        for row in rows
    ]
    return {
        "schema": "crucible.replay-template/1",
        "assessment": {
            "thesis_id": "fixture-thesis",
            "assessment_seal": _sha("assessment"),
            "measurement_seal": _measurement_seal_from_rows(sealed_rows),
        },
        "replay_binding": _replay_binding(rows),
        "instructions": "Crucible-generated replay template fixture",
        "replays": rows,
    }


def test_replay_produces_complete_assessment_bound_five_row_pack(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)

    pack = replay_crucible(memory.store, template)

    assert pack["schema"] == "crucible.replay-pack/1"
    assert pack["assessment"] == template["assessment"]
    assert pack["replay_binding"] == template["replay_binding"]
    assert "measurement_seal_rows" not in pack
    assert len(pack["replays"]) == 5
    assert [r["recheck"] for r in pack["replays"]] == [
        r["recheck"] for r in template["replays"]
    ]
    for replay, source in zip(pack["replays"], template["replays"], strict=True):
        actual = replay["measurement"]
        expected = source["expected_measurement"]
        assert actual == expected
        assert actual["claim_sha256"] == expected["claim_sha256"]
        assert actual["measured_at"] == expected["measured_at"]
        assert actual["tolerance"] == expected["tolerance"]
        assert actual["method"] == expected["method"]
        assert actual["evidence"] == expected["evidence"]
        assert replay["mneme_verdict"] == "MATCH"
        assert replay["mneme_reason"]


@pytest.mark.parametrize("missing", ["thesis_id", "assessment_seal", "measurement_seal"])
def test_replay_rejects_incomplete_assessment_binding(tmp_path, missing):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    del template["assessment"][missing]

    with pytest.raises(ReplayBindingError, match="assessment binding"):
        replay_crucible(memory.store, template)


@pytest.mark.parametrize("assessment", [None, {}, "not-an-object"])
def test_replay_rejects_null_or_invalid_assessment(tmp_path, assessment):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["assessment"] = assessment

    with pytest.raises(ReplayBindingError, match="assessment binding"):
        replay_crucible(memory.store, template)


@pytest.mark.parametrize("schema", [None, "crucible.replay-template/999", 1])
def test_replay_rejects_missing_or_unsupported_template_schema(tmp_path, schema):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    if schema is None:
        template.pop("schema")
    else:
        template["schema"] = schema

    with pytest.raises(ReplayBindingError, match="unsupported replay template schema"):
        replay_crucible(memory.store, template)


def test_replay_rejects_duplicate_rows(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replays"].append(deepcopy(template["replays"][0]))

    with pytest.raises(ReplayBindingError, match="duplicate memory_id"):
        replay_crucible(memory.store, template)


def test_replay_rejects_omitted_row_against_replay_binding(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replays"].pop()

    with pytest.raises(ReplayBindingError, match="replay binding"):
        replay_crucible(memory.store, template)


def test_replay_supports_mixed_assessment_without_legacy_row_disclosure(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replay_binding"] = _replay_binding(
        template["replays"], skipped_count=1)
    # The complete mixed assessment seal is opaque to Mneme and deliberately
    # cannot be reproduced from the five disclosed descriptor rows.
    template["assessment"]["measurement_seal"] = _sha("opaque mixed assessment")

    pack = replay_crucible(memory.store, template)

    assert len(pack["replays"]) == 5
    assert pack["replay_binding"]["skipped_count"] == 1
    assert "measurement_seal_rows" not in pack


def test_replay_rejects_private_measurement_seal_rows_context(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["measurement_seal_rows"] = [{
        **template["replays"][0]["expected_measurement"],
        "recheck": template["replays"][0]["recheck"],
    }]

    with pytest.raises(ReplayBindingError, match="measurement_seal_rows.*not accepted"):
        replay_crucible(memory.store, template)


def test_old_pure_descriptor_template_remains_supported(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template.pop("replay_binding")

    pack = replay_crucible(memory.store, template)

    assert len(pack["replays"]) == 5
    assert "replay_binding" not in pack


def test_schema_less_legacy_template_remains_supported(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template.pop("replay_binding")
    template.pop("schema")

    pack = replay_crucible(memory.store, template)

    assert pack["schema"] == "crucible.replay-pack/1"
    assert len(pack["replays"]) == 5
    assert "replay_binding" not in pack


def test_schema_less_legacy_template_rejects_omitted_row_against_global_seal(
        tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template.pop("replay_binding")
    template.pop("schema")
    template["replays"].pop()

    with pytest.raises(ReplayBindingError, match="assessment measurement seal binding"):
        replay_crucible(memory.store, template)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "crucible.replay-set/999"),
        ("descriptor_count", 4),
        ("descriptor_count", True),
        ("skipped_count", -1),
        ("skipped_count", False),
        ("sha256", "0" * 64),
    ],
)
def test_replay_rejects_tampered_replay_binding(tmp_path, field, value):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replay_binding"][field] = value

    with pytest.raises(ReplayBindingError, match="replay binding"):
        replay_crucible(memory.store, template)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "mneme.recheck/999", "unsupported recheck schema"),
        ("oracle", "shell:exec/v1", "unsupported recheck oracle"),
    ],
)
def test_replay_rejects_unsupported_descriptor_protocol(tmp_path, field, value, message):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replays"][0]["recheck"][field] = value

    with pytest.raises(ReplayBindingError, match=message):
        replay_crucible(memory.store, template)


def test_replay_rejects_descriptor_with_execution_fields(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replays"][0]["recheck"]["command"] = "anything"

    with pytest.raises(ReplayBindingError, match="descriptor fields"):
        replay_crucible(memory.store, template)


def test_replay_rejects_swapped_descriptors(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replays"][0]["recheck"], template["replays"][1]["recheck"] = (
        template["replays"][1]["recheck"], template["replays"][0]["recheck"])

    with pytest.raises(ReplayBindingError, match="claim/descriptor binding"):
        replay_crucible(memory.store, template)


def test_replay_rejects_changed_target_grounding(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    memory.update("memory-1", "Changed derived statement", reason="fixture")

    with pytest.raises(ReplayBindingError, match="grounding binding"):
        replay_crucible(memory.store, template)


def test_replay_rejects_raw_target_tamper_even_when_expected_state_is_already_drift(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    # Establish a legitimate DRIFT/1.0 measurement before template generation.
    memory.store.add_turn("turn-1", "qcr", "user", "Changed source statement")
    template = _template(memory)
    assert template["replays"][0]["expected_measurement"]["deviation"] == 1.0
    # Tamper with the target bytes while preserving its stored content hash.
    memory.store.conn.execute(
        "UPDATE memories SET text=? WHERE id=?",
        ("Changed target bytes hidden behind the same DRIFT deviation", "memory-1"),
    )
    memory.store.conn.commit()

    with pytest.raises(ReplayBindingError, match="grounding binding"):
        replay_crucible(memory.store, template)


def test_replay_uses_one_sqlite_snapshot_across_binding_and_drift_reads(tmp_path):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    memory.store.conn.execute("PRAGMA journal_mode=WAL")
    memory.store.add_turn("turn-1", "qcr", "user", "Changed source statement")
    template = _template(memory)
    before = next(
        row for row in memory.drift()["verdicts"]
        if row["memory_id"] == "memory-1"
    )
    assert before["verdict"] == "DRIFT"

    writer = sqlite3.connect(state_path)
    original_memory = memory.store.memory
    writer_committed = False

    def memory_with_interleaved_writer(memory_id):
        nonlocal writer_committed
        row = original_memory(memory_id)
        if memory_id == "memory-1" and not writer_committed:
            writer.execute(
                "UPDATE memories SET text=? WHERE id=?",
                ("Concurrent target bytes hidden behind the old hash", "memory-1"),
            )
            writer.commit()
            writer_committed = True
        return row

    memory.store.memory = memory_with_interleaved_writer
    try:
        pack = replay_crucible(memory.store, template)
    finally:
        memory.store.memory = original_memory
        writer.close()

    replay = pack["replays"][0]
    assert writer_committed is True
    assert replay["mneme_verdict"] == before["verdict"]
    assert replay["mneme_reason"] == before["reason"]
    after = next(
        row for row in memory.drift()["verdicts"]
        if row["memory_id"] == "memory-1"
    )
    assert after["reason"] != before["reason"]


@pytest.mark.parametrize("target", ["claim_id", "claim_sha256"])
def test_replay_rejects_claim_measurement_mismatch(tmp_path, target):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replays"][0]["expected_measurement"][target] = "wrong"

    with pytest.raises(ReplayBindingError, match="claim/measurement binding"):
        replay_crucible(memory.store, template)


def test_replay_rejects_tampered_measurement_contract(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    template["replays"][0]["expected_measurement"]["method"] = "tampered"

    with pytest.raises(ReplayBindingError, match="measurement contract binding"):
        replay_crucible(memory.store, template)


def test_source_drift_and_missing_source_become_replayed_deviations(tmp_path):
    memory = _state(tmp_path / "mneme.db")
    template = _template(memory)
    memory.store.add_turn("turn-1", "qcr", "user", "Changed source statement")
    memory.store.conn.execute("DELETE FROM turns WHERE id=?", ("turn-2",))
    memory.store.conn.commit()

    pack = replay_crucible(memory.store, template)
    by_id = {row["measurement"]["claim_id"]: row for row in pack["replays"]}

    assert by_id["memory-1"]["measurement"]["deviation"] == 1.0
    assert by_id["memory-1"]["mneme_verdict"] == "DRIFT"
    assert by_id["memory-2"]["measurement"]["deviation"] is None
    assert by_id["memory-2"]["mneme_verdict"] == "UNVERIFIABLE"


def test_cli_writes_strict_utf8_pack_and_refuses_overwrite(tmp_path, capsys):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    template = _template(memory)
    template_path = tmp_path / "template.json"
    pack_path = tmp_path / "pack.json"
    template_path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    memory.close()
    state_sha_before = hashlib.sha256(state_path.read_bytes()).hexdigest()

    args = ["--state", str(state_path), "replay-crucible", str(template_path),
            "--out", str(pack_path)]
    assert main(args) == 0
    assert hashlib.sha256(state_path.read_bytes()).hexdigest() == state_sha_before
    raw = pack_path.read_bytes()
    decoded = raw.decode("utf-8", errors="strict")
    decoded_pack = json.loads(decoded)
    assert decoded_pack["schema"] == "crucible.replay-pack/1"
    assert decoded_pack["replays"][4]["measurement"]["claim_id"] == "memory-5"
    published = pack_path.read_bytes()
    assert main(args) != 0
    assert "already exists" in capsys.readouterr().err
    assert pack_path.read_bytes() == published
    assert list(tmp_path.glob(f".{pack_path.name}.*.tmp")) == []


def test_cli_cleans_partial_temp_and_retry_succeeds_after_write_error(
        tmp_path, capsys, monkeypatch):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    template = _template(memory)
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    memory.close()
    pack_path = tmp_path / "pack.json"
    args = ["--state", str(state_path), "replay-crucible", str(template_path),
            "--out", str(pack_path)]

    real_write = os.write
    writes = 0

    def fail_after_partial_write(fd, data):
        nonlocal writes
        writes += 1
        if writes == 1:
            halfway = max(1, len(data) // 2)
            return real_write(fd, data[:halfway])
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(os, "write", fail_after_partial_write)
    assert main(args) != 0
    captured = capsys.readouterr()
    assert "simulated mid-write failure" in captured.err
    assert "Traceback" not in captured.err
    assert not pack_path.exists()
    assert list(tmp_path.glob(f".{pack_path.name}.*.tmp")) == []

    monkeypatch.setattr(os, "write", real_write)
    assert main(args) == 0
    assert json.loads(pack_path.read_text(encoding="utf-8"))["schema"] == (
        "crucible.replay-pack/1")


def test_cli_cleans_temp_when_staged_file_close_fails(tmp_path, capsys, monkeypatch):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    template = _template(memory)
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    memory.close()
    pack_path = tmp_path / "pack.json"

    real_close = os.close
    failed = False

    def close_then_fail(fd):
        nonlocal failed
        if not failed:
            failed = True
            real_close(fd)
            raise OSError("simulated staged close failure")
        return real_close(fd)

    monkeypatch.setattr(os, "close", close_then_fail)
    rc = main(["--state", str(state_path), "replay-crucible", str(template_path),
               "--out", str(pack_path)])

    captured = capsys.readouterr()
    assert rc != 0
    assert "simulated staged close failure" in captured.err
    assert "Traceback" not in captured.err
    assert not pack_path.exists()
    assert list(tmp_path.glob(f".{pack_path.name}.*.tmp")) == []


def test_atomic_publisher_uses_no_replace_rename_on_windows(tmp_path, monkeypatch):
    output = tmp_path / "pack.json"
    real_rename = os.rename
    calls = []

    def rename(source, destination):
        calls.append((Path(source), Path(destination)))
        return real_rename(source, destination)

    def unexpected_link(*_args, **_kwargs):
        raise AssertionError("Windows publication must not require hard links")

    monkeypatch.setattr(cli_module, "_WINDOWS", True, raising=False)
    monkeypatch.setattr(cli_module.os, "rename", rename)
    monkeypatch.setattr(cli_module.os, "link", unexpected_link)

    warning = cli_module._publish_new_file(output, b"complete")

    assert warning is None
    assert output.read_bytes() == b"complete"
    assert len(calls) == 1


def test_atomic_publisher_reports_cleanup_warning_after_committed_link(
        tmp_path, monkeypatch):
    output = tmp_path / "pack.json"
    real_unlink = Path.unlink

    def fail_temp_unlink(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise OSError("simulated committed-temp cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli_module, "_WINDOWS", False, raising=False)
    monkeypatch.setattr(Path, "unlink", fail_temp_unlink)

    warning = cli_module._publish_new_file(output, b"complete")

    assert output.read_bytes() == b"complete"
    assert "published" in warning
    assert "cleanup failure" in warning


def test_atomic_publisher_preserves_destination_conflict_when_cleanup_fails(
        tmp_path, monkeypatch):
    output = tmp_path / "pack.json"
    output.write_bytes(b"existing")
    real_unlink = Path.unlink

    def fail_temp_unlink(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise OSError("simulated conflict-temp cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli_module, "_WINDOWS", False, raising=False)
    monkeypatch.setattr(Path, "unlink", fail_temp_unlink)

    with pytest.raises(FileExistsError):
        cli_module._publish_new_file(output, b"replacement")

    assert output.read_bytes() == b"existing"


def test_cli_rejects_invalid_utf8_without_traceback(tmp_path, capsys):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    memory.close()
    template_path = tmp_path / "invalid-utf8.json"
    template_path.write_bytes(b'{"assessment":"\xff"}')
    output_path = tmp_path / "pack.json"

    rc = main(["--state", str(state_path), "replay-crucible", str(template_path),
               "--out", str(output_path)])

    captured = capsys.readouterr()
    assert rc != 0
    assert "replay-crucible failed: template is not valid UTF-8" in captured.err
    assert "Traceback" not in captured.err
    assert not output_path.exists()


@pytest.mark.parametrize("missing_column", [
    "source_hashes",
    "created_ord",
    "valid_until",
])
def test_cli_names_incompatible_read_only_schema_without_mutation(
        tmp_path, capsys, missing_column):
    state_path = tmp_path / "legacy.db"
    memory = _state(state_path)
    template = _template(memory)
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    memory.close()
    connection = sqlite3.connect(state_path)
    connection.execute(f"ALTER TABLE memories DROP COLUMN {missing_column}")
    connection.commit()
    connection.close()
    state_sha_before = hashlib.sha256(state_path.read_bytes()).hexdigest()
    output_path = tmp_path / "pack.json"

    rc = main(["--state", str(state_path), "replay-crucible", str(template_path),
               "--out", str(output_path)])

    captured = capsys.readouterr()
    assert rc != 0
    assert "read-only Store schema incompatible" in captured.err
    assert missing_column in captured.err
    assert "Traceback" not in captured.err
    assert not output_path.exists()
    assert hashlib.sha256(state_path.read_bytes()).hexdigest() == state_sha_before


@pytest.mark.parametrize("duplicate", ["schema", "replay_binding"])
def test_cli_rejects_duplicate_template_keys_without_output(
        tmp_path, capsys, duplicate):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    template = _template(memory)
    template_text = json.dumps(template)
    if duplicate == "schema":
        template_text = template_text.replace(
            '"schema": "crucible.replay-template/1"',
            '"schema": "crucible.replay-template/999", '
            '"schema": "crucible.replay-template/1"',
            1,
        )
    else:
        template_text = template_text.replace(
            '"replay_binding": {',
            '"replay_binding": null, "replay_binding": {',
            1,
        )
    template_path = tmp_path / "duplicate-template.json"
    template_path.write_text(template_text, encoding="utf-8")
    memory.close()
    output_path = tmp_path / "pack.json"

    rc = main(["--state", str(state_path), "replay-crucible", str(template_path),
               "--out", str(output_path)])

    captured = capsys.readouterr()
    assert rc != 0
    assert f"duplicate JSON key {duplicate!r}" in captured.err
    assert "Traceback" not in captured.err
    assert not output_path.exists()


def test_cli_does_not_leave_partial_pack_for_unencodable_json(tmp_path, capsys):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    template = _template(memory)
    template["assessment"]["thesis_id"] = "\ud800"
    template_path = tmp_path / "surrogate-template.json"
    # Default ensure_ascii=True makes this a valid UTF-8 JSON input containing
    # an escaped surrogate that cannot be emitted by ensure_ascii=False.
    template_path.write_text(json.dumps(template), encoding="utf-8")
    memory.close()
    state_sha_before = hashlib.sha256(state_path.read_bytes()).hexdigest()
    output_path = tmp_path / "pack.json"

    rc = main(["--state", str(state_path), "replay-crucible", str(template_path),
               "--out", str(output_path)])

    captured = capsys.readouterr()
    assert rc != 0
    assert "replay-crucible failed: replay pack is not valid UTF-8" in captured.err
    assert "Traceback" not in captured.err
    assert not output_path.exists()
    assert hashlib.sha256(state_path.read_bytes()).hexdigest() == state_sha_before


def test_cli_names_unencodable_legacy_measurement_seal_binding(tmp_path, capsys):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    template = _template(memory)
    template.pop("replay_binding")
    template["replays"][0]["claim"]["sha256"] = "\ud800"
    template["replays"][0]["expected_measurement"]["claim_sha256"] = "\ud800"
    template_path = tmp_path / "legacy-surrogate-template.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    memory.close()
    output_path = tmp_path / "pack.json"

    rc = main(["--state", str(state_path), "replay-crucible", str(template_path),
               "--out", str(output_path)])

    captured = capsys.readouterr()
    assert rc != 0
    assert "assessment measurement seal binding is not canonically encodable" in captured.err
    assert "Traceback" not in captured.err
    assert not output_path.exists()


def test_cli_names_binding_failure_and_returns_nonzero(tmp_path, capsys):
    state_path = tmp_path / "mneme.db"
    memory = _state(state_path)
    template = _template(memory)
    template["assessment"] = None
    template_path = tmp_path / "bad-template.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    memory.close()

    rc = main(["--state", str(state_path), "replay-crucible", str(template_path),
               "--out", str(tmp_path / "pack.json")])

    assert rc != 0
    assert "replay-crucible failed: assessment binding" in capsys.readouterr().err


def test_interop_manifest_uses_canonical_crucible_replay_ports():
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "mneme.interop.json").read_text(
            encoding="utf-8"))
    emitted = {row["capability"] for row in manifest["emits"]}
    consumed = {row["capability"] for row in manifest["consumes"]}

    assert "crucible.replay-pack/1" in emitted
    assert "crucible.replay-template/1" in consumed
    assert "crucible.recheck-pack/1" not in json.dumps(manifest, sort_keys=True)
