"""Translate feature-local UI names at the native Luvus boundary only."""
import copy
import json
import os
import subprocess
import sys
from . import ROOT, MODULE, FEATURES, directory, namespaced


def settings():
    binary = os.environ.get('LUVUS_BIN_PATH')
    if not binary:
        raise ValueError('Missing inherited Luvus binary')
    p = subprocess.run([binary, 'module', 'settings', MODULE], capture_output=True, text=True, timeout=10, check=True)
    result = json.loads(p.stdout)
    if 'error' in result:
        raise ValueError(str(result['error']))
    return {s['key']: s['value'] for s in result.get('result', result)['settings'] if not s.get('secret')}


def enabled(feature):
    return settings().get(feature + '-enabled', True) is True


def ui(feature, value):
    if isinstance(value, list):
        return [ui(feature, v) for v in value]
    if isinstance(value, dict):
        return {k: namespaced(feature, v) if k == 'action' and isinstance(v, str) and v else ui(feature, v)
                for k, v in value.items()}
    return value


def command(argv):
    """Re-enter the bundle when Luvus launches a feature into a fresh terminal."""
    if not isinstance(argv, list) or len(argv) < 2:
        return argv
    try:
        relative = __import__('pathlib').Path(argv[1]).resolve().relative_to(ROOT / 'features')
    except (ValueError, OSError):
        return argv
    if len(relative.parts) < 2 or relative.parts[0] not in FEATURES:
        return argv
    return [sys.executable, str(ROOT / 'toolkit.py'), 'run', relative.parts[0], relative.parts[1] if len(relative.parts) == 2 else str(relative.relative_to(relative.parts[0])), *argv[2:]]


def request(feature, method, params):
    params = copy.deepcopy(params)
    owner = next((name for name, old in FEATURES.items() if old in (params.get('id'), params.get('module'))), feature)
    if method.startswith('module.'):
        for key in ('id', 'module'):
            if params.get(key) in FEATURES.values():
                params[key] = MODULE
        for key in ('entrypoint', 'action'):
            if key in params:
                params[key] = namespaced(owner, params[key], 'panes' if key == 'entrypoint' else 'actions')
    if method.startswith(('ui.dock.', 'ui.bar.')):
        if params.get('owner') in FEATURES.values(): params['owner'] = MODULE
        for key in ('id', 'dock', 'bar'):
            if isinstance(params.get(key), str):
                params[key] = namespaced(feature, params[key], 'docks' if '.dock.' in method else 'bars')
        for key in ('rows', 'content', 'compact_content'):
            if key in params:
                params[key] = ui(feature, params[key])
    if method == 'module.link' and 'path' in params:
        if __import__('pathlib').Path(params['path']).resolve().is_relative_to(ROOT / 'features'):
            params['path'] = str(ROOT)
    if method == 'terminal.backend.create' and 'command' in params:
        params['command'] = command(params['command'])
    return params, owner


def response(owner, method, result):
    if method == 'module.info':
        result = dict(result)
        result['root'] = str(ROOT / 'features' / owner)
        result['enabled'] = bool(result.get('enabled')) and enabled(owner)
    elif method == 'module.config_dir':
        result = {**result, 'dir': str(directory(owner))}
    return result


def cli_args(feature, args, stdin=None):
    args = list(args)
    owner = feature
    if args[:2] == ['uhp', 'proxy'] and stdin:
        payload = json.loads(stdin)
        payload['params'], owner = request(feature, payload['method'], payload.get('params', {}))
        return args, json.dumps(payload) + '\n', owner, payload['method']
    if args and args[0] == 'module':
        for i, item in enumerate(args):
            if item in FEATURES.values():
                owner = next(k for k, v in FEATURES.items() if v == item)
                args[i] = MODULE
        if args[1:3] == ['pane', 'open']:
            args[4] = namespaced(owner, args[4], 'panes')
        if args[1:2] == ['run']:
            args[3] = namespaced(owner, args[3])
    if args and (args[0] == 'bar' or args[:2] == ['ui', 'dock']):
        for flag in ('--id',):
            if flag in args:
                index = args.index(flag) + 1
                args[index] = namespaced(feature, args[index], 'bars' if args[0] == 'bar' else 'docks')
        for flag in ('--rows', '--content', '--compact-content'):
            if flag in args:
                index = args.index(flag) + 1
                args[index] = json.dumps(ui(feature, json.loads(args[index])))
        if stdin:
            stdin = json.dumps(ui(feature, json.loads(stdin)))
    return args, stdin, owner, 'module.info' if args[:2] == ['module', 'info'] else ''
