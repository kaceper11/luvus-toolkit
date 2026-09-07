import asyncio
import copy
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from textual.widgets import DataTable, Input, SelectionList, TabbedContent

from luvus_tasks import operations as ops, productivity, pr_links
from luvus_tasks.console import Cockpit, IssueSelection, Preview
from luvus_tasks.core import TaskError, default_config, handover_tickets, has_ticket, ticket_key
from luvus_tasks.handover import git, launch, save_record
from test_tasks import GitTests, GITHUB
from test_console import Host
from test_handover_ux import TICKET


class GroupTests(unittest.TestCase):
    tearDown = GitTests.tearDown

    def setUp(self):
        GitTests.setUp(self)
        git(self.repo, 'remote', 'add', 'origin', 'https://github.com/example/repo.git')
        c = default_config()
        c['connections'] = [{**GITHUB, 'repositories': {'example/repo': str(self.repo)}}]
        self.store.save_config(c)
        self.one = {**TICKET, 'project': 'example/repo', 'key': 'example/repo#1', 'url': 'https://github.com/example/repo/issues/1', 'acceptance': 'first criterion'}
        self.two = {**self.one, 'id': '2', 'key': 'example/repo#2', 'url': 'https://github.com/example/repo/issues/2', 'acceptance': 'second criterion'}
        self.record = ops.draft(self.store, self.one, str(self.repo))
        self.record['inputs'].update(branch='main', base='main', new=False)

    def test_group_prompt_membership_single_launch_and_freeze(self):
        ops.set_group_issues(self.store, self.record, [self.one, self.two, self.one], 'Billing fixes')
        ops.prepare(self.store, self.record)
        self.assertEqual(len(handover_tickets(self.record)), 2)
        self.assertTrue(has_ticket(self.record, self.two))
        self.assertEqual(productivity.candidates(self.store, self.two)[0]['id'], self.record['id'])
        self.assertEqual(self.record['prompt'].count('first criterion'), 1)
        self.assertEqual(self.record['prompt'].count('second criterion'), 1)
        host = Mock()
        host.agents.return_value = []
        host.call.side_effect = lambda method, **kw: {'pane_id': '7', 'terminal_id': 't', 'server_generation': 'g'} if method == 'terminal.backend.create' else {'ready': True}
        launch(self.store, host, self.record)
        launch(self.store, host, self.record)
        prompts = [c for c in host.call.call_args_list if c.args[0] == 'agent.prompt']
        self.assertEqual(len(prompts), 1)
        self.assertIn(self.two['url'], prompts[0].kwargs['text'])
        with self.assertRaisesRegex(TaskError, 'frozen'):
            ops.set_group_issues(self.store, self.record, [self.one])
        child = productivity.phase_draft(self.store, self.record, 'Implement', 'Continue')
        self.assertEqual(handover_tickets(child), [self.one, self.two])

    def test_repository_conflict_empty_and_custom_prompt(self):
        with self.assertRaises(TaskError):
            ops.set_group_issues(self.store, self.record, [])
        with self.assertRaisesRegex(TaskError, 'one code repository'):
            ops.set_group_issues(self.store, self.record, [self.one, {**self.two, 'project': 'other/repo'}])
        ops.update_prompt(self.store, self.record)
        self.record.update(prompt='Keep my exact instructions', prompt_mode='custom')
        ops.set_group_issues(self.store, self.record, [self.one, self.two])
        self.assertEqual(self.record['prompt'], 'Keep my exact instructions')
        self.assertTrue(self.record['prompt_outdated'])
        config = self.store.config()
        config['connections'].append({'id': 'jira', 'provider': 'jira', 'repositories': {'TEST': str(self.repo)}})
        self.store.save_config(config)
        jira = {**self.two, 'connection': 'jira', 'provider': 'Jira', 'project': 'TEST', 'id': '3', 'key': 'TEST-3'}
        ops.set_group_issues(self.store, self.record, [self.one, jira])
        config['connections'][-1]['repositories'] = {}
        self.store.save_config(config)
        with self.assertRaisesRegex(TaskError, 'map this issue'):
            ops.prepare(self.store, self.record)

    def test_sidebar_titles_open_issues_and_group_is_shown_once(self):
        ops.set_group_issues(self.store, self.record, [self.one, self.two], 'Billing fixes')
        ops.prepare(self.store, self.record)
        rows, _, _ = ops.dashboard(self.store, [self.one, self.two], [], {'mode': 'all'}, attention_items=[])
        main = [r for r in rows if r.get('value') == ticket_key(self.one)]
        self.assertEqual(len(main), 1)
        self.assertIn('2 issues', main[0]['text'])
        backlog = {**self.one, 'id': '99', 'key': 'example/repo#99', 'project': 'aaa/repo'}
        rows, _, _ = ops.dashboard(self.store, [backlog, self.one, self.two], [], {'mode': 'all'}, attention_items=[])
        text = [r['text'] for r in rows]
        self.assertLess(text.index('Current work · PR / CI'), text.index('Open issues'))
        self.assertLess(next(i for i, r in enumerate(rows) if r.get('value') == ticket_key(self.one)), text.index('Open issues'))
        self.assertTrue(any(m['value'] == ticket_key(self.two) for m in main[0]['menu']))
        self.assertFalse(any('Checkout unavailable' in r['text'] for r in rows))
        self.assertLess(len(main[0]['menu']), 20)


class FakePR:
    repo = 'example/repo'

    def __init__(self):
        self.prs = [self.make('7', 'main', 'Fixes #1'), self.make('8', 'other', 'Fixes example/repo#2'), self.make('9', 'unrelated', 'Issue 1 is interesting')]
        self.fail = set()

    def make(self, ident, branch, body):
        return {'id': ident, 'head_branch': branch, 'body': body, 'url': 'https://github.com/example/repo/pull/' + ident,
                'repository': self.repo, 'head': ident * 40, 'title': 'PR', 'state': 'open', 'draft': False}

    def list(self, branch):
        return [copy.deepcopy(p) for p in self.prs if p['head_branch'] == branch and p['state'] == 'open']

    def api(self, endpoint):
        return copy.deepcopy(self.prs)

    def normalize(self, p):
        return p

    def get(self, ident):
        return copy.deepcopy(next(p for p in self.prs if p['id'] == str(int(ident))))

    def snapshot(self, ident):
        if ident in self.fail:
            raise TaskError('provider offline')
        p = self.get(ident)
        return {**p, 'checks': [{'id': 'build', 'state': 'passed', 'revision': p['head']}], 'feedback': [], 'runs': []}


class PRTests(unittest.TestCase):
    tearDown = GroupTests.tearDown
    # Reuse setup only, not GroupTests' test methods.
    def setUp(self):
        GroupTests.setUp(self)
        ops.set_group_issues(self.store, self.record, [self.one, self.two])
        ops.prepare(self.store, self.record)
        self.client = FakePR()
        self.link = {'kind': 'github', 'connection': 'github', 'repository': 'example/repo'}

    def test_discovery_deduplicates_matches_and_keeps_closed_links(self):
        pr_links.refresh(self.store, self.record, self.client, self.link)
        record = self.store.records('handovers')[0]
        self.assertEqual(record['pr_id'], '7')
        self.assertEqual({p['id'] for p in record['linked_prs']}, {'7', '8'})
        self.assertEqual(record['linked_prs'][0]['sources'], ['Branch match', 'Issue reference'])
        self.assertIn('2 PRs', pr_links.summary(self.store, record))
        self.client.prs[1].update(state='merged', body='')
        pr_links.refresh(self.store, record, self.client, self.link)
        record = self.store.records('handovers')[0]
        self.assertEqual(len(record['linked_prs']), 2)
        self.assertTrue(any(e.get('snapshot', {}).get('state') == 'merged' for _, e in pr_links.items(self.store, record)))
        self.client.fail.add('8')
        pr_links.refresh(self.store, record, self.client, self.link)
        self.assertIn('stale', pr_links.summary(self.store, record))

    def test_multiple_branch_matches_ignore_restore_and_manual_validation(self):
        self.client.prs[1]['head_branch'] = 'main'
        pr_links.refresh(self.store, self.record, self.client, self.link)
        record = self.store.records('handovers')[0]
        self.assertFalse(record.get('pr_id'))
        record['ignored_prs'] = [pr_links.key(self.link, '8')]
        save_record(self.store, record)
        pr_links.refresh(self.store, record, self.client, self.link)
        record = self.store.records('handovers')[0]
        self.assertEqual(record['pr_id'], '7')
        self.assertEqual(len(pr_links.items(self.store, record)), 1)
        with patch.object(pr_links, 'read_client', return_value=(self.client, self.link)):
            with self.assertRaises(TaskError):
                pr_links.link_url(self.store, record, 'https://github.com/other/repo/pull/7')
            record = pr_links.link_url(self.store, record, 'https://github.com/example/repo/pull/8')
        self.assertEqual(len(pr_links.items(self.store, record)), 2)
        self.assertIn('Manually linked', record['linked_prs'][1]['sources'])

    def test_unpublished_branch_is_not_an_auth_error(self):
        from luvus_tasks import forge
        p = forge.GitHubPR.__new__(forge.GitHubPR)
        p.repo = 'example/repo'
        p.api = Mock(side_effect=[None, {'full_name': p.repo}])
        snapshot = p.snapshot(branch='not-pushed')
        self.assertEqual(snapshot['state'], 'unpublished')
        self.assertEqual(snapshot['checks'], [])
        p.api.assert_any_call('')
        p.api = Mock(side_effect=[None, TaskError('repository denied')])
        with self.assertRaisesRegex(TaskError, 'repository denied'):
            p.snapshot(branch='not-pushed')

    def test_exact_references_and_revision_status(self):
        for body in ('Fixes #1', 'example/repo#2', self.two['url']):
            self.assertTrue(pr_links.references(body, [self.one, self.two], 'example/repo'))
        for body in ('#12', 'other/repo#1', 'feature/1', 'issue 1', self.one['url'] + '2'):
            self.assertFalse(pr_links.references(body, [self.one], 'example/repo'))
        item = {'snapshot': {'head': 'current', 'checks': [{'state': 'passed', 'revision': 'old'}]}}
        self.assertEqual(pr_links.ci_state(item), 'unknown')
        item['snapshot'].update(merge='merge-current', checks=[{'state': 'passed', 'revision': 'current'}, {'state': 'failed', 'revision': 'merge-current'}])
        self.assertEqual(pr_links.ci_state(item), 'failed')


class GroupUITests(unittest.IsolatedAsyncioTestCase):
    async def test_picker_preserves_hidden_selections_and_cancel_has_no_writes(self):
        with tempfile.TemporaryDirectory() as root:
            app = Cockpit(root, Host(), network=False)
            one = copy.deepcopy(TICKET)
            two = {**one, 'id': '2', 'key': '#2', 'title': 'Second issue'}
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                task = app.run_worker(app.push_screen_wait(IssueSelection([one, two], [one])))
                await pilot.pause()
                app.screen.query_one(Input).value = 'Second'
                await pilot.pause()
                await pilot.click('#group-select')
                await pilot.pause()
                await pilot.click('#group-save')
                self.assertEqual(await task.wait(), [one, two])
                self.assertEqual(app.store.records('handovers'), [])
                self.assertFalse(app.query('#work'))
                self.assertFalse(app.query('#orchestration'))
            app.store.db.close()

    async def test_sidebar_click_opens_exact_issue_during_busy_action(self):
        with tempfile.TemporaryDirectory() as root:
            app = Cockpit(root, Host(), network=False)
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                await app.workers.wait_for_complete()
                second = {**TICKET, 'id': '2', 'key': 'TEST-2', 'title': 'Second issue', 'description': 'Distinct second issue body'}
                app.tasks = [copy.deepcopy(TICKET), second]
                app.paint()
                app.io = AsyncMock(return_value=(app.tasks, []))
                app.show_tab('configuration')
                app.query_one('#search', Input).value = 'does not match'
                app.busy = True
                worker = app.dispatch('inbox', {'action': 'open', 'ticket': ticket_key(second)})
                await worker.wait()
                await pilot.pause()
                self.assertEqual(app.query_one('#tabs', TabbedContent).active, 'issues')
                self.assertEqual(app.selected_issue, ticket_key(second))
                self.assertIn(second['description'], app.query_one('#issue-text', Preview).text)
                app.query_one('#issues-table', DataTable).focus()
                await pilot.press('space')
                self.assertEqual(app.checked, {ticket_key(second)})
                worker = app.dispatch('toggle')
                await worker.wait()
                self.assertEqual(app.checked, set())
                self.assertTrue(app.busy)
            app.store.db.close()
