"""Manifest entrypoint. Context goes through local storage, never shell interpolation."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    python = root.parents[1] / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if python.exists() and Path(sys.prefix).resolve() != (root.parents[1] / '.venv').resolve():
        return subprocess.call([str(python), str(__file__), *sys.argv[1:]])
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11+ required. Create .venv using the README.')
    action = sys.argv[1] if len(sys.argv) > 1 else 'ui'
    if action == 'shell':
        argv = [os.environ.get('COMSPEC', 'cmd.exe'), '/d'] if os.name == 'nt' else ['/bin/bash', '--noprofile', '--norc', '-i']
        os.execvp(argv[0], argv)
    from send_agent import Host, Store, Error
    try:
        if action == 'ui' and len(sys.argv) == 4:
            from composer import Composer
            Composer(sys.argv[2], sys.argv[3]).run()
            return 0
        if action not in ('open', 'ui'):
            raise Error('Usage: launcher.py [open|ui]')
        host = Host()
        context = json.loads(os.environ.get('LUVUS_MODULE_CONTEXT_JSON', '{}'))
        store = Store()
        record = store.create(context, host.session)
        state_dir = str(store.root.resolve())
        store.db.close()
        if action == 'open':
            opened = host.call('terminal.backend.create', cwd=record['cwd'], placement={'kind': 'workspace'}, focus=True,
                      label='Send to agent', command=[sys.executable, str(root / 'launcher.py'), 'ui', state_dir, record['id']])
            from tab_titles import remember
            remember(host.call, opened['pane_id'], '↗ Send to agent')
        else:
            from composer import Composer
            Composer(state_dir, record['id']).run()
        return 0
    except (Error, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
