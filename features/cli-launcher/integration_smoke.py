"""Opt-in real owner API check; all commands/state use a disposable Luvus home."""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import commands_adapter as adapter
import project_launcher as project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--commands-root', required=True, type=Path)
    args = parser.parse_args()
    binary = str(args.binary.resolve(strict=True))
    owner_root = args.commands_root.resolve(strict=True)
    python = owner_root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        raise ValueError('Project Commands interpreter is missing.')
    # Keep the Unix socket below macOS's path-length limit.
    with tempfile.TemporaryDirectory(prefix='launcher-owner-', dir='/tmp' if os.name != 'nt' else None) as temporary:
        root = Path(temporary).resolve()
        cwd = root / 'project'; cwd.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith('LUVUS_')}
        env.update(LUVUS_HOME=str(root / 'home'), LUVUS_BIN_PATH=binary,
                   LUVUS_SOCKET_PATH=str(root / 'home' / 'luvus.sock'), PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([binary, 'server', 'start'], env=env, check=True, capture_output=True, timeout=30)
        try:
            with patch.dict(os.environ, env, clear=True):
                command = [str(python), str(owner_root / 'launcher.py'), 'api', '--store', str(root / 'owner-state')]
                def owner(action, **params):
                    return adapter.request_owner(command, str(cwd), action, **params)
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    port = listener.getsockname()[1]
                url = f'http://127.0.0.1:{port}'
                service = {'id': 'web', 'name': 'Disposable HTTP server', 'kind': 'service', 'category': 'custom',
                           'argv': [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'],
                           'ports': [port], 'urls': [url], 'readiness': {'kind': 'http', 'url': url}}
                previous = owner('config')
                config = deepcopy(previous)
                config['projects'][str(cwd)] = {'commands': [service]}
                owner('save-config', config=config, previous=previous)
                def call(operation, **params):
                    return adapter.adapt({'cwd': str(cwd), 'operation': operation, 'id': 'web', **params}, command)
                revision = call('commands.describe')['revision']
                first = call('services.ensure', revision=revision, operation_id='smoke-first')
                second = call('services.ensure', revision=revision, operation_id='smoke-second')
                assert first['run_id'] == second['run_id'], (first, second)
                deadline = time.monotonic() + 20
                while True:
                    status = owner('status', run=first['run_id'])
                    if status['state'] == 'running' and status.get('readiness') == 'ready':
                        break
                    if status['state'] in ('failed', 'interrupted', 'unknown') or time.monotonic() > deadline:
                        raise AssertionError(status)
                    time.sleep(.25)
                assert call('services.urls')['urls'] == [url]
                changed = deepcopy(config)
                changed['projects'][str(cwd)]['commands'][0]['name'] = 'Changed definition'
                owner('save-config', config=changed, previous=config)
                try:
                    call('services.ensure', revision=revision, operation_id='must-not-run')
                except ValueError as error:
                    assert 'changed' in str(error).lower(), error
                else:
                    raise AssertionError('Stale revision was accepted')
                assert len(owner('status')) == 1
                owner('cancel', run=first['run_id'])
                deadline = time.monotonic() + 15
                while owner('status', run=first['run_id'])['state'] in ('starting', 'running', 'cancelling'):
                    if time.monotonic() > deadline:
                        raise AssertionError('Disposable service did not stop')
                    time.sleep(.25)
                print('PASS: real owner API dispatch, cross-operation service reuse, ready URL, stale revision rejection, and cancellation')
        finally:
            subprocess.run([binary, 'server', 'stop'], env=env, check=True, capture_output=True, timeout=30)


if __name__ == '__main__':
    main()
