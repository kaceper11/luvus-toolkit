"""One supervisor per run. The console never owns the child process."""
import codecs
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import uuid

import psutil
from .model import ACTIVE, LOG_LIMIT, Store, command_argv, command_cwd, executable_argv, snapshot, validate_definition
from .host import entry_args


def alive(identity):
    if not identity: return False
    try:
        p = psutil.Process(identity['pid'])
        return p.create_time() == identity['created'] and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, KeyError): return False


def process_identity(pid):
    return {'pid': pid, 'created': psutil.Process(pid).create_time()}


class WindowsJob:
    """A gated helper joins the job before it can spawn the actual command."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.k.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]; self.k.CreateJobObjectW.restype = w.HANDLE
        self.k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]; self.k.AssignProcessToJobObject.restype = w.BOOL
        self.k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]; self.k.TerminateJobObject.restype = w.BOOL
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, w.LPVOID, w.DWORD]
        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64), ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t), ('active', w.DWORD), ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(n, c.c_uint64) for n in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t), ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle: raise OSError(c.get_last_error(), 'CreateJobObject failed')
        info = Extended(); info.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway.
        if not self.k.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            self.close(); raise OSError(c.get_last_error(), 'SetInformationJobObject failed')

    def assign(self, child):
        if not self.k.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise OSError(self.c.get_last_error(), 'Cannot establish Windows job ownership')

    def stop(self):
        if not self.k.TerminateJobObject(self.handle, 130):
            raise OSError(self.c.get_last_error(), 'TerminateJobObject failed')

    def close(self):
        if self.handle: self.k.CloseHandle(self.handle); self.handle = None


def run_and_record(argv, cwd, result_path):
    try:
        code = subprocess.call(argv, cwd=cwd, stdin=subprocess.DEVNULL)
        result = {'exit_code': code}
    except OSError as e:
        result = {'spawn_error': str(e)}
    temporary = Path(str(result_path) + '.tmp')
    temporary.write_text(json.dumps(result))
    os.replace(temporary, result_path)
    return result.get('exit_code', 125)


def gated_child(argv, cwd, result_path):
    """EOF means the parent died before owning this Windows helper: spawn nothing."""
    import sys
    if sys.stdin.buffer.read(1) != b'G': return 125
    return run_and_record(argv, cwd, result_path)


def posix_child(argv, cwd, result_path):
    """Keep the group leader alive until its supervisor closes the ownership pipe."""
    import sys
    # Caught (not ignored) dispositions reset to default when the command execs.
    signal.signal(signal.SIGTERM, lambda *_: None)
    signal.signal(signal.SIGINT, lambda *_: None)
    def owner_closed():
        sys.stdin.buffer.read()
        os.killpg(os.getpgrp(), signal.SIGKILL)
    threading.Thread(target=owner_closed, daemon=True).start()
    run_and_record(argv, cwd, result_path)
    os.close(1); os.close(2)
    # The watchdog kills this still-owned group on supervisor exit, including crashes.
    while True: time.sleep(3600)


def conflict_ports(definition):
    for port in definition.get('ports', []):
        try:
            with socket.socket() as s:
                s.bind(('127.0.0.1', port))
        except OSError:
            raise ValueError(f'Port {port} is unavailable. Inspect its owner; no process was stopped.')


def readiness(d, identity=None):
    r = d.get('readiness')
    if not r: return 'unknown'
    if not alive(identity): return 'unknown'
    try:
        from urllib.parse import urlparse
        url = urlparse(r.get('url', ''))
        port = r.get('port') or url.port or (443 if url.scheme == 'https' else 80)
        owner = psutil.Process(identity['pid'])
        if not any(c.status == psutil.CONN_LISTEN and c.laddr.port == port for p in [owner, *owner.children(recursive=True)] for c in p.net_connections(kind='inet')):
            return 'unknown'
        if r['kind'] == 'tcp':
            with socket.create_connection((r.get('host', '127.0.0.1'), r['port']), timeout=1): pass
        else:
            # Do not follow redirects to a credential-bearing or nonlocal endpoint.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args): return None
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(r['url'], timeout=1) as response:
                if response.status != r.get('status', 200): return 'unhealthy'
        return 'ready'
    except psutil.Error: return 'unknown'
    except (OSError, ValueError): return 'unhealthy'


def session_identity(host):
    return {'socket': os.environ.get('LUVUS_SOCKET_PATH'), 'generation': host.inventory()['server_generation']}


def launch(store, host, root, command, request=None, reviewed=None, expected_session=None):
    definition = store.definition(root, command)
    if reviewed is not None and definition != validate_definition(reviewed):
        raise ValueError('Command changed since review. Review the new definition before running it.')
    session = session_identity(host)
    if expected_session is not None and expected_session != session:
        raise ValueError('Luvus session changed since review. Review this launch again.')
    # Reconcile known dead supervisors before checking the uniqueness reservation.
    for old in store.list(root): reconcile(store, old)
    record, created = store.reserve(root, definition, request or uuid.uuid4().hex, session)
    if not created: return record
    try:
        conflict_ports(definition)
    except Exception as e:
        return store.update(record['id'], state='failed', error=str(e))
    try:
        result = host.create(root, entry_args('supervise', '--store', store.root, '--run', record['id'], '--luvus-bin', host.binary), definition['name'])
        # Keep the full native response as evidence; supervisor binds its own locator.
        store.update(record['id'], terminal_creation=result)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        # The worker may already be running. Do not clobber its authenticated state.
        current = store.get(record['id'])
        if current['state'] == 'starting': store.update(record['id'], state='unknown', error=f'Launch reply uncertain: {e}')
    return store.get(record['id'])


def reconcile(store, record):
    if record['state'] in ACTIVE and record.get('supervisor') and not alive(record['supervisor']):
        return store.update(record['id'], state='unknown', freshness='unknown', error='Supervisor unavailable; exit and remaining child ownership require inspection.')
    return record


def cancel(store, identity, force=False):
    r = reconcile(store, store.get(identity))
    if r['state'] not in ACTIVE: return r
    if not alive(r.get('supervisor')):
        raise ValueError('Supervisor identity is unavailable. Inspect this run; refusing to signal a saved PID.')
    if force and (not r.get('cancel_at') or time.time() - r['cancel_at'] < 10):
        raise ValueError('Request graceful cancellation and wait ten seconds before force-stop.')
    return store.update(identity, cancel=True, force=force, cancel_at=r.get('cancel_at') or time.time())


def restart(store, host, root, identity, reviewed, expected_session, request, timeout=15):
    """One reviewed restart: stop, confirm exit, then start the reviewed definition."""
    old = store.get(identity)
    if old['root'] != root or old['definition']['kind'] != 'service':
        raise ValueError('Restart requires a service in the selected checkout.')
    definition = store.definition(root, old['definition']['id'])
    if definition != validate_definition(reviewed) or session_identity(host) != expected_session:
        raise ValueError('Definition or session changed; review the restart again.')
    if old['session'] != expected_session:
        raise ValueError('Service belongs to a different Luvus session.')
    try:
        prior = store.by_request(request)
    except ValueError: prior = None
    if prior:
        return launch(store, host, root, definition['id'], request, reviewed, expected_session)
    if old['state'] == 'unknown': raise ValueError('Resolve interrupted tracking before restarting.')
    if old['state'] in ACTIVE: cancel(store, identity)
    deadline = time.monotonic() + timeout
    while True:
        old = reconcile(store, store.get(identity))
        if old['state'] not in ACTIVE: break
        if old['state'] == 'unknown' or time.monotonic() >= deadline:
            raise ValueError('Restart did not launch a replacement: stop is not confirmed. Inspect the run; force-stop is a separate action after ten seconds.')
        time.sleep(.1)
    if old['state'] == 'interrupted' or old.get('exit_code') is None:
        raise ValueError('Restart did not launch a replacement: the previous exit is unverified.')
    return launch(store, host, root, definition['id'], request, reviewed, expected_session)


def resolve_interrupted(store, identity):
    r = reconcile(store, store.get(identity))
    if r['state'] != 'unknown' or alive(r.get('supervisor')) or alive(r.get('child')):
        raise ValueError('Only an unknown run with no verified surviving supervisor/child can be acknowledged.')
    # Acknowledgement is explicit; it is not proof that daemonized descendants ended.
    return store.update(identity, state='interrupted', freshness='unknown', error='User acknowledged interrupted tracking; no process was stopped.')


def run_status(store, identity, check_source=True):
    r = reconcile(store, store.get(identity))
    if r.get('container_binding'): return r
    if check_source and r['state'] not in ACTIVE:
        try: definition = store.definition(r['root'], r['definition']['id'])
        except ValueError: definition = None
        current = snapshot(r['root'], definition) if definition else {'state': 'unknown'}
        if r.get('changed') or definition != r['definition']:
            r['freshness'] = 'stale'
        elif current['state'] != 'observed' or r.get('before', {}).get('state') != 'observed':
            r['freshness'] = 'unknown'
        else:
            r['freshness'] = 'current' if current['digest'] == r['before']['digest'] else 'stale'
        r['checked_at'] = time.time()
    return r


def supervise(store, identity, host=None):
    r = store.get(identity)
    # Atomic supervisor claim prevents duplicate terminal creation starting two children.
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        old = json.loads(db.execute('SELECT data FROM runs WHERE id=?', (identity,)).fetchone()['data'])
        if old.get('supervisor') or old['state'] not in ('starting', 'unknown'): return 125
        old['supervisor'] = process_identity(os.getpid())
        db.execute('UPDATE runs SET data=? WHERE id=?', (json.dumps(old), identity))
    d, child, job = r['definition'], None, None
    interrupted = threading.Event()
    def interruption(*_): interrupted.set()
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGHUP', signal.SIGTERM)):
        signal.signal(sig, interruption)
    log_path = store.logs / (identity + '.log')
    q = queue.Queue(maxsize=64)
    before = snapshot(r['root'], d)
    total = 0
    changed = False
    try:
        if host:
            locator = host.locator(os.environ['LUVUS_PANE_ID'])
            if locator['server_generation'] != r['session']['generation'] or os.environ.get('LUVUS_SOCKET_PATH') != r['session']['socket']:
                raise ValueError('Session changed before supervisor launch.')
            host.validate(locator)
            store.update(identity, locator=locator)
        conflict_ports(d)
        args = executable_argv([a.replace('{run_dir}', str(store.logs / identity)) for a in command_argv(d)])
        cwd = command_cwd(r['root'], d)
        (store.logs / identity).mkdir(mode=0o700, exist_ok=True)
        exit_path = store.logs / identity / 'exit.json'
        if os.name == 'nt':
            job = WindowsJob()
            child = subprocess.Popen(entry_args('gated-child', '--cwd', cwd, '--result', exit_path, '--', *args), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                job.assign(child)
                child.stdin.write(b'G'); child.stdin.flush(); child.stdin.close()
            except BaseException:
                child.kill(); child.wait(); raise
        else:
            child = subprocess.Popen(entry_args('posix-child', '--cwd', cwd, '--result', exit_path, '--', *args), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        store.update(identity, state='running', child=process_identity(child.pid), before=before)
        def reader():
            try:
                while True:
                    data = os.read(child.stdout.fileno(), 8192)
                    if not data: break
                    q.put(data)
            finally: q.put(None)
        threading.Thread(target=reader, daemon=True).start()
        next_check = time.monotonic() + 5
        stopping = False
        eof = False
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        with log_path.open('w+b') as log:
            if os.name != 'nt': os.chmod(log_path, 0o600)
            while not eof or child.poll() is None:
                try:
                    data = q.get(timeout=.15)
                    if data is None: eof = True
                    else:
                        total += len(data)
                        if d['kind'] == 'service':
                            if log.tell() + len(data) > LOG_LIMIT:
                                log.seek(max(0, log.tell() - LOG_LIMIT // 2))
                                recent = log.read()
                                log.seek(0); log.truncate(); log.write((recent + data)[-LOG_LIMIT:])
                            else: log.write(data)
                        else: log.write(data[:max(0, LOG_LIMIT - log.tell())])
                        log.flush()
                        from .diagnostics import safe_text
                        try: print(safe_text(decoder.decode(data)), end='', flush=True)
                        except (BrokenPipeError, OSError): interrupted.set()
                except queue.Empty: pass
                state = store.get(identity)
                if (state.get('cancel') or interrupted.is_set()) and not stopping and child.poll() is None:
                    stopping = True
                    store.update(identity, state='cancelling', cancel=True, cancel_at=state.get('cancel_at') or time.time())
                    if os.name == 'nt': child.send_signal(signal.CTRL_BREAK_EVENT)
                    else: os.killpg(child.pid, signal.SIGTERM)
                if child.poll() is None and stopping and (state.get('force') or (interrupted.is_set() and time.time() - state.get('cancel_at', time.time()) > 10)):
                    if job: job.stop()
                    else: os.killpg(child.pid, signal.SIGKILL)
                if time.monotonic() >= next_check:
                    observed = snapshot(r['root'], d)
                    changed |= before.get('digest') != observed.get('digest') or observed['state'] != 'observed'
                    store.update(identity, changed=changed, readiness=readiness(d, state.get('child')), truncated=total > LOG_LIMIT)
                    next_check = time.monotonic() + 5
                # A child which escaped the owned group can hold a pipe open. Never wait forever.
                command_ended = child.poll() is not None or (os.name != 'nt' and exit_path.exists())
                if command_ended and eof: break
                if command_ended and not eof and not hasattr(child, '_ended_at'): child._ended_at = time.monotonic()
                if command_ended and not eof and time.monotonic() - child._ended_at > 2:
                    changed = True
                    break
        outcome = json.loads(exit_path.read_text()) if exit_path.exists() else {}
        code = outcome.get('exit_code', child.poll())
        if 'spawn_error' in outcome:
            store.update(identity, state='failed', error=outcome['spawn_error'], freshness='unknown', finished=time.time())
            return 125
        if code is None: raise ValueError('Command exit could not be confirmed.')
        after = snapshot(r['root'], d)
        changed |= before.get('digest') != after.get('digest')
        state = 'cancelled' if stopping else ('passed' if code == 0 else 'failed')
        if not eof:
            state = 'interrupted'
            store.update(identity, error='Output remained open after the command exited; descendant tracking is incomplete.')
        # A negative POSIX code is an observed interruption, never ordinary success.
        if code < 0 and not stopping: state = 'interrupted'
        from .diagnostics import parse_run
        parsed = parse_run(store, {**r, 'truncated': total > LOG_LIMIT})
        store.update(identity, state=state, exit_code=code, before=before, after=after, changed=changed,
                     freshness='stale' if changed else 'observed' if before['state'] == 'observed' else 'unknown',
                     truncated=total > LOG_LIMIT, problems=parsed, readiness='stopped', finished=time.time())
        print(f'\n{state}; exit={code}; source={"stale" if changed else before["state"]}', flush=True)
        store.prune()
        return code
    except BaseException as e:
        store.update(identity, state='interrupted' if child else 'failed', freshness='unknown', error=str(e), finished=time.time())
        return 125
    finally:
        if job: job.close()
        elif child and child.poll() is None:
            # The process group belongs to this live supervisor, never a stale stored PID.
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        if child:
            if child.stdin and not child.stdin.closed: child.stdin.close()
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: pass
