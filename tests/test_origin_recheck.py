"""Local-origin freshness checks for Gather-ingested Mneme memories.

These tests keep Mneme's internal drift semantics separate from an explicit,
operator-scoped local origin recheck. The recheck supports only Gather local docs
(`source=docs`, `method=file-read`) under a caller-provided allowed root.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory
from mneme.cli import main as cli_main
from mneme.mcp import handle_request
import mneme.origin as origin_module
from mneme.origin import GATHER_DOCS_FILE_READ_PROFILE, recheck_local_origins


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ingest_local_doc(tmp_path: Path, *, text: str = "I use local origin recheck for release review.",
                      bytes_body: bytes | None = None, origin: dict | None = None,
                      allowed_subdir: str = "allowed"):
    allowed = tmp_path / allowed_subdir
    allowed.mkdir()
    source = allowed / "note.txt"
    if bytes_body is None:
        source.write_text(text, encoding="utf-8")
    else:
        source.write_bytes(bytes_body)
    item = {
        "id": "doc-1",
        "text": text,
        "source": "docs",
        "ref": str(source),
        "method": "file-read",
        "sha256": _hash_text(text),
    }
    if origin:
        item.update(origin)
    mem = AgentMemory(":memory:")
    summary = mem.ingest_gather("research", [item])
    return mem, summary["provenance"][0]["memory_id"], source, allowed


def test_local_origin_recheck_matches_gather_docs_normalized_text(tmp_path):
    mem, memory_id, _source, allowed = _ingest_local_doc(tmp_path)

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["schema"] == "mneme.local-origin-recheck/1"
    assert report["overall"] == "MATCH"
    assert report["checked"] == 1
    assert report["origins"][0]["profile"] == GATHER_DOCS_FILE_READ_PROFILE
    assert report["origins"][0]["verdict"] == "MATCH"
    assert "text" not in report["origins"][0]
    assert "raw byte integrity" in " ".join(report["origins"][0]["does_not_prove"])


def test_local_origin_recheck_detects_changed_normalized_text(tmp_path):
    mem, memory_id, source, allowed = _ingest_local_doc(tmp_path)
    source.write_text("I use changed local origin recheck for release review.", encoding="utf-8")

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "DRIFT"
    assert report["origins"][0]["verdict"] == "DRIFT"
    assert report["origins"][0]["reason"] == "normalized text hash differs from origin receipt"


def test_local_origin_recheck_reports_missing_source(tmp_path):
    mem, memory_id, source, allowed = _ingest_local_doc(tmp_path)
    source.unlink()

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "UNVERIFIABLE"
    assert report["origins"][0]["verdict"] == "UNVERIFIABLE"
    assert "missing" in report["origins"][0]["reason"]


def test_local_origin_recheck_matches_crlf_when_gather_normalized_text_matches(tmp_path):
    text = "I use local origin recheck for release review.\nMy receipts use normalized text.\n"
    mem, memory_id, _source, allowed = _ingest_local_doc(
        tmp_path,
        text=text,
        bytes_body=text.replace("\n", "\r\n").encode("utf-8"),
    )

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "MATCH"
    assert report["origins"][0]["comparison"] == "normalized_text_sha256"


def test_local_origin_recheck_refuses_invalid_utf8_lossy_profile(tmp_path):
    text = "I use local origin recheck for release review.�"
    mem, memory_id, _source, allowed = _ingest_local_doc(
        tmp_path,
        text=text,
        bytes_body=b"I use local origin recheck for release review.\xff",
    )

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "UNVERIFIABLE"
    assert "invalid UTF-8" in report["origins"][0]["reason"]


def test_local_origin_recheck_detects_wrong_origin_hash(tmp_path):
    mem, memory_id, _source, allowed = _ingest_local_doc(
        tmp_path,
        origin={"sha256": "0" * 64},
    )

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "DRIFT"
    assert report["origins"][0]["origin_sha256"] == "0" * 64


def test_local_origin_recheck_rejects_unsupported_method_and_native_chain(tmp_path):
    mem = AgentMemory(":memory:")
    summary = mem.ingest_gather("research", [{
        "id": "web-1",
        "text": "I use browser evidence for release review.",
        "source": "web",
        "ref": "https://example.com/profile",
        "method": "browser-extract",
        "sha256": "a" * 64,
    }])
    unsupported = mem.recheck_local_origin(summary["provenance"][0]["memory_id"], allowed_root=tmp_path)
    assert unsupported["overall"] == "UNVERIFIABLE"
    assert "unsupported" in unsupported["origins"][0]["reason"]

    native = AgentMemory(":memory:")
    native.remember("s", [{"id": "t1", "role": "user", "text": "I use native memories."}])
    atom = native.store.memories(layer="L1")[0]
    no_origin = native.recheck_local_origin(atom["id"], allowed_root=tmp_path)
    assert no_origin["overall"] == "UNVERIFIABLE"
    assert no_origin["origins"][0]["origin_present"] is False


def test_local_origin_recheck_rejects_outside_allowed_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "note.txt"
    text = "I use outside roots for a negative control."
    source.write_text(text, encoding="utf-8")
    mem = AgentMemory(":memory:")
    summary = mem.ingest_gather("research", [{
        "id": "doc-1", "text": text, "source": "docs", "ref": str(source),
        "method": "file-read", "sha256": _hash_text(text),
    }])
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    report = mem.recheck_local_origin(summary["provenance"][0]["memory_id"], allowed_root=allowed)

    assert report["overall"] == "UNVERIFIABLE"
    assert "outside allowed root" in report["origins"][0]["reason"]


def test_local_origin_recheck_rejects_symlink_or_hardlink(tmp_path):
    mem, memory_id, source, allowed = _ingest_local_doc(tmp_path)
    linked = allowed / "linked.txt"
    try:
        linked.symlink_to(source)
        kind = "symlink"
    except OSError:
        try:
            os.link(source, linked)
            kind = "hardlink"
        except OSError:
            pytest.skip("neither symlink nor hardlink creation is available")
    origin = mem.store.turn_origin(mem.provenance_chain(memory_id)["chain"][0]["turn_id"])
    origin["ref"] = str(linked)
    mem.store.conn.execute("UPDATE turns SET origin=?", (json.dumps(origin),))
    mem.store.conn.commit()

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "UNVERIFIABLE"
    assert kind in report["origins"][0]["reason"] or "link" in report["origins"][0]["reason"]


def test_local_origin_recheck_uses_bounded_read_for_oversized_file(tmp_path, monkeypatch):
    mem, memory_id, source, allowed = _ingest_local_doc(tmp_path)
    source.write_bytes(b"A" * 64)

    def unbounded_read_bytes(_path):
        raise AssertionError("origin recheck used unbounded Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", unbounded_read_bytes)

    report = recheck_local_origins(mem, memory_id, allowed_root=allowed, max_bytes=8)

    assert report["overall"] == "UNVERIFIABLE"
    assert "exceeds maximum read size" in report["origins"][0]["reason"]


def test_local_origin_recheck_rejects_target_replaced_after_validation(tmp_path, monkeypatch):
    mem, memory_id, source, allowed = _ingest_local_doc(tmp_path)
    attacker_dir = tmp_path / "outside"
    attacker_dir.mkdir()
    attacker = attacker_dir / "note.txt"
    attacker.write_text("I am a swapped target.", encoding="utf-8")
    original_safe_path = origin_module._safe_origin_path

    def race_after_validation(ref, allowed_root):
        result = original_safe_path(ref, allowed_root)
        source.unlink()
        attacker.replace(source)
        return result

    monkeypatch.setattr(origin_module, "_safe_origin_path", race_after_validation)

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "UNVERIFIABLE"
    assert "changed during validation/open" in report["origins"][0]["reason"]


def test_local_origin_recheck_rejects_ancestor_replaced_after_validation(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    nested = allowed / "nested"
    nested.mkdir(parents=True)
    source = nested / "note.txt"
    text = "I use ancestor swap controls."
    source.write_text(text, encoding="utf-8")
    mem = AgentMemory(":memory:")
    summary = mem.ingest_gather("research", [{
        "id": "doc-1", "text": text, "source": "docs", "ref": str(source),
        "method": "file-read", "sha256": _hash_text(text),
    }])
    memory_id = summary["provenance"][0]["memory_id"]
    attacker_nested = tmp_path / "attacker-nested"
    attacker_nested.mkdir()
    (attacker_nested / "note.txt").write_text("I am an ancestor swap.", encoding="utf-8")
    backup_nested = tmp_path / "validated-nested"
    original_safe_path = origin_module._safe_origin_path

    def race_after_validation(ref, allowed_root):
        result = original_safe_path(ref, allowed_root)
        nested.replace(backup_nested)
        attacker_nested.replace(nested)
        return result

    monkeypatch.setattr(origin_module, "_safe_origin_path", race_after_validation)

    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)

    assert report["overall"] == "UNVERIFIABLE"
    assert "changed during validation/open" in report["origins"][0]["reason"]


@pytest.mark.skipif(os.name == "nt" or not hasattr(os, "mkfifo"),
                    reason="POSIX FIFO liveness control")
def test_local_origin_recheck_rejects_fifo_replacement_without_blocking(tmp_path):
    import subprocess

    child = r'''
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])

from mneme import AgentMemory
import mneme.origin as origin_module

if os.open not in getattr(os, "supports_dir_fd", set()):
    print(json.dumps({"skip": "os.open dir_fd is unavailable"}))
    raise SystemExit(0)

tmp_path = Path(sys.argv[1])
allowed = tmp_path / "allowed"
allowed.mkdir()
source = allowed / "note.txt"
text = "I use FIFO swap liveness controls."
source.write_text(text, encoding="utf-8")
mem = AgentMemory(":memory:")
summary = mem.ingest_gather("research", [{
    "id": "doc-1",
    "text": text,
    "source": "docs",
    "ref": str(source),
    "method": "file-read",
    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
}])
memory_id = summary["provenance"][0]["memory_id"]
original_open = origin_module.os.open
state = {"swapped": False}

def hooked_open(path, flags, *args, **kwargs):
    if (not state["swapped"]
            and kwargs.get("dir_fd") is not None
            and os.fspath(path) == source.name):
        state["swapped"] = True
        source.unlink()
        os.mkfifo(source)
    return original_open(path, flags, *args, **kwargs)

def timeout(_signum, _frame):
    raise TimeoutError("origin recheck blocked opening a swapped FIFO")

origin_module.os.open = hooked_open
old_handler = signal.getsignal(signal.SIGALRM)
signal.signal(signal.SIGALRM, timeout)
signal.alarm(2)
try:
    report = mem.recheck_local_origin(memory_id, allowed_root=allowed)
finally:
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old_handler)
    origin_module.os.open = original_open

report["swapped"] = state["swapped"]
print(json.dumps(report))
'''
    proc = subprocess.run(
        [sys.executable, "-c", child, str(tmp_path),
         str(Path(__file__).resolve().parents[1] / "src")],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    if "skip" in report:
        pytest.skip(report["skip"])
    assert report["swapped"] is True
    assert report["overall"] == "UNVERIFIABLE"
    assert ("changed during validation/open" in report["origins"][0]["reason"]
            or "not a regular file" in report["origins"][0]["reason"])


def test_local_origin_recheck_rejects_allowed_root_alias(tmp_path):
    target = tmp_path / "allowed"
    target.mkdir()
    alias = tmp_path / "allowed-alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    mem = AgentMemory(":memory:")

    with pytest.raises(ValueError, match="allowed_root"):
        recheck_local_origins(mem, "missing", allowed_root=alias)


def test_local_origin_recheck_validates_max_bytes(tmp_path):
    mem = AgentMemory(":memory:")

    with pytest.raises(ValueError, match="max_bytes"):
        recheck_local_origins(mem, "missing", allowed_root=tmp_path, max_bytes=0)


def test_cli_origin_recheck_exit_codes_and_mcp_tool(tmp_path, capsys, monkeypatch):
    state = tmp_path / "mneme.db"
    text = "I use CLI origin recheck for release review."
    source = tmp_path / "note.txt"
    source.write_text(text, encoding="utf-8")
    mem = AgentMemory(state)
    summary = mem.ingest_gather("research", [{
        "id": "doc-1", "text": text, "source": "docs", "ref": str(source),
        "method": "file-read", "sha256": _hash_text(text),
    }])
    memory_id = summary["provenance"][0]["memory_id"]

    assert cli_main(["--state", str(state), "origin-recheck", memory_id, "--allowed-root", str(tmp_path)]) == 0
    ok = json.loads(capsys.readouterr().out)
    assert ok["overall"] == "MATCH"

    source.write_text("I use changed CLI origin recheck for release review.", encoding="utf-8")
    assert cli_main(["--state", str(state), "origin-recheck", memory_id, "--allowed-root", str(tmp_path)]) == 1
    changed = json.loads(capsys.readouterr().out)
    assert changed["overall"] == "DRIFT"

    monkeypatch.setenv("MNEME_STATE", str(state))
    tools = {tool["name"] for tool in handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert "mneme.origin_recheck" in tools
    response = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "mneme.origin_recheck",
        "arguments": {"memory_id": memory_id, "allowed_root": str(tmp_path)},
    }})
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["schema"] == "mneme.local-origin-recheck/1"


def test_origin_recheck_cli_and_mcp_do_not_create_missing_state(tmp_path, capsys, monkeypatch):
    missing_state = tmp_path / "missing.db"

    assert cli_main([
        "--state", str(missing_state),
        "origin-recheck", "missing-memory",
        "--allowed-root", str(tmp_path),
    ]) == 1
    captured = capsys.readouterr()
    assert "origin-recheck failed" in captured.err
    assert not missing_state.exists()

    monkeypatch.setenv("MNEME_STATE", str(missing_state))
    response = handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "mneme.origin_recheck",
        "arguments": {"memory_id": "missing-memory", "allowed_root": str(tmp_path)},
    }})

    assert response["result"]["isError"] is True
    assert not missing_state.exists()
