"""Expand MCPB setup values as string substitutions and check native launch grants."""
import json
import subprocess


def expanded_args(manifest, **values):
    settings = {key: spec.get('default') for key, spec in manifest['user_config'].items()}
    settings.update(values)
    args = manifest['server']['mcp_config']['args']
    for key, value in settings.items():
        rendered = json.dumps(value) if isinstance(value, (bool, type(None))) else str(value)
        args = [arg.replace('${user_config.' + key + '}', rendered) for arg in args]
    return args


def check_setup(executable, manifest, tool, env, root):
    keys = ['memory_write'] if tool == 'mneme' else ['write', 'exec']
    cases = [{}] + [{key: True} for key in keys]
    for values in cases:
        call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': tool + '.status', 'arguments': {}}}
        result = subprocess.run([str(executable), *expanded_args(manifest, **values)],
            input=json.dumps(call) + '\n', capture_output=True, text=True,
            env=env, cwd=root, timeout=45)
        if result.returncode:
            raise ValueError('MCPB boolean setup failed to launch')
        row = json.loads(result.stdout)
        body = json.loads(row['result']['content'][0]['text'])
        if tool == 'mneme':
            if body['client_grants']['memory_write'] != values.get('memory_write', False):
                raise ValueError('MCPB memory grant differs from setup')
        else:
            expected_exec = values.get('exec', False)
            expected_write = values.get('write', False) or expected_exec
            if body['grants']['allow_write'] != expected_write or body['grants']['allow_exec'] != expected_exec:
                raise ValueError('MCPB Relay grants differ from setup')
    name = 'mneme.remember' if tool == 'mneme' else 'local_agent_run'
    arguments = ({'session': 'refused', 'turns': [], 'allow_memory_write': True} if tool == 'mneme'
                 else {'goal': 'refuse', 'check': 'echo forbidden', 'allow_exec': True, 'allow_write': True})
    call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments}}
    result = subprocess.run([str(executable), *expanded_args(manifest)],
        input=json.dumps(call) + '\n', capture_output=True, text=True, env=env, cwd=root, timeout=45)
    if result.returncode:
        raise ValueError('default MCPB setup process failed')
    refused = json.loads(result.stdout)
    if not (refused.get('error') or refused['result'].get('isError')):
        raise ValueError('tool arguments widened default MCPB permissions')
    for key in keys:
        for value in ('', 'yes', 'TRUE', '1', None, '${user_config.' + key + '}'):
            result = subprocess.run([str(executable), *expanded_args(manifest, **{key: value})],
                input='', capture_output=True, text=True, env=env, cwd=root, timeout=45)
            if not result.returncode or result.stdout:
                raise ValueError('malformed MCPB grant was accepted')
    return {'status': 'PASS', 'scope': 'expanded default, enabled and malformed MCPB setup arguments'}
