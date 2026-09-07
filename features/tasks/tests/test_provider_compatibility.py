"""Cross-provider regressions for grouped handovers and the compact PR UI."""
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from textual.widgets import Select
from luvus_tasks import forge, operations as ops, pr_links
from luvus_tasks.console import Cockpit
from luvus_tasks.core import handover_tickets, ticket_key
from luvus_tasks.providers import Jira, Azure
import test_tasks as fixtures
from test_console import Host


class ProviderCompatibility(unittest.TestCase):
    def test_jira_search_keeps_handover_material(self):
        issue = fixtures.jira_issue('1')
        issue['fields']['description'] = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Required behavior'}]}]}
        issue['fields']['customfield_123'] = 'Acceptance requirement'
        http = Mock()
        http.request.return_value = {'issues': [issue], 'isLast': True}
        item = Jira({**fixtures.JIRA, 'acceptance_field': 'customfield_123'}, http).query()[0]
        self.assertIn('description', http.request.call_args.args[2]['fields'])
        self.assertIn('customfield_123', http.request.call_args.args[2]['fields'])
        self.assertIn('Required behavior', item['description'])
        self.assertEqual(item['acceptance'], 'Acceptance requirement')

    def azure_client(self):
        p = forge.AzurePR.__new__(forge.AzurePR)
        p.repo = 'repository-guid'
        p.git = 'git/repositories/' + p.repo
        p.repository = {'id': p.repo, 'project': {'id': 'project-guid'}, 'webUrl': 'https://dev.azure.com/org/Code/_git/Repo'}
        p.azure = Mock()
        p.list = Mock(return_value=[])
        p.request = Mock(return_value={'value': []})
        raw = {'pullRequestId': 7, 'title': 'TEAM-2 implementation', 'description': '', 'status': 'active',
               'sourceRefName': 'refs/heads/other', 'targetRefName': 'refs/heads/main', 'lastMergeSourceCommit': {'commitId': 'head'}}
        p.get = Mock(return_value=p.normalize(raw))
        return p, raw

    def test_azure_native_links_and_jira_title_discovery(self):
        p, raw = self.azure_client()
        azure = {'provider': 'Azure DevOps', 'id': '42', 'key': 'AB#42', 'project': 'Work', 'connection': 'azure', 'url': 'https://dev.azure.com/org/Work/_workitems/edit/42'}
        jira = {'provider': 'Jira', 'id': '200', 'key': 'TEAM-2', 'project': 'TEAM', 'connection': 'jira', 'url': 'https://team.atlassian.net/browse/TEAM-2'}
        link = {'kind': 'azure', 'connection': 'code', 'organization': 'org', 'project': 'Code', 'repository': 'Repo'}
        p.request.return_value = {'value': [raw]}
        p.azure.request.return_value = {'relations': [
            {'rel': 'ArtifactLink', 'url': 'vstfs:///Git/PullRequestId/project-guid%2Frepository-guid%2F7'},
            {'rel': 'ArtifactLink', 'url': 'vstfs:///Git/PullRequestId/project-guid%2Fother-repo%2F8'}]}
        record = {'target': {'branch': 'current'}, 'ticket': azure, 'tickets': [azure, jira]}
        found, notice = pr_links.candidates(p, link, record)
        self.assertEqual({source for _, source in found}, {'Issue reference', 'Azure work-item link'})
        p.get.assert_called_once_with('7')
        self.assertEqual(notice, '')
        self.assertFalse(pr_links.references('AB#42', [azure], 'Repo', 'other-org'))
        self.assertTrue(pr_links.references(azure['url'], [azure], 'Repo', 'other-org'))
        p.azure.request.side_effect = ops.TaskError('denied')
        found, notice = pr_links.candidates(p, link, record)
        self.assertIn('Work Items read permission', notice)
        self.assertEqual(found[0][1], 'Issue reference')

    def test_azure_unpublished_and_ci_revision(self):
        p, _ = self.azure_client()
        p.pages = Mock(return_value=[])
        self.assertEqual(p.snapshot(branch='not-pushed')['state'], 'unpublished')
        p.get.return_value.update(reviewers=[], merge='merge')
        p.pages.side_effect = lambda path, query=None: [{'id': 1, 'sourceVersion': 'merge', 'status': 'completed', 'result': 'failed'}] if path == 'build/builds' else []
        item = {'snapshot': p.snapshot('7')}
        self.assertEqual(pr_links.ci_state(item), 'failed')

    def test_azure_remote_shapes(self):
        for remote in ('https://dev.azure.com/org/Code/_git/Repo', 'git@ssh.dev.azure.com:v3/org/Code/Repo', 'https://org.visualstudio.com/Code/_git/Repo'):
            with patch.object(forge, 'git', return_value=remote):
                self.assertEqual(forge.remote_identity('/repo'), {'kind': 'azure', 'organization': 'org', 'project': 'Code', 'repository': 'Repo'})


class GroupCompatibility(unittest.TestCase):
    setUp = fixtures.GitTests.setUp
    tearDown = fixtures.GitTests.tearDown

    def test_jira_and_azure_share_one_azure_code_checkout(self):
        from luvus_tasks.handover import git
        git(self.repo, 'remote', 'add', 'origin', 'https://dev.azure.com/org/Code/_git/Repo')
        config = self.store.config()
        config['connections'] = [{**fixtures.JIRA, 'repositories': {'TEAM': str(self.repo)}},
                                 {**fixtures.AZURE, 'organization': 'org', 'project': 'Code', 'repositories': {'Sample': str(self.repo)}}]
        self.store.save_config(config)
        jira = Jira(fixtures.JIRA, Mock()).normalize(fixtures.jira_issue('1'))
        jira['project'] = 'TEAM'
        azure = Azure(fixtures.AZURE, Mock()).normalize(fixtures.azure_item(2))
        azure['project'] = 'Sample'
        record = ops.draft(self.store, jira, str(self.repo))
        record['inputs'].update(new=False, branch='main', base='main')
        ops.set_group_issues(self.store, record, [jira, azure])
        ops.prepare(self.store, record)
        self.assertEqual(len(handover_tickets(record)), 2)
        self.assertIn(azure['url'], record['prompt'])
        self.assertIn(jira['url'], record['prompt'])
        self.assertEqual(forge.choices(self.store, record)[0]['kind'], 'azure')


class ProjectNavigation(unittest.IsolatedAsyncioTestCase):
    async def test_non_github_project_sidebar_navigation(self):
        with tempfile.TemporaryDirectory() as root:
            app = Cockpit(root, Host(), network=False)
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                await app.workers.wait_for_complete()
                jira = Jira(fixtures.JIRA, Mock()).normalize(fixtures.jira_issue('1'))
                azure = Azure(fixtures.AZURE, Mock()).normalize(fixtures.azure_item(2))
                app.tasks = [jira, azure]
                app.paint()
                for ticket in (jira, azure):
                    await app.inbox_action({'action': 'open', 'ticket': 'dashboard:' + json.dumps({'section': 'issues', 'project': ticket['project']})})
                    await pilot.pause()
                    app.paint()
                    self.assertEqual(app.query_one('#repository-filter', Select).value, ticket['project'])
                    self.assertIn(ticket_key(ticket), app.visible_issue_ids)
            app.store.db.close()
