"""Isolated lifecycle and provider contracts; never contact trackers or launch agents."""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from luvus_tasks import productivity as tasks, productivity_ui, operations as ops, attention, workflow
from luvus_tasks.core import Store, TaskError
from luvus_tasks.providers import Jira, Azure, GitHub, ProviderError
from luvus_tasks import capture_providers as adapters
from luvus_tasks.console import Cockpit
from test_console import TICKET, CONNECTION, Host


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        config = self.store.config()
        config['connections'] = [copy.deepcopy(CONNECTION)]
        self.store.save_config(config)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_queue_identity_order_and_cross_connection_transactions(self):
        a, b = TICKET, {**TICKET, 'project': 'Other'}
        tasks.queue(self.store, a)
        other = Store(self.temp.name)
        try:
            tasks.queue(other, b)
            tasks.queue(self.store, a)
            self.assertEqual(len(tasks.queue(other)), 2)
            tasks.queue(other, b, 'up')
            self.assertEqual(tasks.queue(self.store)[0]['project'], 'Other')
            tasks.queue(self.store, a, 'remove')
            self.assertEqual(len(tasks.queue(other)), 1)
        finally:
            other.db.close()

    def test_candidates_ignore_archived_and_other_project(self):
        record = ops.draft(self.store, TICKET)
        ops.draft(self.store, {**TICKET, 'project': 'Other'})
        self.assertEqual([r['id'] for r in tasks.candidates(self.store, TICKET)], [record['id']])
        from luvus_tasks.handover import save_record
        record['archived_at'] = 'now'
        save_record(self.store, record)
        self.assertEqual(tasks.candidates(self.store, TICKET), [])

    def test_archive_restore_draft_preserves_history_and_blocks_pending(self):
        record = ops.draft(self.store, TICKET)
        host = Mock()
        host.agents.return_value = []
        saved = tasks.archive(self.store, host, record['id'])
        self.assertTrue(saved['archived_at'])
        self.assertEqual(len(self.store.records('handovers')), 1)
        saved = tasks.archive(self.store, host, record['id'], restore=True)
        self.assertNotIn('archived_at', saved)
        self.store.put('writes', 'pending', {'state': 'pending'}, ticket='azure:1')
        with self.assertRaises(TaskError):
            tasks.archive(self.store, host, record['id'])

    def test_archived_targets_do_not_poll(self):
        r = ops.draft(self.store, TICKET)
        r.update(archived_at='now', target={'path': self.temp.name, 'branch': 'feature'})
        self.store.put('handovers', r['id'], r, 'azure:1')
        with patch('luvus_tasks.workflow.refresh_pr') as refresh:
            self.assertEqual(workflow.poll_prs(self.store), 0)
        refresh.assert_not_called()

    def test_pack_copies_entries_rejects_attachments_and_preserves_invalid_refs(self):
        r = ops.draft(self.store, TICKET)
        r['inputs']['repo'] = self.temp.name
        source = [{'mode': 'text', 'label': 'Notes', 'text': 'Conventions'},
                  {'mode': 'reference', 'relative': 'missing.py', 'label': 'Missing', 'start': None, 'end': None}]
        tasks.save_pack(self.store, self.temp.name, 'Conventions', source)
        source[0]['text'] = 'Later'
        with patch('luvus_tasks.productivity.ops.prepare', return_value={'target': {}}), patch('luvus_tasks.productivity.file_context', side_effect=TaskError('Missing file')):
            tasks.apply_pack(self.store, r, 'Conventions')
        self.assertEqual(r['context'][0]['text'], 'Conventions')
        self.assertIn('error', r['context'][1])
        tasks.save_pack(self.store, self.temp.name, 'Conventions', [], delete=True)
        self.assertEqual(r['context'][0]['text'], 'Conventions')
        with self.assertRaises(TaskError):
            tasks.save_pack(self.store, self.temp.name, 'Bad', [{'mode': 'attachment'}])

    def test_snooze_revision_expiry_and_urgent_conditions(self):
        item = {'id': 'feedback', 'source': 'pr-feedback', 'signature': 'a'}
        with patch('luvus_tasks.productivity.time.time', return_value=100):
            tasks.snooze(self.store, item, 900)
            self.assertTrue(tasks.snoozed(self.store, item))
            self.assertFalse(tasks.snoozed(self.store, {**item, 'signature': 'b'}))
        with patch('luvus_tasks.productivity.time.time', return_value=1001):
            self.assertFalse(tasks.snoozed(self.store, item))
        for source in ('agent-input', 'ci', 'validation', 'uncertainty'):
            with self.assertRaises(TaskError):
                tasks.snooze(self.store, {**item, 'source': source}, 900)

    def test_capture_timeout_cannot_repeat_or_change_destination(self):
        c = tasks.new_capture(self.store, 'Output')
        c['title'] = 'Bug'
        self.store.save_capture(c)
        dest = {'connection': 'azure', 'project': 'Example', 'type': 'Bug'}
        with patch('luvus_tasks.capture_providers.fields', return_value=[]), patch('luvus_tasks.capture_providers.create_issue', side_effect=ProviderError('Timeout', True)) as create:
            with self.assertRaises(ProviderError):
                tasks.publish_capture(self.store, c['id'], dest, {}, factory=lambda c: Mock())
            with self.assertRaises(TaskError):
                tasks.publish_capture(self.store, c['id'], {**dest, 'project': 'Elsewhere'}, {}, factory=lambda c: Mock())
            self.assertEqual(create.call_count, 1)
        self.assertEqual(self.store.captures()[0]['state'], 'pending')

    def test_capture_readback_failure_retains_created_identity(self):
        c = tasks.new_capture(self.store)
        c['title'] = 'Bug'
        self.store.save_capture(c)
        client = Mock()
        client.get.side_effect = ProviderError('Readback failed')
        with patch('luvus_tasks.capture_providers.fields', return_value=[]), patch('luvus_tasks.capture_providers.create_issue', return_value=copy.deepcopy(TICKET)):
            with self.assertRaises(ProviderError):
                tasks.publish_capture(self.store, c['id'], {'connection': 'azure'}, {}, factory=lambda c: client)
        saved = self.store.captures()[0]
        self.assertEqual(saved['state'], 'pending')
        self.assertEqual(saved['ticket']['id'], '1')

    def test_progressive_refresh_keeps_failed_cache_and_scopes_network(self):
        config = self.store.config()
        config['connections'].append({**CONNECTION, 'id': 'other', 'project': 'Other'})
        self.store.save_config(config)
        factory = lambda c: Mock(query=Mock(return_value=[{**TICKET, 'connection': c['id'], 'project': c['project']}]))
        ops.refresh(self.store, True, factory)
        called, groups = [], []
        def refreshed(c):
            called.append(c['id'])
            return Mock(query=Mock(side_effect=TaskError('Offline')))
        result, errors = ops.refresh(self.store, True, refreshed, scope=('azure', 'Example'), on_group=lambda t, e: groups.append(t))
        self.assertEqual(called, ['azure'])
        self.assertEqual(len(result), 2)
        self.assertEqual(len(groups), 1)
        self.assertTrue(errors)

    def test_jira_project_scope_preserves_order_and_quoted_order_words(self):
        client = object.__new__(Jira)
        client.c, client.http = {'_scope_project': 'TEAM'}, Mock()
        client.http.request.return_value = {'issues': [], 'isLast': True}
        client.query('summary ~ "ORDER BY" ORDER BY updated DESC')
        expression = client.http.request.call_args.args[2]['jql']
        self.assertEqual(expression, '(summary ~ "ORDER BY") AND project = "TEAM" ORDER BY updated DESC')

    def test_jira_partial_refresh_keeps_other_project_cache(self):
        config = self.store.config()
        config['connections'] = [{'id': 'jira', 'provider': 'jira'}]
        self.store.save_config(config)
        first = {**TICKET, 'connection': 'jira', 'project': 'TEAM', 'provider': 'Jira'}
        other = {**first, 'id': '2', 'project': 'OTHER'}
        ops.refresh(self.store, True, lambda c: Mock(query=Mock(return_value=[first, other])))
        def factory(c):
            self.assertEqual(c['_scope_project'], 'TEAM')
            return Mock(query=Mock(return_value=[{**first, 'title': 'Updated'}]))
        results, errors = ops.refresh(self.store, True, factory, scope=('jira', 'TEAM'))
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(next(t for t in results if t['project'] == 'TEAM')['title'], 'Updated')
        retained = next(t for t in results if t['project'] == 'OTHER')
        self.assertTrue(retained['stale'])
        self.assertEqual(retained['title'], other['title'])

    def test_required_field_validation(self):
        with self.assertRaises(TaskError):
            adapters.field_value({'type': 'string', 'title': 'Required'}, None)
        with self.assertRaises(TaskError):
            adapters.field_value({'type': 'number', 'title': 'Number'}, 'nan')
        self.assertEqual(adapters.field_value({'type': 'date', 'title': 'Date'}, '2026-09-06'), '2026-09-06')
        self.assertEqual(adapters.field_value({'type': 'option', 'title': 'Choice', 'choices': [{'id': '1', 'value': 'One'}]}, '1'), {'id': '1'})

    def test_archive_cannot_race_a_followup(self):
        record = ops.draft(self.store, TICKET)
        with self.store.lock('followup'):
            with self.assertRaises(TaskError):
                tasks.archive(self.store, Mock(), record['id'])
        self.assertFalse(self.store.records('handovers')[0].get('archived_at'))
        self.assertFalse((Path(self.temp.name) / 'archive.lock').exists())

    def test_capture_rejects_bad_fields_before_reserving_and_stale_content(self):
        c = tasks.new_capture(self.store)
        c['title'] = 'Bug'
        self.store.save_capture(c)
        dest = {'connection': 'azure'}
        required = [{'id': 'count', 'type': 'integer', 'title': 'Count'}]
        with patch.object(adapters, 'fields', return_value=required), patch.object(adapters, 'create_issue') as create:
            with self.assertRaises(ValueError):
                tasks.publish_capture(self.store, c['id'], dest, {'count': 'not a number'}, factory=lambda c: Mock())
            self.assertEqual(self.store.captures()[0]['state'], 'draft')
            with self.assertRaises(TaskError):
                tasks.publish_capture(self.store, c['id'], dest, {}, factory=lambda c: Mock(), expected_capture={**c, 'title': 'Older'})
            create.assert_not_called()

    def test_capture_stale_editor_cannot_erase_pending_publication(self):
        c = tasks.new_capture(self.store)
        self.store.save_capture({**c, 'state': 'pending'})
        with self.assertRaises(TaskError):
            self.store.save_capture(c)
        self.assertEqual(self.store.captures()[0]['state'], 'pending')

    def test_phase_draft_keeps_checkout_and_owns_copied_artifact(self):
        output = Path(self.temp.name) / 'plan.txt'
        output.write_text('Plan content')
        parent = ops.draft(self.store, TICKET)
        parent['target'] = {'path': self.temp.name, 'branch': 'feature', 'repo': self.temp.name}
        with patch('luvus_tasks.handover.git', return_value='feature'), patch('luvus_tasks.operations.default_base', return_value='main'), patch('luvus_tasks.handover.target_plan', return_value={'existing': True, 'path': self.temp.name}):
            child = tasks.phase_draft(self.store, parent, 'Implement', 'Use selected plan', [{'mode': 'attachment', 'target': str(output)}])
        self.assertFalse(child['inputs']['new'])
        self.assertEqual(child['inputs']['branch'], 'feature')
        self.assertEqual(child['parent_handover'], parent['id'])
        output.unlink()
        self.assertEqual(Path(child['context'][0]['target']).read_text(), 'Plan content')

    def test_provider_creation_shapes(self):
        github = object.__new__(GitHub)
        github.c = {'id': 'gh', 'repository': 'org/repo'}
        github.repo, github.repos, github.prefix = 'org/repo', ['org/repo'], 'repos/org/repo/issues'
        github.api = Mock(return_value={'number': 1, 'state': 'open', 'title': 'Bug', 'body': 'Body', 'updated_at': 'now'})
        result = adapters.create_issue(github, {'project': 'org/repo', 'type': 'issue'}, 'Bug', 'Body', {})
        self.assertEqual(result['id'], '1')
        with self.assertRaises(TaskError):
            adapters.create_issue(github, {'project': 'other/repo', 'type': 'issue'}, 'Bug', 'Body', {})
        jira = object.__new__(Jira)
        jira.c, jira.http = {'id': 'jira'}, Mock()
        jira.http.request.return_value = {'id': '10', 'key': 'PROJ-10'}
        with patch.object(adapters, 'fields', return_value=[]):
            result = adapters.create_issue(jira, {'project': 'PROJ', 'type': '1'}, 'Bug', 'Body', {})
        self.assertEqual(result['id'], '10')
        self.assertEqual(jira.http.request.call_args.args[2]['fields']['description']['type'], 'doc')
        azure = object.__new__(Azure)
        azure.c = {'id': 'azure', 'project': 'Example'}
        azure.request = Mock(return_value={})
        azure.normalize = Mock(return_value=TICKET)
        with patch.object(adapters, 'fields', return_value=[]):
            adapters.create_issue(azure, {'project': 'Example', 'type': 'Bug'}, 'Bug', '<unsafe>', {})
        self.assertEqual(azure.request.call_args_list[0].kwargs['query'], {'validateOnly': 'true'})
        self.assertTrue(azure.request.call_args_list[1].kwargs['write'])
        self.assertIn('&lt;unsafe&gt;', str(azure.request.call_args_list[1]))


class ProductivityUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Cockpit(self.temp.name, Host(), network=False)
        config = self.app.store.config()
        config['connections'] = [copy.deepcopy(CONNECTION)]
        self.app.store.save_config(config)

    async def asyncTearDown(self):
        self.app.store.db.close()
        self.temp.cleanup()

    async def test_start_reuses_draft_without_provider_or_launch(self):
        record = ops.draft(self.app.store, TICKET)
        with patch.object(self.app, 'wizard', AsyncMock()) as wizard, patch('luvus_tasks.productivity_ui.provider') as provider:
            await productivity_ui.continue_task(self.app, TICKET)
        wizard.assert_awaited_once_with(record)
        provider.assert_not_called()
        self.assertEqual(len(self.app.store.records('handovers')), 1)

    async def test_search_never_hashes_or_calls_provider_and_text_shortcuts_are_safe(self):
        from textual.widgets import Input
        async with self.app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            self.app.tasks = [TICKET]
            with patch('luvus_tasks.attention.fingerprint') as fingerprint, patch('luvus_tasks.operations.provider') as provider:
                self.app.query_one('#search', Input).focus()
                await pilot.press('s', 'f', 'e', 'a')
                await pilot.pause()
                self.assertEqual(self.app.query_one('#search', Input).value, 'sfea')
            fingerprint.assert_not_called()
            provider.assert_not_called()

    async def test_capture_autosaves_without_publication(self):
        with patch.object(self.app, 'form', AsyncMock(return_value=None)), patch('luvus_tasks.productivity.publish_capture') as publish:
            await productivity_ui.capture(self.app, context={'selection': 'Explicit selected output'})
        self.assertEqual(self.app.store.captures()[0]['body'], 'Explicit selected output')
        publish.assert_not_called()

    async def test_capture_form_retains_failed_autosave_and_blocks_submit(self):
        from luvus_tasks.console import Form
        from textual.widgets import Input
        async with self.app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            form = Form('Capture failure', [('title', 'Title', '', 'input')], autosave=Mock(side_effect=OSError('disk full')))
            await self.app.push_screen(form)
            form.query_one('#title', Input).value = 'Retain this title'
            await pilot.pause()
            await pilot.click('#submit')
            self.assertIs(self.app.screen, form)
            self.assertEqual(form.values()['title'], 'Retain this title')
            self.assertTrue(form.save_failed)
            await pilot.press('escape')
            self.assertIs(self.app.screen, form)

    async def test_cancelled_batch_has_no_new_drafts(self):
        self.app.tasks = [copy.deepcopy(TICKET)]
        self.app.checked = {'azure:1'}
        values = {'agent': '', 'preset': '', 'pack': '', 'replace-notes': False, 'notes': ''}
        with patch.object(self.app, 'form', AsyncMock(return_value=values)), patch.object(self.app, 'confirm', AsyncMock(return_value=False)), patch('luvus_tasks.productivity_ui.workflow_target', return_value={'path': '/new', 'branch': 'feature'}):
            await productivity_ui.batch(self.app)
        self.assertEqual(self.app.store.records('handovers'), [])

    async def test_batch_preserves_custom_prompt_and_hidden_selection(self):
        from luvus_tasks.handover import save_record
        first = ops.draft(self.app.store, TICKET)
        first.update(prompt='My custom instructions', prompt_mode='custom', generated_prompt='Original')
        save_record(self.app.store, first)
        second = {**TICKET, 'id': '2', 'key': 'AB#2'}
        self.app.tasks = [TICKET, second]
        self.app.checked = {'azure:1', 'azure:2'}
        self.app.visible_issue_ids = {'azure:1'}
        values = {'agent': '', 'preset': '', 'pack': '', 'replace-notes': True, 'notes': 'Shared note'}
        with patch.object(self.app, 'form', AsyncMock(return_value=values)), patch.object(self.app, 'confirm', AsyncMock(return_value=True)), patch.object(self.app, 'show_tab'), patch('luvus_tasks.productivity_ui.workflow_target', side_effect=lambda s, r: {'path': '/new/' + r['ticket']['id'], 'branch': 'feature/' + r['ticket']['id']}):
            await productivity_ui.batch(self.app)
        saved = self.app.store.records('handovers')
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(r['notes'] == 'Shared note' for r in saved))
        self.assertEqual(next(r for r in saved if r['id'] == first['id'])['prompt'], 'My custom instructions')

    async def test_attention_groups_expand_and_snoozed_filter(self):
        from textual.widgets import Select, DataTable
        async with self.app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            item = {'kind': 'attention', 'handover': 'r', 'task': 'AB#1', 'worktree': '/repo', 'state': 'active', 'since': '2026-01-01', 'signature': 'a', 'target': {'record': 'r'}, 'source': 'pr-feedback'}
            self.app.attention_items = [{**item, 'id': 'one', 'title': 'First'}, {**item, 'id': 'two', 'title': 'Second'}]
            self.app.paint_attention()
            table = self.app.query_one('#attention-table', DataTable)
            self.assertEqual(table.row_count, 1)
            await productivity_ui.action(self.app, 'task-expand-attention')
            self.assertEqual(table.row_count, 2)
            tasks.snooze(self.app.store, self.app.attention_items[0], 900)
            self.app.query_one('#attention-visibility', Select).value = 'snoozed'
            self.app.paint_attention()
            self.assertEqual(table.row_count, 1)
            self.assertEqual(self.app.selected_attention, 'one')
