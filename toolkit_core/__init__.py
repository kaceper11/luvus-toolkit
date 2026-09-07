"""Shared boundaries for the explicitly bundled Luvus features."""
from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parents[1]
MODULE = 'kacper.toolkit'
FEATURES = {
    'cli-launcher': 'personal.luvus-cli-launcher',
    'git-sidebar': 'personal.git-sidebar',
    'project-commands': 'personal.project-commands',
    'send-to-agent': 'personal.luvus-send-to-agent',
    'tasks': 'personal.luvus-tasks',
    'ai-usage': 'kacper.ai-usage',
    'keep-awake': 'kacper.keep-awake',
}

def directory(feature, kind='config'):
    if feature not in FEATURES or kind not in ('config', 'state'):
        raise ValueError('Unknown toolkit feature or directory kind')
    base = os.environ.get('LUVUS_TOOLKIT_' + kind.upper() + '_DIR')
    if not base:
        base = Path((os.environ.get('LUVUS_HOME') or str(Path.home() / '.luvus'))) / 'modules' / kind / MODULE
    return Path(base) / 'features' / feature

def configure(feature):
    if feature not in FEATURES:
        raise ValueError('Unknown toolkit feature')
    # A new module invocation receives the bundle directory. Nested workers already
    # carry the original bundle roots; never append features twice.
    for kind in ('CONFIG', 'STATE'):
        key = 'LUVUS_TOOLKIT_' + kind + '_DIR'
        if not os.environ.get(key):
            base = os.environ.get('LUVUS_MODULE_' + kind + '_DIR') if os.environ.get('LUVUS_MODULE_ID') == MODULE else None
            os.environ[key] = base or str(Path((os.environ.get('LUVUS_HOME') or str(Path.home() / '.luvus'))) / 'modules' / kind.lower() / MODULE)
        os.environ['LUVUS_MODULE_' + kind + '_DIR'] = str(directory(feature, kind.lower()))
    os.environ['LUVUS_TOOLKIT_FEATURE'] = feature
    os.environ['LUVUS_MODULE_ID'] = MODULE
    # Keep the actual module root/identity and the inherited Luvus binary/socket.
    settings = json.loads(os.environ.get('LUVUS_TOOLKIT_SETTINGS_JSON', os.environ.get('LUVUS_MODULE_SETTINGS_JSON', '{}')))
    os.environ['LUVUS_TOOLKIT_SETTINGS_JSON'] = json.dumps(settings)
    local = {k[len(feature) + 1:]: v for k, v in settings.items() if k.startswith(feature + '-')}
    os.environ['LUVUS_MODULE_SETTINGS_JSON'] = json.dumps(local)
    for key, value in local.items():
        os.environ['LUVUS_SETTING_' + key.upper().replace('-', '_')] = str(value).lower() if isinstance(value, bool) else str(value)
    for key in ('LUVUS_MODULE_ROW_ACTION', 'LUVUS_MODULE_BAR_SEGMENT'):
        value = os.environ.get(key, '')
        if value.startswith(feature + '-'):
            os.environ[key] = value[len(feature) + 1:]

def namespaced(feature, value, kind='actions'):
    ident = feature + '-' + value
    if os.name == 'nt':
        catalog = json.loads((ROOT / 'toolkit_core/catalog.json').read_text())
        if ident + '-windows' in catalog.get(kind, []):
            return ident + '-windows'
    return ident
