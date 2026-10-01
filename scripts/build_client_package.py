"""Build source plugin or self-contained Windows client candidates."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = 'mneme'
DESCRIPTION = 'Local memory in a SQLite file you choose, with provenance on every recalled memory.'


def entries(root, extensions=frozenset({'.py', '.md', '.json'})):
    root = Path(root).absolute()
    for ancestor in [root, *root.parents]:
        if ancestor.is_symlink() or (ancestor.exists() and getattr(ancestor.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('linked input path')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('linked input entry')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            if path.name.lower().startswith('.env') or path.suffix.lower() not in extensions:
                raise ValueError('unsupported package input; state and credentials must stay outside package roots')
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def qualify(mode):
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    version = project['version']
    import ast
    module = ast.parse((ROOT / 'src' / TOOL / '__init__.py').read_text(encoding='utf-8-sig'))
    declared = [node.value.value for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets)
                and isinstance(node.value, ast.Constant)]
    if declared != [version]:
        raise ValueError('source and package versions differ')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if mode == 'release':
        heading = next((line for line in (ROOT / 'CHANGELOG.md').read_text().splitlines() if line.startswith('## ')), '')
        if version not in heading or 'unreleased' in heading.lower():
            raise ValueError('release notes must identify the final version without an unreleased marker')
        if not re.fullmatch(r'\d+\.\d+\.0', version):
            raise ValueError('mature releases must end in .0')
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
            raise ValueError('release source must be clean')
        tag = subprocess.check_output(['git', 'rev-parse', f'v{version}^{{commit}}'], cwd=ROOT, text=True).strip()
        if tag != head:
            raise ValueError('release tag must identify this HEAD')
    return version, head


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def manifests(version, native):
    executable = f'server/{TOOL}-local.exe'
    command = '${PLUGIN_ROOT}/' + executable if native else 'python3'
    args = [] if native else ['-I', '-S', '-B', '${PLUGIN_ROOT}/server/serve.py']
    binding = {'mneme': 'MNEME_STATE', 'relay': 'RELAY_MCP_ROOT'}.get(TOOL)
    env = {binding: '${' + binding + '}'} if binding else {}
    config = {'mcpServers': {TOOL: {'command': command, 'args': args, 'env': env, 'type': 'stdio'}}}
    plugin = {'name': TOOL + '-local', 'version': version,
              'description': DESCRIPTION,
              'author': {'name': 'Zain Dana Harper'}, 'license': 'FSL-1.1-MIT'}
    files = {'plugin.json': encoded(plugin), '.claude-plugin/plugin.json': encoded(plugin),
             '.codex-plugin/plugin.json': encoded({**plugin, 'skills': './skills/', 'mcpServers': './mcp.json'}),
             'mcp.json': encoded(config), '.mcp.json': encoded(config).replace(b'${PLUGIN_ROOT}', b'${CLAUDE_PLUGIN_ROOT}')}
    if native:
        mcp = {'command': '${__dirname}/' + executable, 'args': [],
               'env': {binding: '${user_config.local_path}'} if binding else {}}
        manifest = {'manifest_version': '0.3', **plugin, 'display_name': TOOL.title() + ' Local',
                    'server': {'type': 'binary', 'entry_point': executable, 'mcp_config': mcp},
                    'compatibility': {'platforms': ['win32']}}
        if binding:
            manifest['user_config'] = {'local_path': {'type': 'directory' if TOOL == 'relay' else 'file',
                'title': 'Launch root' if TOOL == 'relay' else 'Mneme state database',
                'description': f'Explicit absolute local path for {binding}.', 'required': True}}
        manifest['user_config']['memory_write'] = {'type': 'boolean', 'title': 'Allow memory changes',
            'description': 'Enable memory storage, recall, replay and two-step forgetting in the selected database. Some reads can initialize or migrate state.',
            'default': False, 'required': False}
        mcp['args'] = ['--memory-write=${user_config.memory_write}']
        files['manifest.json'] = encoded(manifest)
    return files


def archive(files, target):
    files = dict(files)
    files['PAYLOAD-SHA256SUMS'] = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in sorted(files.items())).encode()
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)


def write_sums(target, paths):
    """Write sha256sum lines for paths to target with LF endings on every OS.

    Path.write_text turns each newline into CRLF on Windows. GNU sha256sum 8.32
    and Perl shasum then read the file name with a trailing carriage return and
    fail to open it, so the bytes are written directly.
    """
    lines = ''.join(f'{hashlib.sha256(Path(p).read_bytes()).hexdigest()}  {Path(p).name}\n' for p in paths)
    Path(target).write_bytes(lines.encode('utf-8'))


def build(output, native=False, mode='dev'):
    version, head = qualify(mode)
    output = Path(output).absolute()
    if output.exists():
        raise FileExistsError('output must be a new directory')
    files = entries(ROOT / 'client-plugin')
    source = entries(ROOT / 'src' / TOOL)
    inputs = {f'src/{TOOL}/{name}': data for name, data in source.items()}
    inputs.update({f'client-plugin/{name}': data for name, data in files.items()})
    inputs.update({f'scripts/{name}': data for name, data in entries(ROOT / 'scripts', extensions={'.py', '.ps1', '.sh'}).items()})
    inputs['pyproject.toml'] = (ROOT / 'pyproject.toml').read_bytes()
    inputs['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    if mode == 'release':
        tracked = set(subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines())
        if set(inputs) - tracked:
            raise ValueError('release inputs must all be tracked authored files')
    source_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()}
    files.update(manifests(version, native))
    files['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    output.mkdir(parents=True)
    receipt = {'status': 'DEVELOPMENT_CANDIDATE' if mode == 'dev' else 'RELEASE_CANDIDATE',
               'version': version, 'head': head, 'source_sha256': source_hashes,
               'does_not_prove': ['installed client acceptance', 'marketplace admission', 'clean OS compatibility']}
    if native:
        if sys.platform != 'win32':
            raise ValueError('native build requires Windows x64')
        stage = output / 'stage'
        command = [sys.executable, '-m', 'PyInstaller', '--onefile', '--console', '--clean',
                   '--name', TOOL + '-local', '--paths', str(ROOT / 'src'),
                   '--hidden-import', TOOL + ('.local_mcp' if TOOL == 'relay' else '.mcp'),
                   '--distpath', str(stage), '--workpath', str(output / 'work'),
                   '--specpath', str(output / 'spec'), str(ROOT / 'client-plugin/server/serve.py')]
        for excluded in ('harness', 'cryptography', 'cffi', *({'mneme', 'relay', 'plexus'} - {TOOL})):
            command.extend(['--exclude-module', excluded])
        env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'SYSTEMDRIVE'}}
        epoch = subprocess.check_output(['git', 'show', '-s', '--format=%ct', head], cwd=ROOT, text=True).strip()
        if not epoch.isdigit():
            raise ValueError('source commit timestamp is invalid')
        env.update(SOURCE_DATE_EPOCH=epoch, PYTHONHASHSEED='0')
        receipt['native_build_environment'] = {'SOURCE_DATE_EPOCH': epoch, 'PYTHONHASHSEED': '0'}
        env['PATH'] = os.pathsep.join([sys.base_prefix, str(Path(env.get('SYSTEMROOT', 'C:/Windows')) / 'System32')])
        home = output / 'home'; home.mkdir()
        env.update({k: str(home) for k in ('HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA')})
        with (output / 'freeze.log').open('w') as log:
            subprocess.run(command, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        from native_build_provenance import dependencies
        receipt.update(python=sys.version, pyinstaller=importlib.metadata.version('pyinstaller'),
                       native_dependencies=dependencies(output / f'work/{TOOL}-local/Analysis-00.toc', sys.base_prefix))
        from check_native_client import check
        receipt['native_smoke'] = check(stage / f'{TOOL}-local.exe', version)
        files.pop('server/serve.py')
        files[f'server/{TOOL}-local.exe'] = (stage / f'{TOOL}-local.exe').read_bytes()
        files['PYTHON-LICENSE.txt'] = (Path(sys.base_prefix) / 'LICENSE.txt').read_bytes()
        dist = importlib.metadata.distribution('pyinstaller')
        copying = [dist.locate_file(p) for p in dist.files if str(p).endswith('/licenses/COPYING.txt')]
        if len(copying) != 1:
            raise ValueError('missing PyInstaller license')
        files['PYINSTALLER-LICENSE.txt'] = copying[0].read_bytes()
    else:
        files.update({f'server/src/{TOOL}/{name}': data for name, data in source.items()})
    if any((ROOT / name).read_bytes() != data for name, data in inputs.items()):
        raise ValueError('source changed during build')
    files['QUALIFICATION.json'] = encoded(receipt)
    label = '-dev' if mode == 'dev' else ''
    name = f'{TOOL}-{version}{label}-' + ('win-x64' if native else 'source-plugin')
    artifacts = [output / (name + suffix) for suffix in (('.zip', '.mcpb') if native else ('.zip',))]
    for target in artifacts:
        archive(files, target)
    write_sums(output / 'SHA256SUMS', artifacts)
    (output / 'build-receipt.json').write_bytes(encoded(receipt))
    return artifacts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--mode', choices=('dev', 'release'), default='dev')
    args = parser.parse_args()
    for item in build(args.output, args.native, args.mode):
        print(item)
