"""Client artifact and actual stdio controls, using only synthetic paths."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = 'mneme'
sys.path.insert(0, str(ROOT / 'scripts'))
from build_client_package import archive, build, entries, manifests, qualify


def invoke(script, tmp_path, requests, extra_env=None, args=()):
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','SYSTEMDRIVE'}}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), LOCALAPPDATA=str(tmp_path), APPDATA=str(tmp_path),
               MNEME_STATE=str(tmp_path / 'synthetic.db'), RELAY_MCP_ROOT=str(tmp_path),
               RELAY_ALLOW_WRITE='1', RELAY_ALLOW_EXEC='1')
    env.update(extra_env or {})
    command = [str(script)] if script.suffix == '.exe' else [sys.executable, '-I', '-S', str(script)]
    return subprocess.run([*command,*args], input=''.join(json.dumps(r)+'\n' for r in requests),
                          capture_output=True, text=True, env=env, cwd=tmp_path, timeout=30)


def request(mid, method, **params):
    return {'jsonrpc':'2.0', 'id':mid, 'method':method, 'params':params}


@pytest.fixture
def packaged(tmp_path):
    archive_path = build(tmp_path / 'output')[0]
    target = tmp_path / 'extracted'
    with zipfile.ZipFile(archive_path) as z:
        z.extractall(target)
    return target


def test_stdio_identity_and_unknown(packaged, tmp_path):
    p = invoke(packaged / 'server/serve.py', tmp_path, [request(1,'initialize'),
        request(2,'tools/list'), request(3,'tools/call',name=TOOL+'.status',arguments={}),
        request(4,'tools/call',name='destroy_everything',arguments={})])
    assert p.returncode == 0, p.stderr
    rows = [json.loads(s) for s in p.stdout.splitlines()]
    assert rows[0]['result']['serverInfo']['version'] == qualify('dev')[0]
    assert rows[1]['result']['tools']
    assert not rows[2].get('error') and not rows[2]['result'].get('isError')
    assert rows[3].get('error') or rows[3]['result'].get('isError')
    assert not (tmp_path/'synthetic.db').exists()


def test_bad_launch_argument(packaged, tmp_path):
    p = invoke(packaged/'server/serve.py',tmp_path,[],args=['--grant-all'])
    assert p.returncode != 0 and not p.stdout


@pytest.mark.skipif(TOOL == 'plexus', reason='Plexus has no mandatory state binding')
@pytest.mark.parametrize('value',['','relative/path'])
def test_binding_required(packaged,tmp_path,value):
    key = 'MNEME_STATE' if TOOL == 'mneme' else 'RELAY_MCP_ROOT'
    p = invoke(packaged/'server/serve.py',tmp_path,[],{key:value})
    assert p.returncode != 0 and 'absolute' in p.stderr


def test_package_deterministic(tmp_path):
    first=build(tmp_path/'a')[0]; second=build(tmp_path/'b')[0]
    assert first.read_bytes() == second.read_bytes()
    with pytest.raises(FileExistsError):
        build(tmp_path/'a')


def test_linked_payload_refused(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    target=tmp_path/'target'; target.mkdir()
    try:
        (source/'link').symlink_to(target,target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            raise
        p=subprocess.run(['cmd','/c','mklink','/J',str(source/'link'),str(target)],capture_output=True)
        assert p.returncode == 0
    with pytest.raises(ValueError,match='linked'):
        entries(source)


def test_manifest_permissions_and_parity():
    docs=manifests(qualify('dev')[0],True)
    native=json.loads(docs['manifest.json'])
    plugin=json.loads(docs['plugin.json'])
    assert native['version'] == plugin['version']
    assert native['server']['mcp_config']['args'] == ['--memory-write=${user_config.memory_write}']
    assert 'annotations' not in str(docs)


@pytest.mark.skipif(TOOL != 'mneme', reason='Mneme scope')
def test_memory_write_refused(packaged,tmp_path):
    p=invoke(packaged/'server/serve.py',tmp_path,[request(1,'tools/call',name='mneme.remember',
             arguments={'session':'test','turns':[{'role':'user','text':'synthetic'}]})])
    assert p.returncode == 0
    assert json.loads(p.stdout)['error']
    assert not (tmp_path/'synthetic.db').exists()


@pytest.mark.skipif(TOOL != 'relay', reason='Relay scope')
def test_relay_environment_cannot_grant(packaged,tmp_path):
    p=invoke(packaged/'server/serve.py',tmp_path,[request(1,'tools/call',name='local_agent_run',
        arguments={'goal':'do not run','root':str(tmp_path),'allow_write':True,'allow_exec':True})])
    assert p.returncode == 0
    row=json.loads(p.stdout)
    assert row['result'].get('isError'),row
    assert 'GRANT' in p.stdout.upper() or 'PERMISSION' in p.stdout.upper() or 'REFUSED' in p.stdout.upper()


@pytest.mark.skipif(TOOL != 'plexus', reason='Plexus scope')
def test_declarative_discovery(packaged,tmp_path):
    p=invoke(packaged/'server/serve.py',tmp_path,[request(1,'tools/call',name='plexus_discover',arguments={})])
    assert p.returncode == 0
    result=json.loads(json.loads(p.stdout)['result']['content'][0]['text'])
    assert result['organs'] and result['edges']


def test_client_root_expansion_is_client_specific():
    docs=manifests(qualify('dev')[0],False)
    assert b'${PLUGIN_ROOT}' in docs['mcp.json']
    assert b'${CLAUDE_PLUGIN_ROOT}' in docs['.mcp.json']
    assert b'${PLUGIN_ROOT}' not in docs['.mcp.json']
    assert b'${MNEME_STATE}' in docs['mcp.json'] and b'${MNEME_STATE}' not in docs['.mcp.json']


def test_claude_manifest_carries_directory_listing_and_prompts_for_bindings():
    docs=manifests(qualify('dev')[0],True)
    claude=json.loads(docs['.claude-plugin/plugin.json'])
    portable=json.loads(docs['plugin.json'])
    native=json.loads(docs['manifest.json'])
    for key in ('homepage','documentationUrl','supportUrl','privacyPolicyUrl','termsOfServiceUrl'):
        assert claude[key].startswith('https://')
    assert claude['repository'] == 'https://github.com/HarperZ9/mneme'
    assert claude['displayName'] == 'Mneme' and 5 <= len(claude['keywords']) <= 8
    assert all(k == k.lower() for k in claude['keywords'])
    assert {k: claude[k] for k in portable} == portable
    assert 'userConfig' not in portable and 'icon' not in portable
    assert set(claude['userConfig']) == {'state_path','memory_write'}
    for entry in claude['userConfig'].values():
        assert set(entry) <= {'type','title','description','required','default','sensitive'}
    assert claude['userConfig']['state_path']['required'] is True
    # The write switch mirrors the MCPB option: same title, text and default-off.
    assert claude['userConfig']['memory_write'] == native['user_config']['memory_write']
    server=json.loads(docs['.mcp.json'])['mcpServers'][TOOL]
    assert server['command'] == '${CLAUDE_PLUGIN_ROOT}/server/mneme-local.exe'
    source=json.loads(manifests(qualify('dev')[0],False)['.mcp.json'])['mcpServers'][TOOL]
    assert source['command'] == 'python3'
    assert source['args'] == ['-I','-S','-B','${CLAUDE_PLUGIN_ROOT}/server/serve.py',
                              '--memory-write=${user_config.memory_write}']
    assert source['env'] == {'MNEME_STATE':'${user_config.state_path}'}
    assert json.loads(docs['mcp.json'])['mcpServers'][TOOL]['env'] == {'MNEME_STATE':'${MNEME_STATE}'}


def test_committed_icon_is_a_square_png_the_directory_accepts():
    data=(ROOT/'client-plugin/.claude-plugin/icon.png').read_bytes()
    assert data[:8] == bytes([137,80,78,71,13,10,26,10]) and data[12:16] == b'IHDR'
    width,height=int.from_bytes(data[16:20],'big'),int.from_bytes(data[20:24],'big')
    assert width == height and 512 <= width <= 2048 and len(data) < 2*1024*1024


def test_committed_source_manifests_match_generated_contract():
    for name,expected in manifests(qualify('dev')[0],False).items():
        assert json.loads((ROOT/'client-plugin'/name).read_text()) == json.loads(expected), name


def test_source_zip_carries_icon_and_server_source(tmp_path):
    archive_path=build(tmp_path/'output')[0]
    with zipfile.ZipFile(archive_path) as z:
        names=set(z.namelist())
        assert z.read('.claude-plugin/icon.png') == (ROOT/'client-plugin/.claude-plugin/icon.png').read_bytes()
    assert 'server/serve.py' in names and 'server/src/mneme/mcp.py' in names


def test_claude_launch_values_start_the_extracted_server(packaged,tmp_path):
    # The exact strings ${user_config.*} substitution produces for a boolean and a path.
    for value,count in (('false',4),('true',11)):
        p=invoke(packaged/'server/serve.py',tmp_path,[request(1,'initialize'),request(2,'tools/list')],
                 args=['--memory-write='+value])
        assert p.returncode == 0, p.stderr
        assert len(json.loads(p.stdout.splitlines()[1])['result']['tools']) == count


def test_version_drift_refused(tmp_path,monkeypatch):
    import build_client_package as package
    (tmp_path/'pyproject.toml').write_text('[project]\nversion="0.9.0"\n')
    source=tmp_path/'src'/TOOL; source.mkdir(parents=True)
    (source/'__init__.py').write_text('__version__="0.8.0"\n')
    monkeypatch.setattr(package,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='versions differ'):
        package.qualify('dev')

@pytest.mark.parametrize('name',['.env','.env.json','state.db','credential.key','secret.token'])
def test_state_and_credentials_refused_before_archiving(tmp_path,name):
    root=tmp_path/'payload'; root.mkdir()
    (root/name).write_text('synthetic-not-a-secret')
    with pytest.raises(ValueError,match='unsupported package input'):
        entries(root)


def test_unreleased_candidate_cannot_be_packaged_as_release():
    if 'unreleased' in next(line for line in (ROOT/'CHANGELOG.md').read_text().splitlines() if line.startswith('## ')).lower():
        with pytest.raises(ValueError,match='unreleased marker'):
            qualify('release')


def test_native_builder_pins_reproducibility_environment(tmp_path,monkeypatch):
    import build_client_package as package
    class StopBeforeBuild(Exception):
        pass
    def capture(command,**kwargs):
        env=kwargs['env']
        assert env['PYTHONHASHSEED']=='0'
        assert env['SOURCE_DATE_EPOCH'].isdigit()
        assert 'SYNTHETIC_SECRET' not in env
        raise StopBeforeBuild
    monkeypatch.setenv('PYTHONHASHSEED','random')
    monkeypatch.setenv('SOURCE_DATE_EPOCH','1')
    monkeypatch.setenv('SYNTHETIC_SECRET','synthetic-not-a-secret')
    monkeypatch.setattr(package.sys,'platform','win32')
    monkeypatch.setattr(package.subprocess,'run',capture)
    monkeypatch.setattr(package,'qualify',lambda mode: ('0.3.0' if TOOL=='plexus' else '0.6.0','synthetic-head'))
    monkeypatch.setattr(package.subprocess,'check_output',lambda *a,**k:'1234567890\n')
    with pytest.raises(StopBeforeBuild):
        package.build(tmp_path/'native-build',native=True)
