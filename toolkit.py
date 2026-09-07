"""Portable bootstrap and explicit feature entrypoint for Luvus Toolkit."""
import os
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parent

def main():
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if python.exists() and Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        return subprocess.call([str(python), str(__file__), *sys.argv[1:]])
    if sys.version_info < (3, 11):
        for candidate in ('python3.14','python3.13','python3.12','python3.11'):
            binary = shutil.which(candidate)
            if binary:
                return subprocess.call([binary, str(__file__), *sys.argv[1:]])
        raise SystemExit('Luvus Toolkit requires Python 3.11+. Run installation with a supported Python.')
    action = sys.argv[1] if len(sys.argv) > 1 else 'doctor'
    if action == 'test':
        return subprocess.call([sys.executable, str(ROOT / 'scripts/test.py')])
    if action == 'bootstrap':
        from toolkit_core.install import bootstrap
        return bootstrap()
    if action in ('manifest', 'doctor', 'migrate'):
        from toolkit_core.install import manifest, doctor, migrate
        return {'manifest': manifest, 'doctor': doctor, 'migrate': migrate}[action]()
    if action != 'run' or len(sys.argv) < 4:
        raise ValueError('Usage: toolkit.py run FEATURE SCRIPT [ARGS] | bootstrap | doctor | manifest | migrate [--apply]')
    from toolkit_core import ROOT as root, FEATURES, configure
    feature, script = sys.argv[2:4]
    if feature not in FEATURES:
        raise ValueError('Unknown feature')
    target = (root / 'features' / feature / script).resolve()
    if not target.is_relative_to(root / 'features' / feature) or not target.is_file():
        raise ValueError('Feature entrypoint is unavailable')
    configure(feature)
    from toolkit_core.transport import enabled
    if not enabled(feature):
        print(feature + ' is disabled in Toolkit settings.')
        return 0
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([str(root), str(target.parent), env.get('PYTHONPATH', '')])
    executable = 'node' if target.suffix == '.mjs' else sys.executable
    return subprocess.call([executable, str(target), *sys.argv[4:]], cwd=target.parent, env=env)

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
