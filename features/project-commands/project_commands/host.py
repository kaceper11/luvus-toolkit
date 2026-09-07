"""Small UHP boundary with exact terminal identity and argv transport."""
import json
import os
from pathlib import Path
import subprocess
import sys


class Host:
    def __init__(self, binary=None):
        self.binary = binary or os.environ.get('LUVUS_BIN_PATH')
        if not self.binary: raise ValueError('Open through Luvus: LUVUS_BIN_PATH is missing.')

    def call(self, method, **params):
        from toolkit_core.transport import request as toolkit_request, response as toolkit_response
        params, toolkit_owner = toolkit_request('project-commands', method, params)
        p = subprocess.run([self.binary, 'uhp', 'proxy'], input=json.dumps({'id': 'project-commands', 'method': method, 'params': params}) + '\n', text=True, capture_output=True, timeout=30)
        if p.returncode: raise ValueError(p.stderr.strip() or 'Luvus request failed.')
        response = json.loads(p.stdout)
        if 'error' in response: raise ValueError(response['error'].get('message', str(response['error'])))
        return toolkit_response(toolkit_owner, method, response['result'])

    def inventory(self):
        result = self.call('terminal.backend.inventory')
        if result.get('truncated'): raise ValueError('Native inventory is truncated; exact ownership cannot be established.')
        return result

    def locator(self, pane):
        inventory = self.inventory()
        for t in inventory['terminals']:
            if str(t['pane_id']) == str(pane):
                return {'server_generation': inventory['server_generation'], 'terminal_id': t['terminal_id'], 'pane_id': str(pane), 'expected_root': t['root_process']}
        raise ValueError('Target terminal no longer exists.')

    def validate(self, locator):
        result = self.call('terminal.backend.validate', **locator)
        if result.get('state') != 'alive': raise ValueError('Terminal identity is unavailable or changed: ' + str(result.get('state')))
        return result

    def create(self, root, args, label, focus=False):
        result = self.call('terminal.backend.create', cwd=root, command=args, label=label, placement={'kind': 'workspace'}, focus=focus)
        from .tab_titles import remember
        remember(self.call, result['pane_id'], '⌘ ' + ('Commands' if label == 'Project Commands' else label))
        return result

    def source_target(self):
        from .model import canonical
        context = json.loads(os.environ.get('LUVUS_MODULE_CONTEXT_JSON', '{}'))
        if context.get('invocation_source') == 'menu:workspace':
            clicked = context['workspace']
            live = self.call('workspace.get', workspace=clicked['id'])
            if canonical(live['cwd']) != canonical(clicked['cwd']): raise ValueError('Workspace changed; reopen its menu.')
            return canonical(clicked['cwd'])
        pane = context.get('pane', {})
        if pane:
            live = self.call('pane.get', pane=pane['id'])
            if canonical(live['cwd']) != canonical(pane['cwd']): raise ValueError('Pane directory changed; reopen its menu.')
            return checkout_root(live['cwd'])
        cwd = os.environ.get('LUVUS_WORKSPACE_CWD')
        if cwd: return checkout_root(cwd)
        raise ValueError('Select an explicit project directory.')


def checkout_root(cwd):
    from .model import canonical
    p = subprocess.run(['git', '-C', cwd, 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=5)
    return canonical(p.stdout.strip() if p.returncode == 0 else cwd)


def entry_args(*args):
    return [sys.executable, str(Path(__file__).resolve().parents[1] / 'launcher.py'), *map(str, args)]
