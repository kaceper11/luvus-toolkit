import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from project_commands.model import Store, snapshot, LOG_LIMIT
from project_commands import runtime, diagnostics as diag
from project_commands.cli import api
from project_commands.ui import Console, Preview

ENTRY = Path(__file__).resolve().parents[1] / 'launcher.py'


class CommandsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve(); self.root = self.base / 'repo'; self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'source.txt').write_text('first\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        self.store = Store(self.base / 'state'); self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None: child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
            if child.stderr: child.stderr.close()
        self.temp.cleanup()

    def definition(self, code='print("ok")', **fields):
        return {'id': 'test', 'name': 'Test', 'argv': [sys.executable, '-c', code], 'kind': 'command', 'category': 'test', **fields}

    def configure(self, d):
        old = self.store.config(); new = copy.deepcopy(old)
        new['projects'][str(self.root)] = {'commands': [d]}
        self.store.save_config(new, old)

    def start(self, d):
        self.configure(d)
        r, _ = self.store.reserve(str(self.root), d, uuid.uuid4().hex, {})
        env = dict(os.environ)
        for key in ('LUVUS_BIN_PATH', 'LUVUS_MODULE_CONFIG_DIR'): env.pop(key, None)
        child = subprocess.Popen([sys.executable, str(ENTRY), 'supervise', '--store', str(self.store.root), '--run', r['id']], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
        self.children.append(child)
        return r, child

    def until(self, fn, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = fn()
            if value: return value
            time.sleep(.05)
        self.fail('condition timed out')

    def test_evidence_api_binds_request_and_redacts_bounded_output(self):
        d = self.definition(); self.configure(d)
        r, _ = self.store.reserve(str(self.root), d, 'evidence-request', {})
        self.store.update(r['id'], state='failed', error='fixture super-secret-value')
        log = self.store.logs / (r['id'] + '.log')
        log.write_text('super-secret-value ' + 'x' * 17000 + '\nFAIL final assertion super-secret-value')
        with patch.dict(os.environ, {'EXAMPLE_SECRET': 'super-secret-value'}), patch.object(runtime, 'run_status', return_value=self.store.get(r['id'])):
            value = api(self.store, {'version': 1, 'action': 'evidence', 'root': str(self.root), 'request_id': 'evidence-request'})
        self.assertTrue(value['excerpt_truncated'])
        self.assertNotIn('super-secret-value', value['output_excerpt'])
        self.assertNotIn('super-secret-value', value['diagnostic_excerpt'])
        self.assertEqual(value['request'], 'evidence-request')
        self.assertIn('FAIL final assertion', value['output_excerpt'])
        self.assertIn('middle omitted', value['output_excerpt'])
        self.assertLess(len(value['output_excerpt']), 16100)
        with self.assertRaises(ValueError):
            api(self.store, {'version': 1, 'action': 'evidence', 'root': str(self.base), 'request_id': 'evidence-request'})

    def test_exits_and_dirty_freshness(self):
        r, child = self.start(self.definition())
        self.assertEqual(child.wait(timeout=10), 0, child.stderr.read().decode())
        self.assertEqual(runtime.run_status(self.store, r['id'])['freshness'], 'current')
        (self.root / 'source.txt').write_text('dirty')
        self.assertEqual(runtime.run_status(self.store, r['id'])['freshness'], 'stale')
        r, child = self.start(self.definition('raise SystemExit(7)'))
        child.wait(timeout=10)
        self.assertEqual(self.store.get(r['id'])['state'], 'failed')
        self.assertEqual(self.store.get(r['id'])['exit_code'], 7)

    def test_untracked_and_unknown_inputs(self):
        d = self.definition(); before = snapshot(str(self.root), d)
        (self.root / 'new.txt').write_text('untracked')
        self.assertNotEqual(before['digest'], snapshot(str(self.root), d)['digest'])
        self.assertEqual(snapshot(str(self.root), {**d, 'inputs': ['../absent']})['state'], 'unknown')

    def test_configuration_conflict_and_validation(self):
        old = self.store.config(); self.configure(self.definition())
        with self.assertRaises(ValueError): self.store.save_config(old, old)
        with self.assertRaises(ValueError): self.store.reserve(str(self.root), self.definition(cwd='../'), 'x', {})

    def test_duplicate_and_cross_worktree_guard(self):
        d = self.definition(kind='service', exclusive_group='stack')
        a, created = self.store.reserve(str(self.root), d, 'one', {})
        b, created2 = self.store.reserve(str(self.root), d, 'two', {})
        self.assertTrue(created); self.assertFalse(created2); self.assertEqual(a['id'], b['id'])
        second = self.base / 'second'; second.mkdir()
        with self.assertRaises(ValueError): self.store.reserve(str(second), d, 'three', {})
        with self.assertRaises(ValueError): self.store.reserve(str(self.root), self.definition('print("different")'), 'four', {})

    def test_two_independent_worktrees(self):
        a, _ = self.store.reserve(str(self.root), self.definition(kind='service'), 'one', {})
        second = self.base / 'second'; second.mkdir()
        b, created = self.store.reserve(str(second), self.definition(kind='service'), 'two', {})
        self.assertTrue(created); self.assertNotEqual(a['id'], b['id'])

    def test_cancel_owned_child_leaves_unrelated_alive(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.children.append(unrelated)
        r, child = self.start(self.definition('import time; time.sleep(30)', kind='service'))
        self.until(lambda: self.store.get(r['id'])['state'] == 'running')
        runtime.cancel(self.store, r['id'])
        child.wait(timeout=10)
        self.assertEqual(self.store.get(r['id'])['state'], 'cancelled')
        self.assertIsNone(unrelated.poll())

    def test_spawn_failure_is_not_success(self):
        r, child = self.start(self.definition(argv=['/definitely/missing/executable']))
        child.wait(timeout=10)
        self.assertEqual(self.store.get(r['id'])['state'], 'failed')
        self.assertIn('error', self.store.get(r['id']))

    def test_unknown_never_signals_a_saved_pid(self):
        r, _ = self.store.reserve(str(self.root), self.definition(), 'unknown', {})
        self.store.update(r['id'], state='unknown', supervisor={'pid': os.getpid(), 'created': 0})
        with self.assertRaises(ValueError): runtime.cancel(self.store, r['id'])
        self.assertEqual(runtime.resolve_interrupted(self.store, r['id'])['state'], 'interrupted')

    def test_port_conflict_does_not_stop_occupant(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); sock.listen()
            with self.assertRaises(ValueError): runtime.conflict_ports({'ports': [sock.getsockname()[1]]})
            with socket.create_connection(sock.getsockname(), timeout=1): pass

    def test_early_force_rejected(self):
        r, child = self.start(self.definition('import time; time.sleep(30)', kind='service'))
        self.until(lambda: self.store.get(r['id'])['state'] == 'running')
        with self.assertRaises(ValueError): runtime.cancel(self.store, r['id'], True)
        runtime.cancel(self.store, r['id']); child.wait(timeout=10)

    def test_report_parsers_and_safe_paths(self):
        r, _ = self.store.reserve(str(self.root), self.definition(), 'parse', {})
        (self.store.logs / (r['id'] + '.log')).write_text('source.txt(1,2): error CS123: bad\nunknown failed output\n')
        result = diag.parse_run(self.store, r)
        self.assertEqual(result['state'], 'partial'); self.assertEqual(result['items'][0]['line'], 1)
        self.assertEqual(diag.source_location(r, result['items'][0])[0], self.root / 'source.txt')
        with self.assertRaises(ValueError): diag.source_location(r, {'file': '../state/runs.sqlite3', 'line': 1})
        folder = self.store.logs / r['id']; folder.mkdir()
        for kind, data, expected in [('eslint', [{'filePath': str(self.root / 'source.txt'), 'messages': [{'line': 1, 'column': 2, 'severity': 2, 'message': 'bad'}]}], 2), ('vitest', {'testResults': [{'name': str(self.root / 'source.txt'), 'assertionResults': [{'status': 'failed', 'fullName': 'test', 'failureMessages': ['bad']}]}]}, 2)]:
            (folder / 'report.json').write_text(json.dumps(data))
            r['definition'].update(report_format=kind, report='report.json')
            parsed = diag.parse_run(self.store, r); self.assertEqual(parsed['state'], 'partial'); self.assertEqual(len(parsed['items']), expected)
        (folder / 'report.json').write_text('broken')
        self.assertEqual(diag.parse_run(self.store, r)['state'], 'parser-error')
        r['definition'].update(report_format='trx', report='report.trx')
        (folder / 'report.trx').write_text('<TestRun><Results><UnitTestResult testName="a" outcome="Failed"><Message>bad</Message></UnitTestResult></Results></TestRun>')
        self.assertEqual(diag.parse_run(self.store, r)['state'], 'partial')
        (folder / 'report.trx').write_text('<!DOCTYPE x><TestRun/>')
        self.assertEqual(diag.parse_run(self.store, r)['state'], 'parser-error')

    def test_no_execution_from_discovery(self):
        (self.root / 'package.json').write_text(json.dumps({'scripts': {'test': 'touch /never-execute', 'generate': 'node ${ROOT:-../platform}/cli.mjs'}}))
        suggestions = diag.discover(self.root)
        self.assertTrue(any(d['argv'][-1] == 'test' for d in suggestions))
        self.assertTrue(any('windows' in d.get('unsupported_platforms', []) for d in suggestions))
        self.assertEqual(self.store.project(self.root).get('commands'), [])

    def test_api_targets_and_cli_arguments(self):
        r, _ = self.store.reserve(str(self.root), self.definition(), 'api', {})
        second = self.base / 'second'; second.mkdir()
        with self.assertRaises(ValueError): api(self.store, {'version': 1, 'action': 'status', 'root': str(second), 'run': r['id']})
        env = dict(os.environ); env.pop('LUVUS_BIN_PATH', None)
        result = subprocess.run([sys.executable, str(ENTRY), 'api', '--store', str(self.store.root)], input=json.dumps({'version': 1, 'action': 'status', 'root': str(self.root), 'run': r['id']}), text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result']['id'], r['id'])

    def test_container_pin_and_owner(self):
        binding = {'id': 'a' * 64, 'created': 'one', 'daemon': 'daemon', 'context': 'default', 'owner': 'aspire'}
        with patch.object(diag, 'inspect_container', return_value=binding):
            with self.assertRaises(ValueError): diag.container_action(binding, 'stop')
        with patch.object(diag, 'inspect_container', return_value={**binding, 'daemon': 'changed'}):
            with self.assertRaises(ValueError): diag.container_action(binding, 'logs')

    def test_unsafe_output_is_plain_text(self):
        self.assertEqual(diag.safe_text('\x1b[31mhello\x00\x07'), 'hello')
        self.assertEqual(diag.detected_urls('http://user:pass@localhost/a https://localhost/x?token=secret http://localhost:3000'), ['http://localhost:3000'])

    def test_retention_preserves_active(self):
        r, _ = self.store.reserve(str(self.root), self.definition(), 'retain', {})
        self.store.update(r['id'], created=0)
        self.store.prune(); self.assertEqual(self.store.get(r['id'])['state'], 'starting')
        with self.store.db() as db: db.execute('UPDATE runs SET created=0 WHERE id=?', (r['id'],))
        self.store.update(r['id'], state='failed'); self.store.prune()
        with self.assertRaises(ValueError): self.store.get(r['id'])


class UITests(unittest.IsolatedAsyncioTestCase):
    async def test_narrow_console_modal_and_no_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'state')
            app = Console(store, str(Path(temp).resolve()))
            async with app.run_test(size=(85, 30)) as pilot:
                await pilot.pause()
                self.assertEqual(store.list(), [])
                app.push_screen(Preview('Preview', '$(touch bad)\n[red]literal', 'Confirm', editable=True))
                await pilot.pause(); await pilot.press('escape'); await pilot.pause()
                self.assertEqual(len(app.screen_stack), 1)
                app.handle('discover'); await pilot.pause(.4)
                self.assertEqual(store.list(), [])



    async def test_refresh_preserves_selected_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Console(Store(Path(temp) / 'state'), str(Path(temp).resolve()))
            async with app.run_test() as pilot:
                app.table('commands', [{'id': 'old'}], [('Old', 'command', 'test', 'not-run')])
                app.table('commands', [{'id': 'new'}, {'id': 'old'}], [('New', 'command', 'test', 'not-run'), ('Old', 'command', 'test', 'not-run')])
                self.assertEqual(app.selected('commands')['id'], 'old')


class RegressionTests(unittest.TestCase):
    setUp = CommandsTests.setUp
    tearDown = CommandsTests.tearDown
    definition = CommandsTests.definition
    configure = CommandsTests.configure
    start = CommandsTests.start
    until = CommandsTests.until
    # Additional fault cases use the same disposable fixture and runner.
    def test_uncertain_launch_is_not_retried(self):
        self.configure(self.definition())
        class Host:
            binary = 'fixture'; creates = 0
            def inventory(self): return {'server_generation': 'fixture'}
            def create(self, *a):
                self.creates += 1
                raise subprocess.TimeoutExpired('fixture', 1)
        host = Host()
        first = runtime.launch(self.store, host, str(self.root), 'test', 'stable-id')
        second = runtime.launch(self.store, host, str(self.root), 'test', 'stable-id')
        self.assertEqual(first['id'], second['id']); self.assertEqual(first['state'], 'unknown'); self.assertEqual(host.creates, 1)

    def test_failure_freshness_changes_after_edit(self):
        r, child = self.start(self.definition('raise SystemExit(1)'))
        child.wait(timeout=10)
        (self.root / 'source.txt').write_text('changed')
        self.assertEqual(runtime.run_status(self.store, r['id'])['freshness'], 'stale')

    def test_collector_output_is_bounded(self):
        with self.assertRaises(ValueError):
            diag.capture([sys.executable, '-c', 'print("x" * 200000)'], limit=1024)
        p = diag.capture([sys.executable, '-c', 'import sys; print(sys.stdin.read())'], input='{"literal":"$(no-command)"}')
        self.assertIn('$(no-command)', p.stdout)

    def test_report_truncation_does_not_claim_clean(self):
        r, _ = self.store.reserve(str(self.root), self.definition(), 'truncated', {})
        r['truncated'] = True
        self.assertTrue(diag.parse_run(self.store, r)['truncated'])

    def test_other_module_config_environment_is_not_used(self):
        from project_commands.model import MODULE
        with patch.dict(os.environ, {'LUVUS_HOME': str(self.base / 'home'), 'LUVUS_MODULE_ID': 'other.module', 'LUVUS_MODULE_CONFIG_DIR': str(self.base / 'other')}):
            s = Store()
            self.assertEqual(s.root, self.base / 'home/modules/config/kacper.toolkit/features/project-commands')
            self.assertFalse((self.base / 'other').exists())

    def test_readiness_does_not_accept_unrelated_listener(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); sock.listen()
            d = {'readiness': {'kind': 'tcp', 'port': sock.getsockname()[1]}}
            self.assertEqual(runtime.readiness(d, {'pid': os.getpid(), 'created': 0}), 'unknown')

    def test_native_validate_rejects_non_alive_result(self):
        from project_commands.host import Host
        h = Host('unused')
        with patch.object(h, 'call', return_value={'state': 'gone'}):
            with self.assertRaises(ValueError): h.validate({})

    def test_windows_corepack_uses_node_not_batch_shell(self):
        from project_commands.model import executable_argv
        shim = self.base / 'corepack.cmd'; shim.touch()
        script = self.base / 'node_modules/corepack/dist/corepack.js'
        script.parent.mkdir(parents=True); script.touch()
        def which(name): return str(shim) if name == 'corepack' else 'node.exe' if name == 'node' else None
        with patch('shutil.which', side_effect=which):
            result = executable_argv(['corepack', 'pnpm', 'run', 'literal&argument'], windows=True)
            self.assertEqual(result, ['node.exe', str(script), 'pnpm', 'run', 'literal&argument'])
        with self.assertRaises(ValueError): executable_argv([str(self.base / 'unknown.cmd')], windows=True)

    def test_reviewed_command_and_session_cannot_change(self):
        d = self.definition(); self.configure(d)
        class Host:
            def inventory(self): return {'server_generation': 'new'}
        with self.assertRaises(ValueError): runtime.launch(self.store, Host(), str(self.root), 'test', reviewed=self.definition('print("changed")'))
        with self.assertRaises(ValueError): runtime.launch(self.store, Host(), str(self.root), 'test', reviewed=d, expected_session={'socket': 'old', 'generation': 'old'})
        self.assertEqual(self.store.list(), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX ignored-signal regression; Windows uses a Job Object')
    def test_force_stop_owned_group(self):
        r, child = self.start(self.definition('import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready",flush=True); time.sleep(60)', kind='service'))
        path = self.store.logs / (r['id'] + '.log')
        self.until(lambda: path.exists() and 'ready' in path.read_text())
        runtime.cancel(self.store, r['id'])
        self.until(lambda: self.store.get(r['id'])['state'] == 'cancelling')
        self.store.update(r['id'], cancel_at=time.time() - 11)
        runtime.cancel(self.store, r['id'], force=True)
        child.wait(timeout=8)
        self.assertEqual(self.store.get(r['id'])['state'], 'cancelled')

    def test_container_failure_output_is_retained(self):
        binding = {'id': 'b' * 64, 'context': 'default', 'owner': 'manual'}
        with patch.object(diag, 'container_action', side_effect=ValueError('daemon rejected request')):
            with self.assertRaises(ValueError): diag.recorded_container_action(self.store, str(self.root), binding, 'stop')
        run = self.store.list()[0]
        self.assertEqual(run['state'], 'unknown')
        self.assertIn('daemon rejected', (self.store.logs / (run['id'] + '.log')).read_text())

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership-pipe regression')
    def test_supervisor_crash_closes_owned_group(self):
        r, child = self.start(self.definition('import time; print("ready",flush=True); time.sleep(60)', kind='service'))
        path = self.store.logs / (r['id'] + '.log')
        self.until(lambda: path.exists() and 'ready' in path.read_text())
        identity = self.store.get(r['id'])['child']
        child.kill(); child.wait(timeout=5)
        self.until(lambda: not runtime.alive(identity))
        self.assertEqual(runtime.run_status(self.store, r['id'])['state'], 'unknown')


if __name__ == '__main__': unittest.main()
