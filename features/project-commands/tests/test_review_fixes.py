"""Regressions from the independent correctness and UX review."""
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_commands as fixtures
from project_commands import diagnostics as diag, runtime
from project_commands.cli import api
from project_commands.model import Store
from project_commands.ui import Console, DiscoveryPicker, CommandForm, Preview
from textual.widgets import Button, DataTable, Input, TextArea, TabbedContent


class ReviewFixes(unittest.TestCase):
    setUp = fixtures.CommandsTests.setUp
    tearDown = fixtures.CommandsTests.tearDown
    definition = fixtures.CommandsTests.definition
    configure = fixtures.CommandsTests.configure
    start = fixtures.CommandsTests.start
    until = fixtures.CommandsTests.until

    def test_reused_request_is_durable_and_scoped(self):
        d = self.definition(); self.configure(d)
        first, _ = self.store.reserve(str(self.root), d, 'a', {})
        reused, created = self.store.reserve(str(self.root), d, 'b', {})
        self.assertFalse(created); self.assertEqual(reused['request'], 'b')
        self.store.update(first['id'], state='passed')
        restarted_store = Store(self.store.root)
        retried, created = restarted_store.reserve(str(self.root), d, 'b', {})
        self.assertFalse(created); self.assertEqual(retried['id'], first['id'])
        result = api(restarted_store, {'version': 1, 'action': 'status', 'root': str(self.root), 'request_id': 'b'})
        self.assertEqual(result['request'], 'b'); self.assertEqual(result['id'], first['id'])
        second = self.base / 'second'; second.mkdir()
        with self.assertRaises(ValueError):
            api(restarted_store, {'version': 1, 'action': 'status', 'root': str(second), 'request_id': 'b'})

    def test_removed_worktree_does_not_block_other_config(self):
        self.configure(self.definition()); previous = self.store.config()
        shutil.rmtree(self.root, onerror=lambda function, path, error: (os.chmod(path, 0o700), function(path)))
        second = self.base / 'second'; second.mkdir()
        new = copy.deepcopy(previous); new['projects'][str(second)] = {'commands': [self.definition()]}
        self.store.save_config(new, previous)
        self.assertIn(str(self.root), self.store.config()['projects'])
        with self.assertRaises(OSError): self.store.reserve(str(self.root), self.definition(), 'no', {})

    def test_text_limit_and_suite_level_failures(self):
        r, _ = self.store.reserve(str(self.root), self.definition(), 'parse', {})
        log = self.store.logs / (r['id'] + '.log')
        log.write_text('a.cs(1,1): error E1: bad\n' * 5001)
        parsed = diag.parse_run(self.store, r)
        self.assertEqual(len(parsed['items']), 5000); self.assertTrue(parsed['truncated']); self.assertEqual(parsed['state'], 'partial')
        folder = self.store.logs / r['id']; folder.mkdir(); log.write_text('')
        r['definition'].update(report_format='vitest', report='report.json')
        (folder / 'report.json').write_text(json.dumps({'success': False, 'numFailedTestSuites': 1, 'testResults': [{'name': 'test.ts', 'status': 'failed', 'message': 'Cannot import dependency', 'assertionResults': []}]}))
        parsed = diag.parse_run(self.store, r)
        self.assertEqual(parsed['items'][0]['message'], 'Cannot import dependency')
        (folder / 'report.json').write_text(json.dumps({'success': False, 'numFailedTestSuites': 1, 'testResults': []}))
        parsed = diag.parse_run(self.store, r)
        self.assertEqual(parsed['state'], 'partial'); self.assertEqual(len(parsed['items']), 1)

    def test_npm_web_command_uses_web_directory(self):
        (self.root / 'web').mkdir()
        (self.root / 'web/package.json').write_text(json.dumps({'scripts': {'test': 'echo WEB_SCRIPT'}, 'packageManager': 'npm@10'}))
        d = next(d for d in diag.discover(self.root) if d['id'] == 'web:script:test')
        self.assertEqual(d['cwd'], 'web'); self.assertEqual(d['argv'], ['npm', 'run', 'test'])
        if shutil.which('npm'):
            from project_commands.model import executable_argv
            result = subprocess.run(executable_argv(d['argv']), cwd=self.root / d['cwd'], env={**os.environ, 'npm_config_cache': str(self.base / 'npm-cache'), 'npm_config_update_notifier': 'false'}, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr); self.assertIn('WEB_SCRIPT', result.stdout)

    def test_health_only_checks_relevant_tools_and_configured_executables(self):
        self.configure(self.definition(argv=['missing-project-executable']))
        (self.root / 'App.slnx').write_text('<Solution/>')
        with patch.object(diag.shutil, 'which', side_effect=lambda n: None if n == 'missing-project-executable' else '/mock/' + n), patch.object(diag, 'capture', return_value=subprocess.CompletedProcess([], 0, '10.0.302 [sdk]\n', '')) as capture:
            checks = diag.health(self.store, str(self.root))
        states = {c['name']: c['state'] for c in checks}
        self.assertNotIn('node', states); self.assertNotIn('corepack', states)
        self.assertEqual(states['dotnet'], 'healthy'); self.assertEqual(states['missing-project-executable'], 'missing')
        self.assertFalse(any('missing-project-executable' in str(c) for c in capture.call_args_list))

    def test_duplicate_container_bindings_rejected(self):
        b = {'id': 'a'*64, 'created': 'one', 'daemon': 'daemon', 'context': 'default', 'owner': 'manual'}
        previous = self.store.config()
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.store.save_config({'version': 1, 'projects': {str(self.root): {'containers': [b, {**b, 'context': 'another-alias'}]}}}, previous)

    def test_restart_waits_for_actual_exit_before_launch(self):
        r, child = self.start(self.definition('import time; time.sleep(30)', kind='service'))
        self.until(lambda: self.store.get(r['id'])['state'] == 'running')
        class Host:
            def inventory(self): return {'server_generation': 'fixture'}
        host = Host(); session = runtime.session_identity(host)
        self.store.update(r['id'], session=session)
        def launched(*args, **kwargs):
            old = self.store.get(r['id'])
            self.assertEqual(old['state'], 'cancelled'); self.assertIsNotNone(old['exit_code'])
            return {'state': 'starting'}
        with patch.object(runtime, 'launch', side_effect=launched) as launch:
            runtime.restart(self.store, host, str(self.root), r['id'], r['definition'], session, 'restart')
            launch.assert_called_once()
        child.wait(timeout=5)

    def test_restart_does_not_start_on_unknown_or_timeout(self):
        d = self.definition(kind='service'); self.configure(d)
        class Host:
            def inventory(self): return {'server_generation': 'fixture'}
        host = Host(); session = runtime.session_identity(host)
        r, _ = self.store.reserve(str(self.root), d, 'first', session)
        self.store.update(r['id'], state='running', supervisor=runtime.process_identity(os.getpid()))
        with patch.object(runtime, 'launch') as launch:
            with self.assertRaisesRegex(ValueError, 'stop is not confirmed'):
                runtime.restart(self.store, host, str(self.root), r['id'], d, session, 'restart', timeout=0)
            launch.assert_not_called()


class ReviewUI(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_selection_reload_cancel_and_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp).resolve()); store = Store(Path(root)/'state'); app = Console(store, root)
            async with app.run_test(size=(80, 24)) as pilot:
                app.show_discovery([{'id': 'test', 'name': 'Tests', 'kind': 'command', 'category': 'test', 'argv': ['python', '-V'], 'source': 'package.json scripts.test'}])
                await pilot.pause()
                self.assertIsInstance(app.screen, DiscoveryPicker)
                self.assertTrue(app.screen.query_one('#suggestion-save', Button).disabled)
                app.screen.toggle(); await pilot.pause()
                self.assertFalse(app.screen.query_one('#suggestion-save', Button).disabled)
                await pilot.click('#suggestion-save'); await pilot.pause()
                self.assertIsInstance(app.screen, Preview)
                await pilot.click('#confirm'); await pilot.pause(.3)
                self.assertEqual(store.project(root)['commands'][0]['id'], 'test')
                area = app.query_one('#config-text', TextArea); draft = area.text + '\n'
                area.load_text(draft); app.handle('config-load'); await pilot.pause()
                self.assertIsInstance(app.screen, Preview)
                await pilot.press('escape'); await pilot.pause()
                self.assertEqual(area.text, draft)
                app.handle('config-load'); await pilot.pause(); await pilot.click('#confirm'); await pilot.pause()
                self.assertFalse(app.config_dirty())
                app.handle('command-add'); await pilot.pause()
                self.assertIsInstance(app.screen, CommandForm)
                await pilot.press('escape'); await pilot.pause()
                self.assertEqual(len(store.project(root)['commands']), 1)

    async def test_native_lookup_is_off_ui_thread_and_controls_follow_state(self):
        class Host:
            def inventory(self): time.sleep(.3); return {'server_generation': 'fixture'}
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp).resolve()); store = Store(Path(root)/'state'); app = Console(store, root, Host())
            d = {'id': 'test', 'name': 'Test', 'argv': ['python', '-V'], 'kind': 'command'}
            async with app.run_test(size=(80, 24)) as pilot:
                self.assertTrue(app.query_one('#cancel', Button).disabled)
                self.assertTrue(app.query_one('#force', Button).disabled)
                start = time.monotonic(); app.review_run(d)
                self.assertLess(time.monotonic()-start, .15); self.assertTrue(app.busy)
                await pilot.press('tab'); await pilot.pause(.4)
                self.assertIsInstance(app.screen, Preview)
                await pilot.press('escape'); await pilot.pause()
                self.assertFalse(app.busy)

    async def test_selected_freshness_and_container_unbind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp).resolve()); store = Store(Path(root)/'state')
            d = {'id': 'test', 'name': 'Test', 'argv': ['python', '-V'], 'kind': 'command'}
            b = {'id': 'a'*64, 'created': 'one', 'daemon': 'daemon', 'context': 'default', 'owner': 'aspire'}
            store.save_config({'version': 1, 'projects': {root: {'commands': [d], 'containers': [b]}}}, store.config())
            r, _ = store.reserve(root, d, 'first', {}); store.update(r['id'], state='passed')
            app = Console(store, root)
            async with app.run_test(size=(80, 24)) as pilot:
                self.assertFalse(app.query_one('#freshness', Button).disabled)
                self.assertTrue(app.query_one('#cancel', Button).disabled)
                self.assertTrue(app.query_one('#container-start', Button).disabled)
                app.handle('freshness'); await pilot.pause(.3)
                self.assertEqual(app.fresh_checks[r['id']]['freshness'], 'unknown')
                self.assertIn('as of', str(app.query_one('#runs', DataTable).get_cell_at((0, 2))))
                app.handle('container-unbind'); await pilot.pause()
                await pilot.click('#confirm'); await pilot.pause(.3)
                self.assertEqual(store.project(root)['containers'], [])
