"""Real Luvus smoke in a disposable home; never touches the selected session."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
binary=os.environ['LUVUS_BIN_PATH']
with tempfile.TemporaryDirectory(prefix='ltk-',dir='/tmp' if os.name != 'nt' else None) as home:
    env={k:v for k,v in os.environ.items() if not k.startswith('LUVUS_')}
    env.update(LUVUS_HOME=home,LUVUS_BIN_PATH=binary)
    if os.name != 'nt': env['LUVUS_SOCKET_PATH']=str(Path(home)/'luvus.sock')
    def cli(*args):
        p=subprocess.run([binary,*args],env=env,capture_output=True,text=True,timeout=180)
        if p.returncode:raise AssertionError(p.stdout+p.stderr)
        try:result=json.loads(p.stdout)
        except ValueError:return p.stdout
        if 'error' in result:raise AssertionError(result['error'])
        return result.get('result',result)
    try:
        cli('server','start')
        if '--install' in sys.argv:
            settings=Path(home)/'modules/config/kacper.toolkit/settings.json'
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({'ai-usage-enabled':False,'keep-awake-enabled':False}))
            cli('module','install','kaceper11/luvus-toolkit','--ref',os.environ['GITHUB_SHA'],'--yes')
        else:
            cli('module','link',str(ROOT),'--disabled')
        for feature in ('ai-usage','keep-awake'):
            cli('module','settings','kacper.toolkit',feature+'-enabled','false')
        fixture=Path(home)/'fixture';fixture.mkdir()
        subprocess.run(['git','init','-q',str(fixture)],check=True)
        cli('workspace','open',str(fixture))
        cli('module','enable','kacper.toolkit')
        info=cli('module','info','kacper.toolkit')
        assert info['runnable'] and info['enabled'],info
        print('PASS: native manifest accepted',flush=True)
        for action in ('doctor','git-sidebar-refresh'):
            result=cli('module','run','kacper.toolkit',action+('-windows' if os.name=='nt' else ''))
            assert not result.get('exit_code',0),result
            print('PASS:',action,json.dumps(result)[:400],flush=True)
        for name in ('tasks-tasks','project-commands-commands','send-to-agent-composer'):
            pane=cli('module','pane','open','kacper.toolkit',name+('-windows' if os.name=='nt' else ''))
            time.sleep(1)
            state=cli('pane','read',str(pane['pane']),'--lines','30')
            text=json.dumps(state)
            assert not any(error in text for error in ('Traceback','ModuleNotFoundError','SyntaxError')),text
            print('PASS: opened',name,flush=True)
        logs=cli('module','log','kacper.toolkit','--limit','100')['logs']
        assert not [log for log in logs if log.get('status')=='failed'],logs
        print('PASS: all native module command logs succeeded',flush=True)
    finally:
        cli('server','stop')
