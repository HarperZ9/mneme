"""Exercise persistence and two-step forgetting in a synthetic bound native store."""
import json
from pathlib import Path
import subprocess


def check_workflow(executable, root, env, setup_args=None):
    root = Path(root) / 'workflow'; root.mkdir()
    state = root / 'memory.db'
    env = dict(env, MNEME_STATE=str(state))
    flags = setup_args['memory_write'] if setup_args else ['--allow-memory-write']
    for key in ('TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'):
        env[key] = str(root)

    def call(name, arguments):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': arguments}}
        result = subprocess.run([str(executable), *flags],
            input=json.dumps(request) + '\n', capture_output=True, text=True,
            cwd=root, env=env, timeout=45)
        if result.returncode:
            raise ValueError('native memory workflow process failed: ' + result.stderr)
        row = json.loads(result.stdout)
        if row.get('error'):
            raise ValueError('native memory workflow protocol error')
        return row['result']

    def value(result):
        if result.get('isError'):
            raise ValueError('native memory workflow tool error: ' + str(result))
        return json.loads(result['content'][0]['text'])

    value(call('mneme.remember', {'session': 'native-fixture',
        'turns': [{'role': 'user', 'text': 'I live in Oslo.'}]}))
    recalled = value(call('mneme.recall', {'query': 'Oslo'}))
    if not state.is_file() or not recalled['hits'] or 'Oslo' not in str(recalled['hits']):
        raise ValueError('native persistence did not survive process restart')
    memory_id = recalled['hits'][0]['memory_id']
    value(call('mneme.provenance', {'memory_id': memory_id}))
    plan = value(call('mneme.forget', {'memory_id': memory_id}))
    if plan.get('client_snapshot_scope', {}).get('legacy_global_snapshots') != 'not inspected or modified':
        raise ValueError('native forget omitted its legacy snapshot limit')
    if not value(call('mneme.recall', {'query': 'Oslo'}))['hits']:
        raise ValueError('forget plan deleted memory before confirmation')
    bad = call('mneme.forget', {'memory_id': memory_id, 'confirm_plan_sha256': '0' * 64})
    if not bad.get('isError'):
        raise ValueError('incorrect forget confirmation was accepted')
    erased = value(call('mneme.forget', {'memory_id': memory_id,
        'confirm_plan_sha256': plan['plan_sha256'], 'allow_collateral': True}))
    if 'copies_unchecked' not in erased.get('findings', []):
        raise ValueError('native erase claimed unchecked global copies were absent')
    if value(call('mneme.recall', {'query': 'Oslo'}))['hits']:
        raise ValueError('confirmed forget left recalled memory')
    value(call('mneme.audit', {}))
    for item in root.iterdir():
        if item.name not in {'memory.db', 'memory.db-wal', 'memory.db-shm', 'memory.db-journal'} and not item.name.startswith('.mneme-snapshots-'):
            raise ValueError('unexpected native workflow output: ' + item.name)
    return {'status': 'PASS', 'scope': 'remember, restarted recall, provenance, two-step forget, audit',
            'does_not_prove': ['semantic recall quality', 'OS sandbox confinement', 'migration of legacy global snapshots']}
