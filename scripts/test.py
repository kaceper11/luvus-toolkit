"""Run each feature suite in a separate process to isolate legacy module names."""
from pathlib import Path
import subprocess
import sys
import os
import tempfile
ROOT = Path(__file__).resolve().parents[1]
failed = []
state = tempfile.TemporaryDirectory(prefix='toolkit-tests-')
for key in list(os.environ):
    if key.startswith('LUVUS_'): os.environ.pop(key)
os.environ['LUVUS_HOME'] = state.name
for feature in ['cli-launcher','git-sidebar','project-commands','send-to-agent','tasks']:
    folder = ROOT / 'features' / feature
    print('\nTesting ' + feature, flush=True)
    command = [sys.executable, '-c', 'import faulthandler, unittest; faulthandler.dump_traceback_later(120, repeat=True); unittest.main(module=None)', 'discover', '-s', 'tests' if (folder / 'tests').exists() else '.', '-v']
    try:
        result = subprocess.run(command, cwd=folder, timeout=900 if os.name == 'nt' and feature == 'tasks' else 600)
        if result.returncode: failed.append(feature)
        if feature == 'git-sidebar':
            result = subprocess.run([sys.executable, 'test_git_sidebar.py'], cwd=folder, timeout=60)
            if result.returncode: failed.append('git-sidebar scenarios')
    except subprocess.TimeoutExpired:
        failed.append(feature + ' (timeout)')
for folder in [ROOT]:
    result = subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-q'],cwd=folder)
    if result.returncode: failed.append('toolkit')
print('Failed:', failed, flush=True)
raise SystemExit(bool(failed))
