"""Versioned stdin JSON integration and manifest entrypoints."""
import argparse
import json
import os
from pathlib import Path
import sys

from .model import Store, canonical, MODULE
from .host import Host, entry_args


def api(store, request, host=None):
    from . import diagnostics as diag, runtime
    if request.get('version') != 1: raise ValueError('API version must be 1.')
    action = request['action']
    if action == 'describe':
        return {'module': MODULE, 'version': 1, 'actions': ['describe', 'discover', 'config', 'save-config', 'session', 'run', 'restart', 'status', 'evidence', 'cancel', 'resolve-interrupted', 'health', 'resources', 'container-preview', 'container-action'], 'provider_contract': {'input': {'version': 1, 'root': '/exact/checkout'}, 'output': {'checks': [{'name': 'check', 'state': 'healthy|missing|failing|unverified', 'evidence': 'bounded text', 'action': 'owner recovery reference'}]}}}
    root = canonical(request['root'])
    if action == 'session': return runtime.session_identity(host or Host())
    if action == 'discover': return diag.discover(root)
    if action == 'config': return store.config()
    if action == 'evidence':
        r = store.by_request(request['request_id'])
        if r['root'] != root or request.get('run') and request['run'] != r['id']:
            raise ValueError('Evidence belongs to a different checkout or run.')
        result = runtime.run_status(store, r['id'])
        path = store.logs / (r['id'] + '.log')
        if path.is_symlink(): raise ValueError('Refusing a symlinked command log.')
        raw = b''; truncated = False
        if path.exists():
            with path.open('rb') as stream:
                raw = stream.read(16001)
                if len(raw) > 16000:
                    stream.seek(-8000, os.SEEK_END)
                    raw = raw[:8000] + b'\n[... middle omitted ...]\n' + stream.read(8000)
                    truncated = True
        # Diagnostics may contain credentials too; redact before bounding the text.
        diagnostic = diag.redact(json.dumps({k: result[k] for k in ('error', 'problems') if k in result}, ensure_ascii=False))[:4000]
        return {**result, 'request': request['request_id'],
                'diagnostic_excerpt': diagnostic,
                'output_excerpt': diag.redact(raw.decode('utf-8', 'replace')),
                'excerpt_truncated': truncated or result.get('truncated', False)}
    if action == 'save-config':
        store.save_config(request['config'], request['previous']); return {'saved': True}
    if action == 'run':
        if not request.get('request_id'): raise ValueError('run requires a stable request_id; reuse it to reconcile uncertain replies.')
        if not request.get('reviewed') or not request.get('session'): raise ValueError('run requires the reviewed definition and selected session identity (socket, generation).')
        return runtime.launch(store, host or Host(), root, request['command'], request['request_id'], request['reviewed'], request['session'])
    if action in ('status', 'cancel', 'resolve-interrupted'):
        if action == 'status' and request.get('request_id'):
            r = store.by_request(request['request_id'])
            if r['root'] != root or (request.get('run') and request['run'] != r['id']): raise ValueError('Request belongs to a different checkout or run.')
            return {**runtime.run_status(store, r['id']), 'request': request['request_id']}
        if action == 'status' and not request.get('run'):
            return [runtime.run_status(store, r['id']) for r in store.list(root)[:50]]
        r = store.get(request['run'])
        if r['root'] != root: raise ValueError('Run belongs to a different checkout.')
        if action == 'status': return runtime.run_status(store, r['id'])
        if action == 'cancel': return runtime.cancel(store, r['id'], request.get('force', False))
        if request.get('confirm') is not True: raise ValueError('Explicit confirm is required to acknowledge lost tracking.')
        return runtime.resolve_interrupted(store, r['id'])
    if action == 'health': return diag.health(store, root, host)
    if action == 'restart':
        if not all(request.get(k) for k in ('run', 'reviewed', 'session', 'request_id')):
            raise ValueError('Restart requires run, reviewed definition, session and stable request_id.')
        return runtime.restart(store, host or Host(), root, request['run'], request['reviewed'], request['session'], request['request_id'])
    if action == 'resources': return diag.resources(host or Host(), store)
    if action == 'container-preview': return diag.inspect_container(request['context'], request['container'])
    if action == 'container-action':
        bindings = store.project(root).get('containers', [])
        binding = next((b for b in bindings if b['id'] == request['container']), None)
        if not binding: raise ValueError('Container is not bound to the selected project.')
        if request['operation'] in ('start', 'stop') and request.get('confirm') is not True: raise ValueError('Explicit confirmation is required.')
        return diag.recorded_container_action(store, root, binding, request['operation'])
    raise ValueError('Unknown API action.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['open', 'ui', 'api', 'supervise', 'gated-child', 'posix-child'])
    p.add_argument('--root'); p.add_argument('--cwd'); p.add_argument('--store'); p.add_argument('--run'); p.add_argument('--luvus-bin'); p.add_argument('--result')
    a, remaining = p.parse_known_args()
    if remaining and a.action not in ('gated-child', 'posix-child'): p.error('Unexpected arguments: ' + ' '.join(remaining))
    if a.luvus_bin: os.environ['LUVUS_BIN_PATH'] = a.luvus_bin
    try:
        if a.action in ('gated-child', 'posix-child'):
            from .runtime import gated_child, posix_child
            argv = remaining[1:] if remaining[:1] == ['--'] else remaining
            return gated_child(argv, a.cwd, a.result) if a.action == 'gated-child' else posix_child(argv, a.cwd, a.result)
        store = Store(a.store)
        host = Host() if os.environ.get('LUVUS_BIN_PATH') else None
        if a.action == 'api':
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024: raise ValueError('Request exceeds 1 MiB.')
            request = json.loads(raw)
            result = api(store, request, host)
            print(json.dumps({'version': 1, 'request_id': request.get('request_id'), 'cwd': request.get('cwd'), 'result': result})); return 0
        if a.action == 'supervise':
            from .runtime import supervise
            return supervise(store, a.run, host)
        root = canonical(a.root) if a.root else host.source_target() if host else canonical(os.getcwd())
        if a.action == 'open':
            if not host: raise ValueError('Open through a Luvus module action.')
            print(json.dumps(host.create(root, entry_args('ui', '--root', root, '--store', store.root, '--luvus-bin', host.binary), 'Project Commands', focus=True)))
        else:
            from .ui import Console
            from .tab_titles import remember
            if host and os.environ.get("LUVUS_PANE_ID"):
                remember(host.call, os.environ["LUVUS_PANE_ID"], "⌘ Commands")
            Console(store, root, host).run()
        return 0
    except Exception as e:
        if a.action == 'api': print(json.dumps({'version': 1, 'error': {'message': str(e)}}))
        else: print('Project Commands: ' + str(e), file=sys.stderr)
        return 1
