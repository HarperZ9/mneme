"""Local client adapter. No service, model, installation or implicit grant."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

TOOL = 'mneme'
if not getattr(sys, 'frozen', False):
    source = Path(__file__).resolve().parent / 'src'
    if not (source / 'mneme' / 'mcp.py').is_file():
        sys.stderr.write('mneme: the server code is missing from the plugin folder. Reinstall the plugin.\n')
        raise SystemExit(1)
    sys.path.insert(0, str(source))


def explicit_path(value, *, directory=False):
    path = Path(value or '')
    if not value or not path.is_absolute():
        raise ValueError('an explicit absolute local path is required')
    for part in [path, *path.parents]:
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0)
                                & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('linked path components are not accepted')
    if directory and not path.is_dir():
        raise ValueError('launch root must be an existing directory')
    if not directory and (not path.parent.is_dir() or path.is_dir()):
        raise ValueError('state must name a file in an existing directory')
    return str(path)


def mneme_serve(writable):
    from mneme import mcp
    safe = {'mneme.status', 'mneme.doctor', 'mneme.to_crucible', 'mneme.origin_recheck'}
    # Preserve Mneme's duplicate-key handling but avoid global snapshot cleanup
    # on a profile that cannot create or delete snapshots.
    if writable:
        mcp.snapshot_dir.startup_sweep()
    for line in sys.stdin:
        try:
            req = json.loads(line, object_pairs_hook=mcp._unique_json_object)
            if not isinstance(req, dict):
                raise ValueError('request must be an object')
            params = req.get('params') or {}
            if not isinstance(params, dict):
                raise ValueError('params must be an object')
            if req.get('method') == 'tools/call' and not writable and params.get('name') not in safe:
                response = mcp._err(req.get('id'), -32602, 'tool unavailable in bound export profile; explicit memory-write launch required')
            else:
                response = mcp.handle_request(req)
            if response and req.get('method') == 'tools/list' and not writable:
                response['result']['tools'] = [t for t in response['result']['tools'] if t['name'] in safe]
            if response and req.get('method') == 'tools/call' and params.get('name') in {'mneme.status', 'mneme.doctor'}:
                content = response['result']['content'][0]
                info = json.loads(content['text'])
                info['client_grants'] = {'memory_write': writable,
                    'profile': 'memory' if writable else 'bound_export',
                    'snapshot_scope': 'selected state sibling namespace',
                    'legacy_global_snapshots': 'not inspected or modified'}
                if not writable and 'tools' in info:
                    info['tools'] = [name for name in info['tools'] if name in safe]
                content['text'] = json.dumps(info)
            if response and req.get('method') == 'tools/call' and params.get('name') == 'mneme.forget' and not response.get('error') and not response['result'].get('isError'):
                content = response['result']['content'][0]
                info = json.loads(content['text'])
                info['client_snapshot_scope'] = {
                    'managed': 'selected state sibling namespace',
                    'legacy_global_snapshots': 'not inspected or modified'}
                content['text'] = json.dumps(info)
        except (ValueError, TypeError, AttributeError) as exc:
            response = mcp._err(None, -32600, str(exc))
        if response is not None:
            print(json.dumps(response), flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    if TOOL == 'mneme':
        parser.add_argument('--allow-memory-write', action='store_true')
        parser.add_argument('--memory-write', choices=('true', 'false'), default='false')
    if TOOL == 'relay':
        parser.add_argument('--allow-write', action='store_true')
        parser.add_argument('--allow-exec', action='store_true')
    args = parser.parse_args(argv)
    try:
        if TOOL == 'mneme':
            os.environ['MNEME_STATE'] = explicit_path(os.environ.get('MNEME_STATE'))
            from mneme.snapshot_binding import for_state
            with for_state(os.environ['MNEME_STATE']):
                return mneme_serve(args.allow_memory_write or args.memory_write == 'true')
        if TOOL == 'relay':
            root = explicit_path(os.environ.get('RELAY_MCP_ROOT'), directory=True)
            from relay.local_mcp import serve
            from relay.mcp_grants import StartGrants
            return serve(grants=StartGrants(allow_write=args.allow_write,
                                           allow_exec=args.allow_exec, root=root))
        from plexus.mcp import serve
        return serve()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
