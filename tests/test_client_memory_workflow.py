"""Exercise the opt-in adapter with the existing synthetic snapshot fixture."""
import importlib.util
import io
import json
from pathlib import Path
import sys


def test_explicit_memory_grant_remember_and_recall(tmp_path,monkeypatch,snapshot_root):
    path=Path(__file__).resolve().parents[1]/'client-plugin/server/serve.py'
    spec=importlib.util.spec_from_file_location('client_entry',path)
    entry=importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
    state=tmp_path/'bound.db'
    monkeypatch.setenv('MNEME_STATE',str(state))
    requests=[{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'mneme.remember',
        'arguments':{'session':'synthetic','turns':[{'role':'user','text':'I live in Oslo.'}]}}},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'mneme.recall',
        'arguments':{'query':'Oslo'}}}]
    output=io.StringIO()
    monkeypatch.setattr(sys,'stdin',io.StringIO(''.join(json.dumps(r)+'\n' for r in requests)))
    monkeypatch.setattr(sys,'stdout',output)
    assert entry.main(['--allow-memory-write']) == 0
    rows=[json.loads(line) for line in output.getvalue().splitlines()]
    assert state.exists()
    assert not any(row['result'].get('isError') for row in rows)
    recall=json.loads(rows[1]['result']['content'][0]['text'])
    assert recall['hits'] and 'Oslo' in str(recall)
