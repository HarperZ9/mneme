"""Generated host-expanded setup arguments must preserve explicit launch authority."""
import json
import pytest
from test_client_package import packaged, invoke, request, TOOL
from build_client_package import manifests, qualify
from check_mcpb_setup import expanded_args


@pytest.mark.parametrize('enabled', [False, True])
def test_generated_setup_launches(packaged, tmp_path, enabled):
    manifest = json.loads(manifests(qualify('dev')[0], True)['manifest.json'])
    keys = ['memory_write'] if TOOL == 'mneme' else ['write', 'exec']
    for key in keys:
        assert manifest['user_config'][key]['type'] == 'boolean'
        assert manifest['user_config'][key]['default'] is False
        assert manifest['user_config'][key].get('required', False) is False
        values = {key: enabled}
        args = expanded_args(manifest, **values)
        p = invoke(packaged / 'server/serve.py', tmp_path,
            [request(1, 'tools/call', name=TOOL + '.status', arguments={})], args=args)
        assert p.returncode == 0, p.stderr
        body = json.loads(json.loads(p.stdout)['result']['content'][0]['text'])
        if TOOL == 'mneme':
            assert body['client_grants']['memory_write'] is enabled
        else:
            assert body['grants']['allow_write'] is enabled
            assert body['grants']['allow_exec'] is (enabled and key == 'exec')


@pytest.mark.parametrize('value', ['', 'yes', 'TRUE', '1', None, '${user_config.missing}'])
def test_malformed_setup_refused(packaged, tmp_path, value):
    manifest = json.loads(manifests(qualify('dev')[0], True)['manifest.json'])
    keys = ['memory_write'] if TOOL == 'mneme' else ['write', 'exec']
    for key in keys:
        p = invoke(packaged / 'server/serve.py', tmp_path, [],
            args=expanded_args(manifest, **{key: value}))
        assert p.returncode != 0 and not p.stdout


def test_default_setup_tool_arguments_cannot_grant(packaged, tmp_path):
    manifest = json.loads(manifests(qualify('dev')[0], True)['manifest.json'])
    name = 'mneme.remember' if TOOL == 'mneme' else 'local_agent_run'
    arguments = ({'session': 'refused', 'turns': [], 'allow_memory_write': True} if TOOL == 'mneme'
                 else {'goal': 'refuse', 'check': 'echo forbidden', 'allow_write': True, 'allow_exec': True})
    p = invoke(packaged / 'server/serve.py', tmp_path,
        [request(1, 'tools/call', name=name, arguments=arguments)], args=expanded_args(manifest))
    assert p.returncode == 0
    row = json.loads(p.stdout)
    assert row.get('error') or row['result'].get('isError')
    assert not (tmp_path / 'synthetic.db').exists()
