"""Bounded diagnostics; collectors return evidence, never recovery side effects."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from .model import LOG_LIMIT, command_argv, executable_argv, platform

ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
DIAGNOSTIC = re.compile(r'^(?P<file>.+?)\((?P<line>\d+)(?:,(?P<column>\d+))?\):\s*(?P<severity>error|warning)\s*(?P<code>[\w-]+)?\s*:\s*(?P<message>.*)$', re.I)


def capture(argv, *, input=None, env=None, timeout=5, limit=65536):
    """Bound memory and runtime for external read-only collectors."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as incoming:
        if input: incoming.write(input.encode())
        incoming.seek(0)
        child = subprocess.Popen(executable_argv(argv), stdin=incoming, stdout=out, stderr=err, env=env)
        deadline = time.monotonic() + timeout
        exceeded = False
        try:
            while child.poll() is None:
                if time.monotonic() > deadline or os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > limit:
                    exceeded = True; child.terminate()
                    try: child.wait(timeout=1)
                    except subprocess.TimeoutExpired: child.kill(); child.wait()
                    break
                time.sleep(.02)
            out.seek(0); err.seek(0)
            stdout, stderr = out.read(limit + 1), err.read(limit + 1)
            if exceeded or len(stdout) + len(stderr) > limit:
                raise ValueError('Collector exceeded its time/output limit. Partial output: ' + redact((stdout + stderr)[-2000:].decode('utf-8', 'replace')))
            return subprocess.CompletedProcess(argv, child.returncode, stdout.decode('utf-8', 'replace'), stderr.decode('utf-8', 'replace'))
        finally:
            if child.poll() is None: child.kill(); child.wait()


def safe_text(text):
    text = ANSI.sub('', str(text))
    return ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127))


def redact(text):
    text = safe_text(text)
    for key, value in os.environ.items():
        if len(value) >= 8 and any(s in key.upper() for s in ('TOKEN', 'PASSWORD', 'SECRET', 'API_KEY')):
            text = text.replace(value, '[redacted]')
    return re.sub(r'(?i)(https?://[^\s?]+)\?[^\s]+', r'\1?[redacted]', text)


def discover(root):
    root = Path(root)
    out = []
    def add(identity, name, argv, **fields):
        out.append({'id': identity, 'name': name, 'argv': argv, 'kind': 'command', 'category': 'validation', **fields})
    for package in (root / 'package.json', root / 'web/package.json'):
        if not package.exists(): continue
        data = json.loads(package.read_text())
        folder = str(package.parent.relative_to(root))
        manager = 'pnpm' if 'pnpm' in data.get('packageManager', '') or (root / 'pnpm-lock.yaml').exists() else 'npm'
        prefix = [] if folder == '.' or manager == 'npm' else ['--dir', folder]
        executable = ['corepack', manager] if manager == 'pnpm' else ['npm']
        for name, source in data.get('scripts', {}).items():
            kind = 'service' if name in ('dev', 'start', 'web:dev', 'test:watch') else 'command'
            category = next((c for c in ('build', 'test', 'lint', 'setup') if c in name), 'custom')
            fields = {'kind': kind, 'category': category, 'source': f'{package.relative_to(root)} scripts.{name}: {source}'}
            if manager == 'npm': fields['cwd'] = folder
            if '${' in source or re.search(r'(^|[;&])\s*(export |\./.*\.sh)', source): fields['unsupported_platforms'] = ['windows']
            add(f'{folder.replace("/", ".")}:script:{name}', f'{folder}: {name}', executable + prefix + ['run', name], **fields)
        add(f'{folder}:setup', f'{folder}: install dependencies', executable + prefix + ['install', '--frozen-lockfile'] if manager == 'pnpm' else executable + ['ci'], category='setup', cwd='.' if manager == 'pnpm' else folder)
    for solution in sorted([*root.glob('*.slnx'), *root.glob('*.sln')]):
        name = solution.name
        for verb in ('restore', 'build', 'test'):
            add(f'dotnet:{verb}:{solution.stem}', f'.NET {verb}: {name}', ['dotnet', verb, name] + ([] if verb == 'restore' else ['--configuration', 'Release']), category='setup' if verb == 'restore' else verb)
        add(f'dotnet:trx:{solution.stem}', f'.NET tests with TRX: {name}', ['dotnet', 'test', name, '--configuration', 'Release', '--logger', 'trx', '--results-directory', '{run_dir}'], category='test', report_format='trx', report='*.trx')
    if (root / 'web/package.json').exists():
        add('web:eslint-report', 'Web lint (structured report)', ['corepack', 'pnpm', '--dir', 'web', 'exec', 'eslint', '--max-warnings=0', '--format', 'json', '--output-file', '{run_dir}/eslint.json'], category='lint', report_format='eslint', report='eslint.json', problem_cwd='web')
        add('web:vitest-report', 'Web tests (structured report)', ['corepack', 'pnpm', '--dir', 'web', 'exec', 'vitest', 'run', '--reporter=json', '--outputFile={run_dir}/vitest.json'], category='test', report_format='vitest', report='vitest.json', problem_cwd='web')
    apphosts = list((root / 'src').glob('*.AppHost/*.csproj')) if (root / 'src').exists() else []
    for project in apphosts:
        fields = {'kind': 'service', 'category': 'dev', 'urls': ['http://localhost:3000', 'http://localhost:8025', 'http://localhost:9001'],
                  'ports': [3000, 5100, 5101, 5102, 5103, 5104, 8025, 9000, 9001], 'exclusive_group': 'bookulum-local-aspire',
                  'inputs': ['../email-service', '../billing-service', '../user-service'], 'aspire': True,
                  'source': 'Existing Bookulum Aspire stack; fixed ports and volumes. Start can run local migrations.'}
        add('aspire:stack', 'Bookulum local Aspire stack', ['dotnet', 'run', '--project', str(project.relative_to(root)), '--no-launch-profile', '--environment', 'DOTNET_ENVIRONMENT=Development'], **fields)
        # The equivalent package script needs the same guard; it must not bypass ownership.
        for d in out:
            if d.get('source', '').startswith('package.json scripts.dev:'):
                d.update({k: v for k, v in fields.items() if k != 'source'})
    return out


def parse_run(store, r):
    path = store.logs / (r['id'] + '.log')
    text = safe_text(path.read_bytes()[:LOG_LIMIT].decode('utf-8', 'replace')) if path.exists() else ''
    problems = []
    dropped = False
    def add(problem):
        nonlocal dropped
        if len(problems) < 5000: problems.append(problem)
        else: dropped = True
    def finish(result):
        result['items'] = [{k: safe_text(v) if isinstance(v, str) else v for k, v in p.items()} for p in problems]
        if dropped or r.get('truncated'):
            result['truncated'] = True
            if result['state'] != 'parser-error': result['state'] = 'partial'
        return result
    unparsed = 0
    for number, line in enumerate(text.splitlines(), 1):
        m = DIAGNOSTIC.match(line.strip())
        if m:
            p = m.groupdict(); p['line'] = int(p['line']); p['column'] = int(p['column'] or 1); p['output_line'] = number
            add(p)
        elif re.search(r'\b(error|warning|failed)\b', line, re.I): unparsed += 1
    d = r['definition']
    result = {'state': 'partial' if unparsed else 'text-only', 'items': problems, 'unparsed_lines': unparsed, 'truncated': r.get('truncated', False)}
    if not d.get('report_format'): return finish(result)
    try:
        base = (store.logs / r['id']).resolve()
        reports = sorted(base.glob(d['report']))
        if not reports or len(reports) > 100: raise ValueError('Expected 1–100 report files.')
        if sum(p.stat().st_size for p in reports) > LOG_LIMIT: raise ValueError('Reports exceed 10 MiB combined.')
        for path in reports:
            report = path.resolve(strict=True)
            if not report.is_relative_to(base) or report.stat().st_size > LOG_LIMIT: raise ValueError('Report escaped run directory or exceeded size limit.')
            raw = report.read_bytes()
            if d['report_format'] == 'trx':
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('XML entities/DOCTYPE are not supported.')
                root = ET.fromstring(raw)
                if root.tag.rsplit('}', 1)[-1] != 'TestRun': raise ValueError('Not a TRX TestRun.')
                for node in root.iter():
                    if node.tag.rsplit('}', 1)[-1] == 'UnitTestResult' and node.get('outcome') in ('Failed', 'Error', 'Timeout', 'Aborted'):
                        message = ' '.join(node.itertext()).strip()
                        location = re.search(r'\bin (.+):line (\d+)', message)
                        add({'file': location[1] if location else '', 'line': int(location[2]) if location else 0, 'column': 1, 'severity': 'error', 'message': node.get('testName', '') + ': ' + message})
            else:
                data = json.loads(raw)
                if d['report_format'] == 'eslint':
                    if not isinstance(data, list): raise ValueError('Expected ESLint results array.')
                    for f in data:
                        for p in f['messages']:
                            add({'file': f['filePath'], 'line': p.get('line', 1), 'column': p.get('column', 1), 'severity': 'error' if p['severity'] == 2 else 'warning', 'message': p['message']})
                else:
                    if not isinstance(data.get('testResults'), list): raise ValueError('Expected Vitest testResults.')
                    failures = 0
                    for f in data['testResults']:
                        failed_assertions = 0
                        for p in f.get('assertionResults', []):
                            if p['status'] == 'failed':
                                failed_assertions += 1
                                add({'file': f['name'], 'line': (p.get('location') or {}).get('line', 1), 'column': 1, 'severity': 'error', 'message': p.get('fullName', '') + '\n' + '\n'.join(p.get('failureMessages', []))})
                        if f.get('status') == 'failed' or f.get('message'):
                            failures += 1
                            if not failed_assertions or f.get('message'):
                                add({'file': f.get('name', ''), 'line': 1, 'column': 1, 'severity': 'error', 'message': f.get('message') or 'Test suite failed before producing assertion diagnostics; open raw output.'})
                        elif failed_assertions: failures += 1
                    if data.get('numFailedTestSuites', 0) > failures or (data.get('success') is False and not failures):
                        add({'file': '', 'line': 0, 'column': 1, 'severity': 'error', 'message': 'Vitest reported failures without complete suite diagnostics. Open raw output.'})
                        unparsed += 1
        result['state'] = 'partial' if unparsed else 'parsed'
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as e:
        result.update(state='parser-error', error=safe_text(str(e)))
    return finish(result)


def source_location(r, problem):
    d = r['definition']
    root = Path(r['root'])
    candidate = Path(problem.get('file', ''))
    if not problem.get('file'): raise ValueError('This problem has no source location; open output instead.')
    if not candidate.is_absolute(): candidate = root / d.get('problem_cwd', d.get('cwd', '.')) / candidate
    candidate = candidate.resolve(strict=True)
    allowed = [root] + [Path(root, p).resolve() for p in d.get('inputs', [])]
    if not any(candidate == p or candidate.is_relative_to(p) for p in allowed):
        raise ValueError('Source path is outside approved input roots. Review it manually; output remains available.')
    if not candidate.is_file() or candidate.stat().st_size > 2 * 1024 * 1024: raise ValueError('Source is unavailable or too large for preview.')
    return candidate, max(1, int(problem.get('line') or 1))


def detected_urls(text):
    urls = []
    for url in re.findall(r'https?://[^\s<>"\x1b]+', safe_text(text)):
        url = url.rstrip('.,);]')
        u = urlparse(url)
        if u.hostname and not u.username and not u.password and not u.query and not u.fragment and url not in urls:
            urls.append(url)
    return urls[:20]


def health(store, root, host=None):
    checks = []
    def add(name, state, evidence, action='Inspect configuration'):
        checks.append({'name': name, 'state': state, 'evidence': redact(evidence), 'at': time.time(), 'action': action})
    try: config = store.project(root)
    except (OSError, ValueError) as e:
        add('Project configuration', 'failing', str(e), 'Edit configuration')
        return checks
    tools = {}
    if (Path(root) / '.git').exists(): tools['git'] = ['--version']
    packages = []
    for path in (Path(root) / 'package.json', Path(root) / 'web/package.json'):
        if path.exists():
            try:
                package = json.loads(path.read_text()); packages.append(package)
                tools['node'] = ['--version']
                manager = package.get('packageManager', '').split('@')[0]
                tools['corepack' if manager in ('pnpm', 'yarn') or (Path(root) / 'pnpm-lock.yaml').exists() else 'npm'] = ['--version']
            except (OSError, ValueError) as e: add(str(path), 'failing', str(e), 'Edit configuration')
    if (Path(root) / 'global.json').exists() or any(Path(root).glob('*.sln*')) or any(Path(root).glob('*.csproj')) or any((Path(root) / 'src').glob('*/*.csproj')):
        tools['dotnet'] = ['--list-sdks']
    for d in config.get('commands', []):
        try:
            argv = command_argv(d)
            # Only known diagnostic tools run --version; arbitrary commands are resolved, never executed.
            tools.setdefault(argv[0], None)
        except (ValueError, KeyError, TypeError) as e: add(d.get('name', 'Command'), 'failing', str(e), 'Edit configuration')
    for name, args in tools.items():
        binary = shutil.which(name)
        if not binary: add(name, 'missing', 'Executable is absent from the inherited PATH.', 'Configure PATH'); continue
        if args is None:
            add(name, 'healthy', 'Executable found: ' + binary, 'Edit configuration'); continue
        try:
            r = capture([binary, *args])
            state = 'healthy' if r.returncode == 0 else 'failing'
            output = r.stdout[:2000]
            if name == 'node':
                for package in packages:
                    required = package.get('engines', {}).get('node', '')
                    major = re.search(r'(\d+)', output)
                    minimum = re.fullmatch(r'>=\s*(\d+)(?:\.\d+)*', required)
                    if minimum and (not major or int(major[1]) < int(minimum[1])): state = 'failing'
                    elif required and not minimum and state == 'healthy': state = 'unverified'
                    output += '\nRequired: ' + required
            if name == 'dotnet' and (Path(root) / 'global.json').exists():
                sdk = json.loads((Path(root) / 'global.json').read_text()).get('sdk', {})
                if sdk.get('rollForward') == 'disable' and sdk.get('version') not in [line.split()[0] for line in output.splitlines() if line.split()]: state = 'failing'
                output += '\nSDK contract: ' + json.dumps(sdk)
            add(name, state, binary + '\n' + output, 'Configure PATH / installed versions')
        except (OSError, ValueError, subprocess.SubprocessError) as e: add(name, 'unverified', str(e))
    try:
        config = store.project(root)
        add('Project configuration', 'healthy', f'{len(config.get("commands", []))} reviewed commands', 'Edit configuration')
        if config.get('containers') or any((Path(root) / 'src').glob('*.AppHost/*.csproj')):
            if not shutil.which('docker'): add('Docker', 'missing', 'Docker CLI is absent from inherited PATH.', 'Configure PATH / Docker')
            else:
                try:
                    version = docker_command(config.get('docker_context', 'default'), 'info', '--format', '{{.ServerVersion}}')
                    add('Docker daemon', 'healthy', version, 'Inspect Docker context')
                except (OSError, ValueError, subprocess.SubprocessError) as e: add('Docker daemon', 'failing', str(e), 'Inspect Docker context')
        for d in config.get('commands', []):
            try: command_argv(d)
            except ValueError as e: add(d['name'], 'failing', str(e), 'Edit configuration')
            for p in d.get('inputs', []):
                target = Path(root, p).resolve()
                add('Input ' + p, 'healthy' if target.exists() else 'missing', str(target), 'Edit configuration')
        if any('novulum-platform' in d.get('source', '') for d in discover(root)):
            target = Path(os.environ.get('NOVULUM_PLATFORM_ROOT', str(Path(root).parent / 'novulum-platform')))
            add('Novulum platform', 'healthy' if target.exists() else 'missing', str(target), 'Edit configuration')
        seen = set()
        for r in store.list(root)[:50]:
            if r['definition']['id'] in seen: continue
            seen.add(r['definition']['id'])
            if r['definition'].get('category') == 'setup':
                from .runtime import run_status
                r = run_status(store, r['id'])
                add('Setup: ' + r['definition']['name'], 'healthy' if r['state'] == 'passed' and r.get('freshness') == 'current' else 'failing' if r['state'] == 'failed' else 'unverified', f'Run {r["id"]}: {r["state"]}; source {r.get("freshness", "unknown")}; recorded {r["updated"]}', 'Open run ' + r['id'])
            if r['definition']['kind'] == 'service':
                from .runtime import reconcile, readiness
                r = reconcile(store, r)
                ready = readiness(r['definition'], r.get('child')) if r['state'] == 'running' else 'unknown'
                add('Service: ' + r['definition']['name'], 'healthy' if ready == 'ready' else 'failing' if ready == 'unhealthy' else 'unverified', f'{r["state"]}; readiness {ready}', 'Open run ' + r['id'])
        for provider in config.get('health_providers', []):
            if not provider.get('argv'):
                add(provider.get('name', 'Module provider'), 'unverified', 'No owner-supplied read-only health command is configured.'); continue
            try:
                env = {k: v for k, v in os.environ.items() if not k.startswith('LUVUS_MODULE_')}
                r = capture(provider['argv'], input=json.dumps({'version': 1, 'root': root}) + '\n', env=env)
                if r.returncode or len(r.stdout) > 65536: raise ValueError('Provider failed or exceeded 64 KiB.')
                data = json.loads(r.stdout)
                for c in data['checks'][:50]:
                    if c['state'] not in ('healthy', 'missing', 'failing', 'unverified'): raise ValueError('Invalid health state.')
                    add(provider['name'] + ': ' + c['name'], c['state'], c['evidence'][:4000], 'Owner recovery: ' + c.get('action', 'Open owning module'))
                    if c.get('recovery'):
                        recovery = c['recovery']
                        if set(recovery) != {'module', 'action'} or not all(isinstance(v, str) and v for v in recovery.values()): raise ValueError('Invalid owner recovery reference.')
                        checks[-1]['recovery'] = recovery
                        if recovery['module'] == provider.get('module') and recovery['action'] in provider.get('recovery_actions', {}):
                            checks[-1]['recovery_argv'] = provider['recovery_actions'][recovery['action']]
                    if c.get('at'):
                        checks[-1]['provider_at'] = c['at']
                        if not isinstance(c['at'], (int, float)) or time.time() - c['at'] > 60: checks[-1]['state'] = 'unverified'
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e: add(provider.get('name', 'Module provider'), 'unverified', str(e))
    except (ValueError, OSError) as e: add('Project configuration', 'failing', str(e), 'Edit configuration')
    if host:
        try:
            caps = host.call('uhp.capabilities')
            required = {'terminal.backend.create', 'terminal.backend.inventory', 'terminal.backend.validate'}
            missing = required - set(caps.get('methods', []))
            add('Luvus connection', 'failing' if missing else 'healthy', 'Missing methods: ' + ', '.join(sorted(missing)) if missing else 'Required native methods advertised.')
            for module in host.call('module.list')['modules']:
                if module.get('warning') or not module.get('runnable'): add(module['name'], 'failing', module.get('warning') or 'Not runnable', 'Open owning module settings')
            add('Owner connections', 'unverified', 'Tracker/provider credentials are owned by their modules; no credentials were queried.', 'Open owning module')
        except (ValueError, OSError, subprocess.SubprocessError) as e: add('Luvus connection', 'unverified', str(e))
    return checks


def resources(host, store=None):
    import psutil
    inv = host.inventory()
    used, rows = set(), []
    managed = {r.get('locator', {}).get('terminal_id'): r for r in store.list()} if store else {}
    for terminal in inv['terminals']:
        root = terminal.get('root_process') or {}
        run = managed.get(terminal['terminal_id'])
        item = {'pane': terminal['pane_id'], 'terminal': terminal, 'workspace': terminal.get('workspace'), 'name': (run['definition']['kind'] + ': ' + run['definition']['name']) if run else terminal.get('label') or terminal.get('terminal_title'), 'state': 'unverified', 'cpu_seconds': None, 'rss_bytes': None}
        try:
            p = psutil.Process(root['pid'])
            if not root.get('start_marker'):
                rows.append(item); continue
            locator = {'server_generation': inv['server_generation'], 'terminal_id': terminal['terminal_id'], 'pane_id': terminal['pane_id'], 'expected_root': root}
            host.validate(locator)
            total, memory, count, times = 0, 0, 0, {}
            partial = False
            for proc in [p, *p.children(recursive=True)]:
                try:
                    key = (proc.pid, proc.create_time())
                    if key in used: continue
                    used.add(key)
                    t = proc.cpu_times(); total += t.user + t.system
                    times[str(key)] = t.user + t.system
                    memory += proc.memory_info().rss; count += 1
                except psutil.Error: partial = True
            host.validate(locator)
            item.update(state='partial' if partial else 'observed', cpu_seconds=total, process_times=times, rss_bytes=memory, processes=count, at=time.time())
        except (psutil.Error, OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError): pass
        rows.append(item)
    return rows


def docker_command(context, *args):
    # Explicit context, no inherited DOCKER_HOST redirect to a different daemon.
    env = {k: v for k, v in os.environ.items() if k not in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
    result = capture(['docker', '--context', context, *args], env=env, timeout=15)
    if result.returncode: raise ValueError(redact((result.stdout + result.stderr)[:8000]))
    return result.stdout + (result.stderr if args[0] == 'logs' else '')


def inspect_container(context, identity):
    if not re.fullmatch(r'[0-9a-f]{12,64}', identity): raise ValueError('Use a concrete container ID, not a name or option.')
    daemon = docker_command(context, 'info', '--format', '{{.ID}}').strip()
    fmt = '{{json .Id}}\n{{json .Created}}\n{{json .Name}}\n{{json .State.Status}}\n{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}'
    values = [json.loads(line) for line in docker_command(context, 'inspect', '--type', 'container', '--format', fmt, identity).splitlines()]
    if len(values) != 5 or not daemon: raise ValueError('Container identity could not be verified.')
    return dict(zip(('id', 'created', 'name', 'status', 'health'), values), context=context, daemon=daemon)


def container_action(binding, action):
    current = inspect_container(binding['context'], binding['id'])
    if any(current[k] != binding[k] for k in ('id', 'created', 'daemon', 'context')):
        raise ValueError('Container/daemon identity changed. Review the binding again.')
    if action == 'status': return current
    if action == 'logs': return redact(docker_command(binding['context'], 'logs', '--tail', '200', binding['id'])[-65536:])
    if action not in ('start', 'stop'): raise ValueError('Only status, logs, start and stop are supported.')
    if binding.get('owner') != 'manual': raise ValueError('Aspire/unknown-owned containers must be controlled through their owning stack.')
    return redact(docker_command(binding['context'], action, binding['id'])[:8000])


def recorded_container_action(store, root, binding, action):
    import uuid
    if action not in ('status', 'logs', 'start', 'stop'): raise ValueError('Unsupported container action.')
    definition = {'id': 'container:' + binding['id'][:32] + ':' + action, 'name': 'Container ' + action + ': ' + binding.get('name', binding['id'][:12]), 'category': 'container', 'kind': 'command', 'argv': ['docker', '--context', binding['context'], action, binding['id']]}
    record, created = store.reserve(root, definition, uuid.uuid4().hex, {})
    if not created: raise ValueError('Previous container action remains unresolved. Inspect status and acknowledge its run first.')
    store.update(record['id'], container_binding=binding, freshness='not-applicable')
    path = store.logs / (record['id'] + '.log')
    try:
        result = container_action(binding, action)
        text = json.dumps(result, indent=2) if isinstance(result, dict) else result
        path.write_text(redact(text)[:65536])
        return store.update(record['id'], state='passed', exit_code=0, freshness='not-applicable', result=result, finished=time.time())
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        path.write_text(redact(str(e))[:65536])
        store.update(record['id'], state='unknown' if action in ('start', 'stop') else 'failed', error=redact(str(e)), freshness='not-applicable', finished=time.time())
        raise ValueError(f'Container action output retained in run {record["id"]}: {redact(str(e))}') from e
    finally:
        if os.name != 'nt' and path.exists(): os.chmod(path, 0o600)
        store.prune()
