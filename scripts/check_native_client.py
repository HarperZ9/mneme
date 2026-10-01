"""Check real packaged stdio identity and permission refusals without model use."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

TOOL = 'mneme'


def check(executable, version):
    with tempfile.TemporaryDirectory(prefix=TOOL+'-client-') as temp:
        env = {k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','SYSTEMDRIVE'}}
        env.update({k:temp for k in ('HOME','USERPROFILE','LOCALAPPDATA','APPDATA')})
        env.update(MNEME_STATE=str(Path(temp)/'memory.db'),RELAY_MCP_ROOT=temp,
                   RELAY_ALLOW_WRITE='1',RELAY_ALLOW_EXEC='1',PATH=str(Path(env.get('SYSTEMROOT','C:/Windows'))/'System32'))
        rows=[{'jsonrpc':'2.0','id':1,'method':'initialize'},
              {'jsonrpc':'2.0','id':2,'method':'tools/list'},
              {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':TOOL+'.status','arguments':{}}},
              {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'unknown_destructive_tool','arguments':{}}}]
        if TOOL == 'mneme':
            rows.append({'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'mneme.remember','arguments':{'session':'synthetic','turns':[]}}})
        if TOOL == 'relay':
            rows.append({'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'local_agent_run','arguments':{'goal':'refuse this request','check':'echo forbidden','root':temp,'allow_exec':True}}})
        if TOOL == 'plexus':
            rows.append({'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'plexus_discover','arguments':{}}})
        p=subprocess.run([str(executable)],input=''.join(json.dumps(r)+'\n' for r in rows),
            capture_output=True,text=True,env=env,cwd=temp,timeout=45)
        if p.returncode:
            raise ValueError('native stdio process failed: '+p.stderr)
        result=[json.loads(line) for line in p.stdout.splitlines()]
        if len(result)!=5 or result[0]['result']['serverInfo']['version']!=version:
            raise ValueError('native protocol/version mismatch')
        if not result[1]['result']['tools'] or result[2].get('error') or result[2]['result'].get('isError'):
            raise ValueError('native status or discovery failed')
        if not (result[3].get('error') or result[3]['result'].get('isError')):
            raise ValueError('unknown tool was accepted')
        if TOOL != 'plexus' and not (result[4].get('error') or result[4]['result'].get('isError')):
            raise ValueError('ungranted operation was accepted')
        if TOOL == 'plexus' and (result[4].get('error') or result[4]['result'].get('isError')):
            raise ValueError('native declarative discovery failed')
        if TOOL == 'relay':
            grants=json.loads(result[2]['result']['content'][0]['text'])['grants']
            if grants['allow_write'] or grants['allow_exec']:
                raise ValueError('ambient grants widened native permissions')
        if (Path(temp)/'memory.db').exists():
            raise ValueError('refused operation created state')
        if TOOL in ('mneme', 'relay'):
            missing = dict(env)
            missing.pop('MNEME_STATE' if TOOL == 'mneme' else 'RELAY_MCP_ROOT')
            refused = subprocess.run([str(executable)], input='', capture_output=True, text=True, env=missing, cwd=temp, timeout=45)
            if not refused.returncode or refused.stdout:
                raise ValueError('missing mandatory binding was accepted')
        bad=subprocess.run([str(executable),'--grant-all'],capture_output=True,env=env,cwd=temp,timeout=45)
        if not bad.returncode or bad.stdout:
            raise ValueError('unknown launch argument accepted')
    return {'status':'PASS','version':version,'executable_sha256':hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
            'scope':'identity, tool list, unknown tool, default permission refusal',
            'does_not_prove':['model workflow','installed client compatibility','clean OS compatibility']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('executable',type=Path); parser.add_argument('--version',required=True)
    args=parser.parse_args()
    print(json.dumps(check(args.executable,args.version),indent=2))
