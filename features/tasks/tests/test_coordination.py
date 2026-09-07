import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from luvus_tasks import checkout, forge, operations as ops, review
from luvus_tasks.core import Store, TaskError
from luvus_tasks.handover import git, launch, save_record
import test_tasks


class OrchDisplayTests(unittest.TestCase):
    def test_native_orch_has_no_duplicate_module_projection(self):
        from luvus_tasks.orch import show, try_show
        store, host = Mock(), Mock()
        show(store, host, reveal=True)
        self.assertEqual(try_show(store, host), '')
        host.call.assert_not_called()


class CoordinationTests(unittest.TestCase):
    setUp = test_tasks.GitTests.setUp
    tearDown = test_tasks.GitTests.tearDown
    record = test_tasks.GitTests.record

    def host(self, record):
        task = {'id': 't1', 'title': 'test', 'status': 'pending', 'assignee': None, 'paths': [], 'deps': []}
        host = Mock()
        host.agents.return_value = []
        host.capabilities.return_value = {'method_contracts': [{'method': m} for m in ('task.list', 'lease.list', 'task.add', 'task.claim', 'task.done')]}
        def call(method, **params):
            if method == 'terminal.backend.create':
                return {'pane_id': '4', 'terminal_id': 'terminal-unique', 'server_generation': 'generation'}
            if method == 'agent.start':
                host.agents.return_value = [{'pane': '4', 'terminal_id': 'terminal-unique', 'generation': 'generation', 'name': record['name'], 'agent': 'codex', 'cwd': record['target']['path']}]
                return {'ready': True}
            if method == 'task.list':
                return {'tasks': [task]}
            if method == 'lease.list':
                return {'leases': []}
            if method == 'task.claim':
                task.update(assignee=params['pane'], status='running')
                return {'task': task}
            if method in ('task.add', 'task.get'):
                return {'task': copy.deepcopy(task)}
            return {}
        host.call.side_effect = call
        return host

    def enable(self):
        config = self.store.config()
        config['orchestration'] = True
        self.store.save_config(config)

    def test_claim_before_prompt_and_retry_reuses_native_task(self):
        self.enable()
        record = self.record()
        host = self.host(record)
        with patch('luvus_tasks.orch.try_show'):
            launch(self.store, host, record)
            launch(self.store, host, record)
        methods = [c.args[0] for c in host.call.call_args_list]
        self.assertLess(methods.index('task.claim'), methods.index('agent.prompt'))
        self.assertEqual(methods.count('task.add'), 1)
        self.assertEqual(methods.count('agent.prompt'), 1)
        self.assertEqual(record['orch_id'], 't1')

    def test_conflict_stops_delivery_and_retry_does_not_recreate(self):
        self.enable()
        record = self.record()
        host = self.host(record)
        original = host.call.side_effect
        def fail(method, **params):
            if method == 'task.claim':
                raise TaskError('Dependency not ready')
            return original(method, **params)
        host.call.side_effect = fail
        with patch('luvus_tasks.orch.try_show'):
            for _ in range(2):
                with self.assertRaisesRegex(TaskError, 'Dependency'):
                    launch(self.store, host, record)
        methods = [c.args[0] for c in host.call.call_args_list]
        self.assertNotIn('agent.prompt', methods)
        self.assertEqual(methods.count('task.add'), 1)
        self.assertEqual(record['stage'], 'agent_ready')

    def test_display_failure_does_not_undo_delivery(self):
        self.enable()
        record = self.record()
        host = self.host(record)
        with patch('luvus_tasks.orch.show', side_effect=TaskError('display unavailable')):
            launch(self.store, host, record)
        self.assertEqual(record['stage'], 'delivered')
        self.assertIsNone(self.store.preference('orch-display-error'))

    def test_branch_change_blocks_followup_even_with_same_worker(self):
        record = self.record()
        host = self.host(record)
        launch(self.store, host, record)
        git(record['target']['path'], 'switch', '-c', 'wrong')
        before = host.call.call_count
        with self.assertRaisesRegex(TaskError, 'branch changed'):
            ops.followup(self.store, host, record, 'Continue')
        self.assertEqual(host.call.call_count, before)

    def test_local_checkout_badge_and_guard(self):
        record = self.record()
        record['target'].update(path=str(self.repo.resolve()), branch='main')
        first = checkout.snapshot(self.repo)
        record['checkout_identity'] = checkout.identity(first)
        (self.repo / 'extra').write_text('change')
        self.assertTrue(checkout.guard(record)['dirty'])
        self.assertIn('no upstream', checkout.badge(first))
        git(self.repo, 'switch', '-c', 'changed')
        with self.assertRaises(TaskError):
            checkout.guard(record)

    def test_remembered_connection_requires_same_account_and_scope(self):
        record = self.record()
        links = [{'kind': 'github', 'repository': 'owner/repo', 'connection': x} for x in ('a', 'b')]
        config = self.store.config()
        config['connections'] = [{'id': x, 'provider': 'github', 'repository': 'owner/repo', 'account': 'alice'} for x in ('a', 'b')]
        self.store.save_config(config)
        with patch.object(forge, 'choices', return_value=links), patch.object(forge, 'remote_identity', return_value={'kind': 'github', 'repository': 'owner/repo'}):
            self.assertIsNone(forge.preferred(self.store, record))
            forge.remember(self.store, record, links[1])
            self.assertEqual(forge.preferred(self.store, record), links[1])
            config['connections'][1]['account'] = 'bob'
            self.store.save_config(config)
            self.assertIsNone(forge.preferred(self.store, record))

    def test_review_claim_metadata_precedes_worker_and_parent_preserves_running_state(self):
        self.enable()
        record = self.record()
        save_record(self.store, record)
        binary = self.root / 'fake-reviewer'
        binary.write_text('test')
        item = self.store.observe('review:test', record['id'], {'kind': 'review', 'title': 'Review', 'state': 'prepared', 'folder': str(self.root)})
        host = self.host(record)
        original = host.call.side_effect
        def call(method, **params):
            if method == 'terminal.backend.create':
                current = self.store.evidence(record['id'])[0]
                self.assertEqual(current['orch_id'], 't1')
                self.store.observe(current['id'], record['id'], {**current, 'state': 'running'})
            return original(method, **params)
        host.call.side_effect = call
        with patch.object(review, 'capability', return_value=str(binary)), patch('luvus_tasks.orch.try_show'):
            saved = review.start(self.store, host, item)
        self.assertEqual(saved['state'], 'running')
        self.assertEqual(saved['orch_id'], 't1')

    def test_review_wrapper_claims_before_exec_and_strips_control_environment(self):
        import os
        import hashlib
        record = self.record()
        save_record(self.store, record)
        binary = self.root / 'review-bin'
        binary.write_text('test')
        item = self.store.observe('review:wrapper', record['id'], {'kind': 'review', 'title': 'Review', 'state': 'pending',
            'folder': str(self.root), 'orch_enabled': True, 'orch_id': 't1', 'binary_hash': hashlib.sha256(binary.read_bytes()).hexdigest()})
        host = Mock()
        calls = []
        def call(method, **params):
            calls.append(method)
            if method == 'terminal.backend.inventory':
                return {'server_generation': 'g', 'terminals': [{'pane_id': '4', 'terminal_id': 't', 'cwd': str(self.root)}]}
            if method == 'task.get':
                return {'task': {'assignee': None, 'status': 'queued'}}
            return {}
        host.call.side_effect = call
        def execute(argv, **kwargs):
            self.assertIn('task.claim', calls)
            self.assertIn('--sandbox', argv)
            self.assertIn('read-only', argv)
            self.assertFalse(any(k.startswith('LUVUS_') for k in kwargs['env']))
            self.assertNotIn('GH_TOKEN', kwargs['env'])
            return Mock(returncode=0)
        with patch.object(review, 'capability', return_value=str(binary)), patch('luvus_tasks.handover.Luvus', return_value=host), \
             patch.object(review.subprocess, 'run', side_effect=execute), patch.dict(os.environ, {'LUVUS_PANE_ID': '4', 'GH_TOKEN': 'test-only'}):
            self.assertEqual(review.worker(self.store.root, item['id']), 0)
        self.assertEqual(calls[-1], 'task.done')
        saved = next(e for e in self.store.evidence(record['id']) if e['id'] == item['id'])
        self.assertEqual(saved['state'], 'passed')
        self.assertFalse(saved['orch_completion_pending'])
