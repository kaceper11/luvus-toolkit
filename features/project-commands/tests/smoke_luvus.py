"""Opt-in: isolated Luvus home, a fixture project and a harmless owned process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from project_commands.model import Store, MODULE
from project_commands.host import Host
from project_commands.runtime import launch, cancel, restart, session_identity


def main():
    binary = os.environ['LUVUS_BIN_PATH']
    module = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='pc-smoke-', dir='/tmp' if os.name != 'nt' else None) as tmp:
        previous = {k: v for k, v in os.environ.items() if k.startswith('LUVUS_')}
        for k in previous: os.environ.pop(k)
        os.environ['LUVUS_HOME'] = tmp
        os.environ['LUVUS_BIN_PATH'] = binary
        def cli(*args):
            r = subprocess.run([binary, *args], capture_output=True, text=True, timeout=30)
            if r.returncode: raise AssertionError(r.stderr or r.stdout)
            return r.stdout
        def wait(fn):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                r = fn()
                if r: return r
                time.sleep(.1)
            raise AssertionError('Timed out waiting for isolated runtime.')
        try:
            cli('server', 'start')
            cli('module', 'link', str(module))
            h = Host(binary)
            info = h.call('module.info', id=MODULE)
            assert info['runnable'], info
            fixture = Path(tmp).resolve() / 'fixture'; fixture.mkdir()
            store = Store(Path(tmp) / 'module-state')
            old = store.config()
            command = {'id': 'fixture', 'name': 'Disposable service', 'kind': 'service', 'argv': [sys.executable, '-u', '-c', 'import time; print("fixture ready"); time.sleep(60)']}
            check = {'id': 'check', 'name': 'Disposable check', 'kind': 'command', 'category': 'test', 'argv': [sys.executable, '-c', 'print("integration check passed")']}
            store.save_config({'version': 1, 'projects': {str(fixture): {'commands': [command, check]}}}, old)
            # The isolated server injects its own socket into command terminals.
            inv = h.inventory()
            os.environ['LUVUS_SOCKET_PATH'] = str(Path(tmp) / 'luvus.sock')
            run = launch(store, h, str(fixture), 'fixture', 'smoke')
            wait(lambda: store.get(run['id'])['state'] != 'starting')
            record = store.get(run['id'])
            assert record['state'] == 'running', record
            assert record['locator']['server_generation'] == inv['server_generation']
            assert launch(store, h, str(fixture), 'fixture', 'repeat')['id'] == run['id']
            ui = h.create(str(fixture), [sys.executable, str(module / 'launcher.py'), 'ui', '--root', str(fixture), '--store', str(store.root), '--luvus-bin', binary], 'Commands smoke', focus=False)
            wait(lambda: 'Project Commands' in json.dumps(h.call('pane.read', pane=ui['pane_id'], lines=50)))
            h.call('terminal.backend.close', server_generation=ui['server_generation'], terminal_id=ui['terminal_id'], pane_id=ui['pane_id'])
            assert store.get(run['id'])['state'] == 'running', 'Closing console stopped its service'
            replacement = restart(store, h, str(fixture), run['id'], store.definition(str(fixture), 'fixture'), session_identity(h), 'restart-smoke')
            wait(lambda: store.get(run['id'])['state'] == 'cancelled')
            assert 'fixture ready' in (store.logs / (run['id'] + '.log')).read_text()
            assert replacement['id'] != run['id']
            wait(lambda: store.get(replacement['id'])['state'] == 'running')
            # Repeating the alias from the old run cannot create a third service.
            assert launch(store, h, str(fixture), 'fixture', 'repeat')['id'] == run['id']
            cancel(store, replacement['id']); wait(lambda: store.get(replacement['id'])['state'] == 'cancelled')
            owner = [sys.executable, str(module / 'launcher.py'), 'api', '--store', str(store.root)]
            tasks = module.parent / 'luvus-tasks'
            launcher = module.parent / 'luvus-cli-launcher'
            def bridge(directory, code, payload):
                interpreter = directory / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                result = subprocess.run([str(interpreter), '-c', code], cwd=directory, input=json.dumps(payload), capture_output=True, text=True, timeout=30)
                assert result.returncode == 0, result.stderr or result.stdout
                return json.loads(result.stdout)
            if (tasks / 'luvus_tasks/workflow.py').exists():
                params = {'repository': str(fixture), 'worktree': str(fixture), 'definition': check, 'command_id': 'check', 'request_id': 'tasks-smoke'}
                code = 'import json,sys; from luvus_tasks.workflow import commands_bridge; p=json.load(sys.stdin); print(json.dumps(commands_bridge(p["owner"],p["method"],p["params"])))'
                submitted = bridge(tasks, code, {'owner': owner, 'method': 'run', 'params': params})
                wait(lambda: store.get(submitted['run_id'])['state'] == 'passed')
                result = bridge(tasks, code, {'owner': owner, 'method': 'result', 'params': {**params, 'run_id': submitted['run_id']}})
                assert result['state'] == 'passed' and result['request_id'] == 'tasks-smoke', result
                print('PASS: actual Tasks bridge -> Project Commands API -> native command -> result reconciliation.')
            if (launcher / 'commands_adapter.py').exists():
                code = 'import json,sys; from commands_adapter import adapt; p=json.load(sys.stdin); print(json.dumps(adapt(p["request"],p["owner"])))'
                request = {'version': 1, 'request_id': 'launcher-smoke', 'cwd': str(fixture), 'id': 'check', 'operation': 'commands.describe'}
                description = bridge(launcher, code, {'owner': owner, 'request': request})
                result = bridge(launcher, code, {'owner': owner, 'request': {**request, 'operation': 'commands.run', 'revision': description['revision'], 'operation_id': 'launcher-smoke'}})
                wait(lambda: store.get(result['run_id'])['state'] == 'passed')
                print('PASS: actual Launcher adapter -> Project Commands API -> native command.')
            print('PASS: isolated manifest, UI, exact identity, durable duplicate start, panel-independent service, one-action restart, cancellation and output.')
        finally:
            try: cli('server', 'stop')
            finally:
                for k in list(os.environ):
                    if k.startswith('LUVUS_'): os.environ.pop(k)
                os.environ.update(previous)


if __name__ == '__main__': main()
