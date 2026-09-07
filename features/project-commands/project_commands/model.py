"""Local configuration, run records and observed source fingerprints."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid

MODULE = 'personal.project-commands'
ACTIVE = ('starting', 'running', 'cancelling', 'unknown')
LOG_LIMIT = 10 * 1024 * 1024


def canonical(path):
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_dir():
        raise ValueError('Select an existing directory.')
    return str(p)


def platform():
    import sys
    return 'windows' if os.name == 'nt' else 'macos' if sys.platform == 'darwin' else 'linux'


def validate_definition(raw):
    d = dict(raw)
    if not re.fullmatch(r'[a-zA-Z0-9._:-]{1,100}', d.get('id', '')):
        raise ValueError('Command ID must be 1–100 letters, digits, dots, colons, underscores or hyphens.')
    if not isinstance(d.get('name'), str) or not d['name'].strip():
        raise ValueError('Command name is required.')
    if d.get('kind', 'command') not in ('command', 'service'):
        raise ValueError('Kind must be command or service.')
    for args in [d.get('argv', [])] + list(d.get('platform_argv', {}).values()):
        if not isinstance(args, list) or not args or len(args) > 128 or any(not isinstance(a, str) or '\0' in a or len(a) > 16384 for a in args):
            raise ValueError('Use a nonempty bounded argv array; shell syntax requires an explicit shell argv.')
    cwd = Path(d.get('cwd', '.'))
    if cwd.is_absolute() or '..' in cwd.parts:
        raise ValueError('Command cwd must remain inside the selected checkout.')
    if d.get('report_format') not in (None, 'eslint', 'vitest', 'trx'):
        raise ValueError('Report format must be eslint, vitest or trx.')
    if not isinstance(d.get('inputs', []), list) or not all(isinstance(p, str) for p in d.get('inputs', [])):
        raise ValueError('Additional input paths must be a list.')
    ports = d.get('ports', [])
    if not isinstance(ports, list) or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
        raise ValueError('Ports must be integers from 1 to 65535.')
    from urllib.parse import urlparse
    for url in d.get('urls', []):
        u = urlparse(url)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            raise ValueError('Preview URLs must be HTTP(S), without embedded credentials.')
    readiness = d.get('readiness')
    if readiness:
        if readiness.get('kind') == 'tcp':
            if readiness.get('host', '127.0.0.1') not in ('localhost', '127.0.0.1', '::1') or type(readiness.get('port')) is not int or not 1 <= readiness['port'] <= 65535:
                raise ValueError('TCP readiness requires a loopback host and valid port.')
        elif readiness.get('kind') == 'http':
            u = urlparse(readiness.get('url', ''))
            if u.scheme not in ('http', 'https') or u.hostname not in ('localhost', '127.0.0.1', '::1') or u.username or u.password:
                raise ValueError('HTTP readiness requires a loopback HTTP(S) URL without credentials.')
        else:
            raise ValueError('Readiness kind must be tcp or http.')
    d.setdefault('kind', 'command')
    d.setdefault('category', 'custom')
    return d


def command_argv(d):
    if platform() in d.get('unsupported_platforms', []) and platform() not in d.get('platform_argv', {}):
        raise ValueError('This repository script is not compatible with this platform. Review a platform-specific argv alternative.')
    return d.get('platform_argv', {}).get(platform(), d['argv'])


def executable_argv(argv, windows=None):
    """Resolve Windows package-manager shims without passing argv through cmd.exe."""
    import shutil
    windows = os.name == 'nt' if windows is None else windows
    if not windows: return argv
    executable = shutil.which(argv[0]) or argv[0]
    if Path(executable).suffix.lower() in ('.cmd', '.bat'):
        name = Path(executable).stem.lower()
        scripts = {'corepack': 'corepack/dist/corepack.js', 'npm': 'npm/bin/npm-cli.js', 'pnpm': 'pnpm/bin/pnpm.cjs'}
        script = Path(executable).parent / 'node_modules' / scripts.get(name, 'missing')
        node = shutil.which('node')
        if name not in scripts or not script.is_file() or not node:
            raise ValueError('Cannot safely execute this Windows batch shim. Configure platform_argv with its native executable/Node entrypoint or an explicitly reviewed shell command.')
        return [node, str(script), *argv[1:]]
    return [executable, *argv[1:]]


def command_cwd(root, d):
    p = Path(root, d.get('cwd', '.')).resolve(strict=True)
    if not p.is_relative_to(Path(root)) or not p.is_dir():
        raise ValueError('Command directory escapes or is missing from the selected checkout.')
    return str(p)


def snapshot(root, d):
    """Content snapshot, not a hermetic build or filesystem transaction."""
    started = time.monotonic()
    h = hashlib.sha256(json.dumps(d, sort_keys=True).encode())
    count = 0
    try:
        roots = [Path(root)] + [Path(root, p).resolve(strict=True) for p in d.get('inputs', [])]
        for base in roots:
            h.update(str(base).encode())
            if base.is_file():
                paths = [base]
            else:
                probe = subprocess.run(['git', '-C', str(base), 'rev-parse', '--show-toplevel'], capture_output=True, timeout=3)
                if probe.returncode or Path(os.fsdecode(probe.stdout).strip()).resolve() != base:
                    return {'state': 'unknown', 'reason': f'Input directory is not an exact Git root: {base}'}
                for args in [['rev-parse', 'HEAD'], ['ls-files', '--stage', '-z']]:
                    result = subprocess.run(['git', '-C', str(base), *args], capture_output=True, timeout=3)
                    if result.returncode:
                        return {'state': 'unknown', 'reason': 'Git HEAD/index is unavailable.'}
                    h.update(result.stdout)
                result = subprocess.run(['git', '-C', str(base), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], capture_output=True, check=True, timeout=3)
                paths = [base / os.fsdecode(p) for p in sorted(set(result.stdout.split(b'\0'))) if p]
            for path in paths:
                count += 1
                if count > 50000 or time.monotonic() - started > 10:
                    return {'state': 'unknown', 'reason': 'Source snapshot exceeded its 50,000-file/10-second budget.'}
                h.update(str(path).encode())
                if path.is_symlink():
                    h.update(b'link:' + os.fsencode(os.readlink(path)))
                    return {'state': 'unknown', 'reason': f'Symlink input requires explicit target review: {path}'}
                if not path.exists():
                    h.update(b'deleted'); continue
                before = path.stat()
                h.update(str(before.st_mode).encode())
                if not path.is_file() or before.st_size > 32 * 1024 * 1024:
                    return {'state': 'unknown', 'reason': f'Unsupported or oversized input: {path}'}
                with path.open('rb') as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        h.update(chunk)
                after = path.stat()
                if any(getattr(before, field) != getattr(after, field) for field in ('st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_ino', 'st_dev', 'st_mode')):
                    return {'state': 'unknown', 'reason': 'An input changed while being read.'}
        return {'state': 'observed', 'digest': h.hexdigest(), 'files': count, 'at': time.time()}
    except (OSError, subprocess.SubprocessError) as e:
        return {'state': 'unknown', 'reason': str(e)}


class Store:
    def __init__(self, directory=None):
        home = Path((os.environ.get('LUVUS_HOME') or Path.home() / '.luvus'))
        injected = os.environ.get('LUVUS_MODULE_CONFIG_DIR') if os.environ.get('LUVUS_TOOLKIT_FEATURE') == 'project-commands' else None
        self.root = Path(directory or injected or home / 'modules/config' / 'kacper.toolkit' / 'features' / 'project-commands').resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.logs = self.root / 'runs'
        self.logs.mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request TEXT UNIQUE, root TEXT, command TEXT,
                    state TEXT, guard TEXT, created REAL, updated REAL, data TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS live_command ON runs(root,command) WHERE state IN ('starting','running','cancelling','unknown');
                CREATE UNIQUE INDEX IF NOT EXISTS live_guard ON runs(guard) WHERE guard IS NOT NULL AND state IN ('starting','running','cancelling','unknown');
                CREATE TABLE IF NOT EXISTS requests (request TEXT PRIMARY KEY, run TEXT NOT NULL);
                INSERT OR IGNORE INTO requests SELECT request,id FROM runs;
            ''')
        if os.name != 'nt':
            os.chmod(self.root / 'runs.sqlite3', 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'runs.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def config(self):
        try:
            data = json.loads((self.root / 'projects.json').read_text())
            if data.get('version') != 1 or not isinstance(data.get('projects'), dict):
                raise ValueError('Unsupported or malformed projects.json; the original is preserved.')
            return data
        except FileNotFoundError:
            return {'version': 1, 'projects': {}}

    def save_config(self, data, previous):
        if data.get('version') != 1 or not isinstance(data.get('projects'), dict):
            raise ValueError('Expected version 1 and projects object.')
        for root, project in data['projects'].items():
            if not Path(root).is_absolute(): raise ValueError('Project paths must be absolute.')
            # Missing worktrees are retained as inactive configuration, not launch targets.
            resolved = str(Path(root).resolve())
            if root != resolved: raise ValueError('Use the canonical project path: ' + resolved)
            if not isinstance(project, dict): raise ValueError('Each project must be an object.')
            ids = [validate_definition(d)['id'] for d in project.get('commands', [])]
            if len(set(ids)) != len(ids):
                raise ValueError('Duplicate command IDs.')
            for provider in project.get('health_providers', []):
                if not isinstance(provider.get('name'), str): raise ValueError('Health providers need a name.')
                if provider.get('argv'): validate_definition({'id': 'provider', 'name': provider['name'], 'argv': provider['argv']})
                for action, argv in provider.get('recovery_actions', {}).items():
                    validate_definition({'id': action, 'name': action, 'argv': argv})
            bindings = set()
            for binding in project.get('containers', []):
                if not re.fullmatch(r'[0-9a-f]{64}', binding.get('id', '')) or binding.get('owner') not in ('manual', 'aspire'):
                    raise ValueError('Container bindings need a full reviewed ID and manual/aspire owner.')
                if any(not isinstance(binding.get(k), str) or not binding[k] for k in ('context', 'daemon', 'created')):
                    raise ValueError('Container bindings need context, daemon and creation identity from Inspect binding.')
                key = (binding['daemon'], binding['id'])
                if key in bindings: raise ValueError('This container is already bound to the project.')
                bindings.add(key)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.config() != previous:
                raise ValueError('Configuration changed in another console. Reload before saving.')
            fd, path = tempfile.mkstemp(dir=self.root)
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())
                os.replace(path, self.root / 'projects.json')
            finally:
                if os.path.exists(path): os.unlink(path)

    def project(self, root):
        return self.config()['projects'].get(canonical(root), {'commands': [], 'containers': [], 'health_providers': []})

    def definition(self, root, identity):
        for d in self.project(root).get('commands', []):
            if d['id'] == identity: return validate_definition(d)
        raise ValueError('Command was removed or is not configured for this checkout.')

    def get(self, identity):
        with self.db() as db:
            row = db.execute('SELECT data FROM runs WHERE id=?', (identity,)).fetchone()
        if not row: raise ValueError('Run not found.')
        return json.loads(row['data'])

    def update(self, identity, **fields):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM runs WHERE id=?', (identity,)).fetchone()
            if not row: raise ValueError('Run not found.')
            d = json.loads(row['data']); d.update(fields); d['updated'] = time.time()
            db.execute('UPDATE runs SET state=?,updated=?,data=? WHERE id=?', (d['state'], d['updated'], json.dumps(d), identity))
        return d

    def list(self, root=None):
        with self.db() as db:
            rows = db.execute('SELECT data FROM runs ' + ('WHERE root=? ' if root else '') + 'ORDER BY created DESC', (root,) if root else ()).fetchall()
        return [json.loads(r['data']) for r in rows]

    def reserve(self, root, definition, request, session):
        if not isinstance(request, str) or not request or len(request) > 200:
            raise ValueError('Request ID must be a nonempty string of at most 200 characters.')
        root = canonical(root)
        d = validate_definition(definition)
        command_cwd(root, d); command_argv(d)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT data FROM runs JOIN requests ON runs.id=requests.run WHERE requests.request=?', (request,)).fetchone()
            if not existing:
                existing = db.execute('SELECT data FROM runs WHERE root=? AND command=? AND state IN (?,?,?,?)', (root, d['id'], *ACTIVE)).fetchone()
            if existing:
                old = json.loads(existing['data'])
                if old['root'] != root or old['definition'] != d or old['session'] != session:
                    raise ValueError('Existing run/request has a different definition, checkout or session; inspect it before retrying.')
                db.execute('INSERT OR IGNORE INTO requests VALUES (?,?)', (request, old['id']))
                return {**old, 'request': request}, False
            now = time.time()
            record = {'id': uuid.uuid4().hex, 'request': request, 'root': root, 'definition': d, 'session': session,
                      'state': 'starting', 'created': now, 'updated': now, 'freshness': 'unknown', 'cancel': False,
                      'force': False, 'truncated': False, 'readiness': 'unknown'}
            try:
                db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)', (record['id'], request, root, d['id'], 'starting', d.get('exclusive_group'), now, now, json.dumps(record)))
                db.execute('INSERT INTO requests VALUES (?,?)', (request, record['id']))
            except sqlite3.IntegrityError as e:
                raise ValueError('A conflicting managed stack is already active or unresolved. Inspect its run first.') from e
        return record, True

    def by_request(self, request):
        with self.db() as db:
            row = db.execute('SELECT data FROM runs JOIN requests ON runs.id=requests.run WHERE requests.request=?', (request,)).fetchone()
        if not row: raise ValueError('Request not found in retained history; inspect before starting a new attempt.')
        return {**json.loads(row['data']), 'request': request}

    def prune(self):
        completed = [r for r in self.list() if r['state'] not in ACTIVE]
        counts, size = {}, 0
        for r in completed:
            path = self.logs / (r['id'] + '.log')
            counts[r['root']] = counts.get(r['root'], 0) + 1
            folder = self.logs / r['id']
            size += (path.stat().st_size if path.exists() else 0) + len(json.dumps(r).encode())
            if folder.exists(): size += sum(p.stat().st_size for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
            if counts[r['root']] > 50 or time.time() - r['created'] > 14 * 86400 or size > 500 * 1024 * 1024:
                with self.db() as db:
                    db.execute('DELETE FROM requests WHERE run=?', (r['id'],))
                    db.execute('DELETE FROM runs WHERE id=? AND state NOT IN (?,?,?,?)', (r['id'], *ACTIVE))
                for p in self.logs.glob(r['id'] + '.*'): p.unlink(missing_ok=True)
                if folder.is_symlink(): folder.unlink()
                elif folder.exists():
                    import shutil
                    shutil.rmtree(folder)
