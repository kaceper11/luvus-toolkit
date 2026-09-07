"""Opt-in native Lazygit checks; only disposable repositories and Luvus state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch
import git_sidebar as app


def main():
    binary = shutil.which('luvus')
    assert binary and shutil.which('lazygit'), 'Install Luvus and Lazygit first'
    with tempfile.TemporaryDirectory(prefix='git-views-', dir='/tmp') as tmp:
        root = Path(tmp)
        home = root / 'home'
        env = {k:v for k,v in os.environ.items() if not k.startswith('LUVUS_')}
        env.update(LUVUS_HOME=str(home), LUVUS_BIN_PATH=binary, LUVUS_SOCKET_PATH=str(home/'luvus.sock'))
        def cli(*args):
            result = subprocess.run([binary, *args], env=env, text=True, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            if not result.stdout.lstrip().startswith("{"): return result.stdout.strip()
            data = json.loads(result.stdout)
            assert 'error' not in data, data
            return data.get('result', data)
        try:
            cli('server', 'start')
            cli('module', 'link', str(Path(__file__).parent.resolve()))
            repo = root / 'repo with spaces'
            repo.mkdir()
            subprocess.run(['git','init','-q', str(repo)],check=True)
            cli('workspace','open',str(repo))
            env['LUVUS_MODULE_STATE_DIR'] = str(home / 'modules/state' / app.MODULE)
            with patch.dict(os.environ, env, clear=True):
                target = app.current_target(str(repo))
                panes = [app.show_hub(target), *[app.open_terminal('lazygit-'+view,target) for view in list(app.VIEWS)[1:]]]
                assert len(set(panes)) == 4
                again = [app.open_terminal('lazygit-'+view,target) for view in app.VIEWS]
                assert panes == again
                for pane, view in zip(panes,app.VIEWS):
                    app.rpc('pane.focus', pane=pane)
                    tabs = app.rpc('tab.list')['tabs']
                    info = app.rpc('pane.get',pane=pane)
                    tab = next(t for t in tabs if t['tab_id']==info['tab_id'])
                    assert tab['name']=='⎇ '+app.VIEWS[view]+' · repo with spaces',tab
                assert not app.git(repo,'status','--porcelain').stdout
            print('Four native Lazygit views named and reused; checkout unchanged.', flush=True)
        finally:
            cli('server','stop')

if __name__ == '__main__': main()
