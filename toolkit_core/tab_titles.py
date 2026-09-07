"""Best-effort exact-tab titles; deferred background names apply on pane focus."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unicodedata

MODULE_ID = 'kacper.toolkit'


def title(value):
    value = ' '.join(''.join(' ' if c.isspace() else c for c in str(value) if c.isspace() or not unicodedata.category(c).startswith('C')).split())
    return value if len(value) <= 40 else value[:39] + '…'


def directory():
    base = os.environ.get('LUVUS_TOOLKIT_CONFIG_DIR')
    if not base:
        base = Path(os.environ.get('LUVUS_HOME', str(Path.home() / '.luvus'))) / 'modules/config' / MODULE_ID
    return Path(base) / 'tab-titles'


def apply(call, record):
    pane = call('pane.get', pane=str(record['pane']))
    if pane.get('terminal_id') != record['terminal_id'] or pane.get('tab_id') != record['tab_id']:
        return False
    # tab.rename is scoped to the active workspace. Stable IDs make a workspace
    # switch fail closed; the next focus event can retry without moving focus.
    tab = next((t for t in call('tab.list')['tabs'] if t.get('tab_id') == record['tab_id']), None)
    if tab is None:
        return False
    if tab.get('name'):
        return True  # Keep any existing name, including a manual rename.
    call('tab.rename', tab_id=record['tab_id'], name=record['title'])
    return True


def remember(call, pane, name):
    if pane is None:
        return False
    try:
        info = call('pane.get', pane=str(pane))
        if not info.get('terminal_id') or not info.get('tab_id'):
            return False
        record = {'pane': str(pane), 'terminal_id': info['terminal_id'], 'tab_id': info['tab_id'], 'title': title(name)}
        root = directory(); root.mkdir(parents=True, exist_ok=True)
        path = root / (info['terminal_id'] + '.json')
        temporary = path.with_suffix('.' + str(os.getpid()) + '.tmp')
        temporary.write_text(json.dumps(record)); temporary.replace(path)
        if apply(call, record):
            path.unlink(missing_ok=True)
            return True
        # Luvus 0.13.x does not emit pane.focused for every focus route.
        # Only unnamed background tabs need this short-lived helper process.
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--pending', str(path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return False
    except Exception:
        return False  # Naming never changes whether an agent was launched or prompted.


def rpc(method, **params):
    result = subprocess.run([os.environ['LUVUS_BIN_PATH'], 'uhp', 'proxy'],
                            input=json.dumps({'id':'tab-title', 'method':method, 'params':params}) + '\n',
                            capture_output=True, text=True, timeout=10)
    value = json.loads(result.stdout)
    if 'error' in value: raise ValueError(value['error'])
    return value['result']


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--pending':
        path = Path(sys.argv[2])
        try:
            record = json.loads(path.read_text())
            while path.exists():
                info = rpc('pane.get', pane=record['pane'])
                if info.get('terminal_id') != record['terminal_id'] or info.get('tab_id') != record['tab_id'] or apply(rpc, record):
                    path.unlink(missing_ok=True)
                    return
                time.sleep(2)
        except Exception:
            return
    if len(sys.argv) == 3:
        remember(rpc, sys.argv[1], sys.argv[2]); return
    try:
        pane = os.environ.get('LUVUS_PANE_ID')
        if not pane: return
        info = rpc('pane.get', pane=pane)
        path = directory() / (info['terminal_id'] + '.json')
        if path.exists(): apply(rpc, json.loads(path.read_text()))
    except Exception:
        pass


if __name__ == '__main__':
    main()
