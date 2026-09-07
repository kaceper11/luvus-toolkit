"""Isolated navigation regressions: no tracker requests, agent starts or sends."""
import copy
import asyncio
import threading
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from textual.widgets import Input, Select, TabbedContent, DataTable, Button, Static

from luvus_tasks.console import Cockpit
from luvus_tasks.core import TaskError, default_config
from luvus_tasks import operations as ops, workflow_ui
from luvus_tasks.handover import save_record
from test_console import Host, TICKET, CONNECTION, wait_until


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_controls_and_existing_draft_bypass_busy_action(self):
        record = ops.draft(self.app.store, TICKET)
        async with self.app.run_test() as pilot:
            await pilot.pause()
            self.app.history = [record]
            self.app.selected_handover = record['id']
            self.app.busy = True
            self.app.dispatch('task-evidence')
            await pilot.pause()
            self.assertEqual(self.app.query_one('#tabs', TabbedContent).active, 'handovers')
            self.assertTrue(self.app.busy)
            self.app.dispatch('task-start')
            await pilot.pause()
            await self.app.workers.wait_for_complete()
            await wait_until(pilot, lambda: self.app.active_editor() is not None)
            self.assertEqual(self.app.active_editor().record['id'], record['id'])
            self.app.dispatch('task-next-attention')
            await pilot.pause()
            await wait_until(pilot, lambda: self.app.query_one('#tabs', TabbedContent).active == 'attention')
            self.assertEqual(self.app.query_one('#tabs', TabbedContent).active, 'attention')
            with patch.object(self.app, 'copy_data', new=AsyncMock()) as copied:
                self.app.dispatch('copy')
                await pilot.pause()
                copied.assert_awaited_once()
            self.assertTrue(self.app.busy)
            self.app.busy = False

    async def test_saved_blank_base_is_suggested_without_overwriting_manual_choice(self):
        record = ops.draft(self.app.store, TICKET)
        record['inputs']['base'] = ''
        async with self.app.run_test() as pilot:
            await pilot.pause()
            with patch('luvus_tasks.editor.default_base', return_value='main'), patch('luvus_tasks.editor.base_branches', return_value=['main']):
                await self.app.wizard(record)
                await pilot.pause()
                editor = self.app.active_editor()
                self.assertEqual(editor.record['inputs']['base'], 'main')
                editor.query_one('#target-base', Input).value = ''
                await pilot.pause()
                editor.load_branches()
                await pilot.pause()
                self.assertEqual(editor.record['inputs']['base'], '')

    async def test_null_draft_and_filter_selection_are_safe_before_and_after_events(self):
        record = ops.draft(self.app.store, TICKET)
        record['target'] = None
        save_record(self.app.store, record)
        async with self.app.run_test() as pilot:
            await pilot.pause()
            await self.app.wizard(record)
            await wait_until(pilot, lambda: self.app.active_editor() is not None)
            self.assertEqual(self.app.active_editor().record['id'], record['id'])
            self.app.show_tab('issues')
            await pilot.pause()
            a = {**TICKET, 'title': 'Alpha'}
            b = {**TICKET, 'id': '2', 'key': 'AB#2', 'title': 'Beta'}
            self.app.tasks = [a, b]
            self.app.selected_issue = 'azure:1'
            self.app.checked = {'azure:1', 'azure:2'}
            self.app.paint()
            self.app.query_one('#search', Input).value = 'Beta'
            self.app.paint()
            self.assertIsNone(self.app.selected_issue)
            self.assertTrue(self.app.query_one('Button#handover', Button).disabled)
            await pilot.pause()
            self.assertIsNone(self.app.selected_issue)
            self.assertIn('1 visible, 1 hidden', str(self.app.query_one('#summary', Static).render()))
            self.assertEqual(self.app.checked, {'azure:1', 'azure:2'})

    async def test_slow_discovery_does_not_delay_editor_or_steal_navigation(self):
        record = ops.draft(self.app.store, TICKET)
        gate = threading.Event()
        def discover():
            gate.wait(5)
            return {}
        async with self.app.run_test() as pilot:
            await pilot.pause()
            with patch.object(self.app.host, 'available_agents', side_effect=discover):
                try:
                    await asyncio.wait_for(self.app.wizard(record), 1.0)
                    self.assertFalse(gate.is_set())
                    self.assertEqual(self.app.query_one('#tabs', TabbedContent).active, 'handover')
                    editor = self.app.active_editor()
                    saved_agent = editor.record['agent']
                    await pilot.click('#--content-tab-attention')
                finally:
                    gate.set()
                await pilot.pause()
                await wait_until(pilot, lambda: self.app.query_one('#tabs', TabbedContent).active == 'attention')
                self.assertEqual(self.app.query_one('#tabs', TabbedContent).active, 'attention')
                self.assertEqual(editor.record['agent'], saved_agent)

    async def test_shortcut_bypasses_busy_work_and_latest_destination_wins(self):
        import json
        async with self.app.run_test() as pilot:
            await pilot.pause()
            self.app.busy = True
            for section in ('draft', 'attention-all'):
                self.app.pending.append({'action': 'open', 'ticket': 'dashboard:' + json.dumps({'section': section})})
            self.app.read_inbox()
            async def arrived():
                while self.app.query_one('#tabs', TabbedContent).active != 'attention':
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(arrived(), 0.25)
            self.assertFalse(self.app.pending)
            self.app.busy = False

    async def test_local_search_can_be_selected_while_native_search_is_stalled(self):
        gate = asyncio.Event()
        async def load_more():
            await gate.wait()
            return 'Native results loaded'
        async with self.app.run_test() as pilot:
            await pilot.pause()
            rows = [{'id': 'local', 'title': 'Local draft'}]
            worker = self.app.run_worker(workflow_ui.select(self.app, 'Results', rows, load_more=load_more))
            await pilot.pause()
            self.assertEqual(self.app.screen.query_one('#choice', Select).value, '0')
            await pilot.click('#submit')
            self.assertEqual(await worker.wait(), rows[0])
            self.assertFalse(gate.is_set())

    async def test_action_identity_is_frozen_while_selection_changes(self):
        first = ops.draft(self.app.store, TICKET)
        second = ops.draft(self.app.store, TICKET)
        entered, finish = asyncio.Event(), asyncio.Event()
        captured = []
        async def perform(action, payload):
            entered.set()
            await finish.wait()
            captured.append(self.app.record()['id'])
        async with self.app.run_test() as pilot:
            await pilot.pause()
            self.app.selected_handover = first['id']
            with patch.object(self.app, 'perform', side_effect=perform):
                worker = self.app.dispatch('work-test')
                await entered.wait()
                self.app.selected_handover = second['id']
                finish.set()
                await worker.wait()
            self.assertEqual(captured, [first['id']])

    async def test_repaint_preserves_long_table_scroll_without_rebuilding_unchanged_rows(self):
        async with self.app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            self.app.tasks = [{**TICKET, 'id': str(i), 'key': 'AB#' + str(i)} for i in range(250)]
            self.app.paint()
            await pilot.pause()
            table = self.app.query_one('#issues-table', DataTable)
            table.scroll_to(y=100, animate=False, force=True)
            await pilot.pause()
            position = table.scroll_y
            self.assertGreater(position, 0)
            with patch.object(table, 'clear', wraps=table.clear) as clear:
                self.app.paint(False)
                await pilot.pause()
                clear.assert_not_called()
            self.assertEqual(table.scroll_y, position)

    async def test_navigation_preserves_interrupted_form_text_without_submitting(self):
        from luvus_tasks.console import Form
        async with self.app.run_test() as pilot:
            await pilot.pause()
            fields = [('note', 'Notes', '', 'text')]
            worker = self.app.run_worker(self.app.form('Context notes', fields))
            await pilot.pause()
            self.app.screen.query_one('#note').load_text('unfinished context')
            await pilot.pause()
            await self.app.navigate('show-attention').wait()
            self.assertIsNone(await worker.wait())
            await wait_until(pilot, lambda: self.app.query_one('#tabs', TabbedContent).active == 'attention')
            self.assertEqual(self.app.query_one('#tabs', TabbedContent).active, 'attention')
            reopened = self.app.run_worker(self.app.form('Context notes', fields))
            await pilot.pause()
            self.assertEqual(self.app.screen.query_one('#note').text, 'unfinished context')
            self.app.screen.dismiss(None)
            await reopened.wait()
            await self.app.action_quit()
            await pilot.pause()
            self.assertIsInstance(self.app.screen, Form)
            self.assertEqual(self.app.screen.heading, 'Unsaved drafts')
            self.app.screen.dismiss(None)

    async def test_shortcuts_use_registered_console_channel(self):
        session = ops.session_key()
        self.app.store.set_preference('console:' + session, {'pane': '5', 'terminal': 'new', 'generation': 'g', 'inbox': session + ':terminal:new'})
        host = Mock()
        host.call.return_value = {'server_generation': 'g', 'terminals': [{'pane_id': '5', 'terminal_id': 'new'}]}
        ops.open_console(self.app.store, host, ticket='dashboard:{}')
        self.assertEqual(self.app.store.take_actions(session), [])
        self.assertEqual(len(self.app.store.take_actions(session + ':terminal:new')), 1)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Cockpit(self.temp.name, Host(), network=False)
        config = default_config()
        config['connections'] = [copy.deepcopy(CONNECTION)]
        self.app.store.save_config(config)

    async def asyncTearDown(self):
        self.app.store.db.close()
        self.temp.cleanup()

    async def test_search_same_issue_number_in_other_project_is_retained(self):
        first = {**TICKET, 'key': '#1', 'project': 'org/first'}
        second = {**TICKET, 'key': '#1', 'id': '2', 'project': 'org/second'}
        ops.draft(self.app.store, first)
        async with self.app.run_test() as pilot:
            await pilot.pause()
            self.app.tasks = [first, second]
            with patch.object(self.app, 'form', AsyncMock(return_value={'query': ' #1 '})), patch('luvus_tasks.workflow_ui.select', AsyncMock(return_value=None)) as select:
                await workflow_ui.search(self.app)
            rows = select.call_args.args[2]
            self.assertEqual(len(rows), 2)
            self.assertIn('org/second', rows[1]['title'])
            self.assertEqual(rows[1]['ticket']['id'], '2')
            self.assertIn('local matches ready', select.call_args.args[1])
            self.assertIn('stored matches only', await select.call_args.kwargs['load_more']())

    async def test_search_matches_checkout_path_and_rejects_missing_native_pane(self):
        record = ops.draft(self.app.store, TICKET)
        record['inputs']['repo'] = '/project/needle'
        save_record(self.app.store, record)
        async with self.app.run_test() as pilot:
            await pilot.pause()
            with patch.object(self.app, 'form', AsyncMock(return_value={'query': 'needle'})), patch('luvus_tasks.workflow_ui.select', AsyncMock(return_value=None)) as select:
                await workflow_ui.search(self.app)
            self.assertEqual(select.call_args.args[2][0]['record'], record['id'])
            def native(method, **params):
                if method == 'search.query':
                    self.assertEqual(params['scope'], 'navigate')
                    return {'partial': True, 'matches': [{'id': 'n', 'label': 'needle', 'kind': 'pane', 'target': {'pane': 'gone'}}]}
                if method == 'terminal.backend.inventory':
                    return {'server_generation': 'g', 'terminals': []}
                self.fail('No activation allowed: ' + method)
            async def choose(app, title, rows, load_more):
                self.assertIn('partial', await load_more())
                return rows[-1]
            with patch.object(self.app, 'form', AsyncMock(return_value={'query': 'needle'})), patch.object(self.app.host, 'call', side_effect=native), patch('luvus_tasks.workflow_ui.select', side_effect=choose):
                with self.assertRaisesRegex(TaskError, 'target changed'):
                    await workflow_ui.search(self.app)

    async def test_missing_workspace_context_never_falls_back_to_cwd(self):
        async with self.app.run_test() as pilot:
            await pilot.pause()
            with self.assertRaisesRegex(TaskError, 'path is missing'):
                await self.app.workspace_handover({})
            self.assertEqual(self.app.store.records('handovers'), [])

    async def test_stopped_existing_draft_is_offered_without_creating_another(self):
        record = ops.draft(self.app.store, TICKET)
        record['inputs'].update(repo=self.temp.name, new=False)
        save_record(self.app.store, record)
        async with self.app.run_test() as pilot:
            await pilot.pause()
            with patch('luvus_tasks.console.repository', return_value=self.temp.name), patch('luvus_tasks.console.git', return_value='feature'), patch.object(self.app, 'choice', AsyncMock(return_value=record['id'])), patch.object(self.app, 'wizard', AsyncMock()) as wizard:
                await self.app.workspace_handover({'workspace': {'cwd': self.temp.name}})
            wizard.assert_awaited_once()
            self.assertEqual(len(self.app.store.records('handovers')), 1)

    async def test_unmapped_non_github_issue_not_assigned_to_workspace(self):
        async with self.app.run_test() as pilot:
            await pilot.pause()
            self.app.tasks = [TICKET]
            with patch('luvus_tasks.console.repository', return_value=self.temp.name), patch('luvus_tasks.console.git', return_value='feature'), patch('luvus_tasks.operations.ticket_matches', return_value=False):
                with self.assertRaisesRegex(TaskError, 'repository mapping'):
                    await self.app.workspace_handover({'workspace': {'cwd': self.temp.name}})
            self.assertEqual(self.app.store.records('handovers'), [])

    async def test_rejected_console_open_does_not_leave_surprise_queued_action(self):
        host = Mock()
        host.call.return_value = {'server_generation': 'g', 'terminals': []}
        self.app.store.set_preference('console:' + ops.session_key(), {'pending': True, 'generation': 'g'})
        with self.assertRaisesRegex(TaskError, 'uncertain'):
            ops.open_console(self.app.store, host, action='workspace-handover', context={'workspace': {'cwd': self.temp.name}})
        self.assertEqual(self.app.store.records('inbox'), [])
