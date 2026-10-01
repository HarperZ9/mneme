"""A successful-looking protocol response cannot replace workflow evidence."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_workflow_rejects_protocol_success_without_effect(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/check_native_workflow.py'
    spec = importlib.util.spec_from_file_location('mneme_workflow', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    response = {'result': {'content': [{'type': 'text', 'text': json.dumps({'hits': [{'memory_id': 'claimed'}]})}]}}
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k:
        SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr=''))
    with pytest.raises(ValueError):
        module.check_workflow(tmp_path / 'fake.exe', tmp_path, {})
