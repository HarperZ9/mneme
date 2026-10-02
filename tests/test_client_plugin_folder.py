"""The client-plugin folder on its own: what a directory install receives.

A directory install copies only client-plugin/, so the server code has to live
inside it. These tests launch the committed folder the way Claude Code does,
hold the vendored copy to src/, and check the directory's size limits."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'client-plugin'
sys.path.insert(0, str(ROOT / 'scripts'))
from build_client_package import SYNC_COMMAND, VENDORED, client_entries, vendored_files  # noqa: E402

IMAGES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
MISSING = 'mneme: the server code is missing from the plugin folder. Reinstall the plugin.'


def _committed_files():
    return sorted(p for p in PLUGIN.rglob('*') if p.is_file() and '__pycache__' not in p.parts)


def _vendored_on_disk():
    root = PLUGIN / VENDORED
    return {p.relative_to(PLUGIN).as_posix(): p.read_bytes().replace(b'\r\n', b'\n')
            for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def test_vendored_server_code_matches_src():
    expected, actual = vendored_files(), _vendored_on_disk()
    missing, extra = sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected))
    changed = sorted(n for n in set(expected) & set(actual) if expected[n] != actual[n])
    assert not (missing or extra or changed), (
        f'client-plugin/{VENDORED} is out of date with src/ (missing {missing}, extra {extra}, '
        f'changed {changed}). Run: {SYNC_COMMAND}')


def test_builder_scan_leaves_the_vendored_copy_out():
    assert not any(name.startswith(VENDORED) for name in client_entries())


def _launch(plugin_root, tmp_path, write='false', calls=()):
    server = json.loads((plugin_root / '.mcp.json').read_text())['mcpServers']['mneme']
    values = {'${CLAUDE_PLUGIN_ROOT}': str(plugin_root), '${user_config.memory_write}': write,
              '${user_config.state_path}': str(tmp_path / 'state.db')}

    def fill(text):
        for key, value in values.items():
            text = text.replace(key, value)
        assert '${' not in text, text
        return text
    command = sys.executable if server['command'] == 'python3' else fill(server['command'])
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
                                                                'SYSTEMDRIVE', 'USERPROFILE', 'HOME'}}
    env.update({k: fill(v) for k, v in server.get('env', {}).items()})
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    requests += [{'jsonrpc': '2.0', 'id': i, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}
                 for i, (name, args) in enumerate(calls, 3)]
    return subprocess.run([command, *[fill(a) for a in server['args']]], env=env, cwd=tmp_path,
                          input=''.join(json.dumps(r) + '\n' for r in requests),
                          capture_output=True, text=True, timeout=60)


def _isolated_copy(tmp_path):
    target = tmp_path / 'installed-plugin'
    shutil.copytree(PLUGIN, target, ignore=shutil.ignore_patterns('__pycache__'))
    return target


def test_plugin_folder_alone_starts_with_the_claude_launch_command(tmp_path):
    plugin = _isolated_copy(tmp_path)
    for write, count in (('false', 4), ('true', 11)):
        p = _launch(plugin, tmp_path, write)
        assert p.returncode == 0, p.stderr
        rows = [json.loads(line) for line in p.stdout.splitlines()]
        assert rows[0]['result']['serverInfo']['name'] == 'mneme'
        names = [tool['name'] for tool in rows[1]['result']['tools']]
        assert len(names) == count and 'mneme.status' in names


def test_missing_server_code_fails_with_one_clear_line(tmp_path):
    plugin = _isolated_copy(tmp_path)
    shutil.rmtree(plugin / 'server' / 'src')
    p = _launch(plugin, tmp_path)
    assert p.returncode == 1 and not p.stdout
    assert p.stderr.strip() == MISSING


def test_plugin_folder_fits_the_directory_limits():
    files = _committed_files()
    assert len(files) <= 512
    for path in files:
        size = path.stat().st_size
        limit = 2 * 1024 * 1024 if path.suffix.lower() in IMAGES else 256 * 1024
        assert size < limit, (path.relative_to(ROOT).as_posix(), size)


def test_no_gitattributes_inside_the_plugin_folder():
    assert not [p for p in PLUGIN.rglob('.gitattributes')]


def test_vendored_code_holds_no_model_extractor_or_cli():
    vendored = _vendored_on_disk()
    assert not [n for n, data in vendored.items() if b'OPENAI_API_KEY' in data]
    names = {n.rsplit('/', 1)[1] for n in vendored}
    assert not names & {'llm_extract.py', 'cli.py', '__main__.py'}


def _result(row):
    assert 'error' not in row, row
    return row['result']['content'][0]['text']


def test_vendored_closure_serves_every_tool_from_the_folder_alone(tmp_path):
    """A module missing from the trimmed copy shows up as an import error on the
    tool path that needs it, so drive every tool, including both forget steps."""
    plugin = _isolated_copy(tmp_path)
    turns = [{'role': 'user', 'text': 'I live in Denver.'}, {'role': 'user', 'text': 'My dog is Juniper.'}]
    first = _launch(plugin, tmp_path, 'true', [
        ('mneme.remember', {'session': 's', 'turns': turns}), ('mneme.recall', {'query': 'Denver'})])
    assert first.returncode == 0, first.stderr
    rows = [json.loads(line) for line in first.stdout.splitlines()]
    listed = {tool['name'] for tool in rows[1]['result']['tools']}
    memory = json.loads(_result(rows[3]))['hits'][0]['memory_id']
    plan = _launch(plugin, tmp_path, 'true', [('mneme.forget', {'memory_id': memory, 'reason': 'test'})])
    digest = json.loads(_result([json.loads(x) for x in plan.stdout.splitlines()][2]))['plan_sha256']
    calls = [('mneme.status', {}), ('mneme.doctor', {}), ('mneme.drift', {}), ('mneme.audit', {}),
             ('mneme.provenance', {'memory_id': memory}), ('mneme.to_crucible', {'all_users': True}),
             ('mneme.origin_recheck', {'memory_id': memory, 'allowed_root': str(tmp_path)}),
             ('mneme.replay_crucible', {'template': {'schema': 'crucible.replay-template/1', 'replays': []}}),
             ('mneme.forget', {'memory_id': memory, 'reason': 'test', 'confirm_plan_sha256': digest})]
    assert {name for name, _ in calls} | {'mneme.remember', 'mneme.recall'} == listed
    second = _launch(plugin, tmp_path, 'true', calls)
    assert second.returncode == 0, second.stderr
    out = first.stdout + plan.stdout + second.stdout
    assert 'No module named' not in out and 'ImportError' not in out
    texts = {name: _result(json.loads(line)) for (name, _), line in zip(calls, second.stdout.splitlines()[2:])}
    receipt = json.loads(texts['mneme.forget'])
    # The client reports legacy copies as unchecked, so the receipt says incomplete;
    # the erase itself ran through every module it needs.
    assert receipt['findings'] == ['copies_unchecked'] and receipt['structural']['rows_absent'] is True
