import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from luvus_tasks import bounded as b, forge, workflow
from luvus_tasks.core import Store, TaskError, default_config
from luvus_tasks.handover import save_record


class Host:
    def __init__(self, record): self.record, self.sent, self.status = record, [], 'done'
    def call(self, method, **params):
        if method == 'terminal.backend.inventory': return {'server_generation': 'g'}
        if method == 'agent.prompt': self.sent.append(params['text']); return {}
        return {}
    def validate_terminal(self, record):
        if record['terminal_id'] != 't': raise TaskError('wrong terminal')
    def agents(self):
        r = self.record
        return [{'terminal_id': 't', 'generation': 'g', 'pane': 1, 'name': r['name'], 'agent': 'codex', 'cwd': r['target']['path'], 'status': self.status}]


class Forge:
    def __init__(self, head):
        self.pr = {'id': '1', 'head': head, 'base': head, 'head_branch': 'feature', 'base_branch': 'main', 'state': 'open', 'url': 'https://github.com/owner/repo/pull/1'}
        self.feedback = [forge.feedback_item('thread:a', 'a', [{'id': '1', 'text': 'Fix it', 'author': 'reviewer'}])]
        self.feedback[0]['reply_to'] = '1'
        self.replies = []; self.resolutions = []
    def get(self, ident): return copy.deepcopy(self.pr)
    def workflow_feedback(self, ident): return copy.deepcopy(self.feedback)
    def workflow_find_reply(self, ident, item, marker): return next((str(i + 1) for i, x in enumerate(self.replies) if marker in x), None)
    def workflow_reply(self, ident, item, body): self.replies.append(body)
    def workflow_resolve(self, ident, item): self.feedback[0]['resolved'] = True; self.resolutions.append(item['id'])


class BoundedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve(); self.repo = self.root / 'repo'; self.repo.mkdir()
        self.env = patch.dict(os.environ, {'LUVUS_SOCKET_PATH': '/test/socket', 'LUVUS_PANE_ID': '1', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'commit.gpgsign', 'GIT_CONFIG_VALUE_0': 'false',
            'GIT_CONFIG_KEY_1': 'core.hooksPath', 'GIT_CONFIG_VALUE_1': str(self.root / 'no-hooks')}); self.env.start()
        b.git(self.repo, 'init', '-b', 'feature'); b.git(self.repo, 'config', 'user.email', 'test@example.com'); b.git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'a.txt').write_text('before'); b.git(self.repo, 'add', '.'); b.git(self.repo, 'commit', '-m', 'Initial')
        self.remote = self.root / 'remote.git'; subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        b.git(self.repo, 'remote', 'add', 'origin', str(self.remote)); b.git(self.repo, 'push', 'origin', 'feature')
        self.store = Store(self.root / 'state')
        self.record = {'id': 'record', 'name': 'worker', 'agent': 'codex', 'pane': 1, 'terminal_id': 't', 'generation': 'g', 'stage': 'delivered',
            'target': {'path': str(self.repo), 'repo': str(self.repo), 'branch': 'feature'}, 'pr_id': '1', 'forge': {'kind': 'github', 'repository': 'owner/repo', 'connection': 'gh'},
            'ticket': {'id': '1', 'key': 'owner/repo#1', 'connection': 'gh', 'title': 'Fix'}}
        save_record(self.store, self.record); self.host = Host(self.record); self.forge = Forge(b.git(self.repo, 'rev-parse', 'HEAD'))
        self.client = patch.object(forge, 'client', return_value=self.forge); self.client.start()
        self.definition = {'id': 'tests', 'title': 'Tests', 'kind': 'validation', 'producer_definition': {'id': 'tests', 'argv': ['test']}}
        self.commands = patch.object(workflow, 'commands', return_value=[self.definition]); self.commands.start()
        self.requests = []; self.states = ['passed']; self.command_results = {}
        self.bridge = patch.object(workflow, 'bridge', side_effect=self.command); self.bridge.start()
    def tearDown(self):
        self.bridge.stop(); self.commands.stop(); self.client.stop(); self.env.stop(); self.store.db.close(); self.tmp.cleanup()
    def command(self, store, owner, method, **kw):
        key = kw['request_id']
        if method == 'run':
            self.requests.append(key)
            state = self.states[min(len(self.requests) - 1, len(self.states) - 1)]
            self.command_results[key] = {'request_id': key, 'run_id': 'run-' + key, 'state': state, 'freshness': 'current', 'worktree': str(self.repo), 'source': {'error': 'failure' if state == 'failed' else ''}}
        return copy.deepcopy(self.command_results[key])
    def begin(self, **settings):
        prepared = b.prepare(self.store, self.host, self.record, {**b.DEFAULT, 'checks': ['tests'], **settings}, ['thread:a'])
        r = b.start(self.store, self.host, prepared); self.ident = r['id']; return r
    def tick(self): b.tick(self.store, self.host, 'owner'); return b.get(self.store, self.ident)
    def result(self, outcome='addressed', files=None):
        r = b.get(self.store, self.ident)
        data = {'version': 1, 'attempt': r['attempt'], 'token': r['token'], 'files': b.changed(self.repo) if files is None else files,
                'outcomes': [{'id': 'thread:a', 'outcome': outcome, 'reply': 'Implemented fix.'}]}
        return b.report(self.store, self.host, r['id'], data)
    def finish(self):
        for _ in range(12):
            r = self.tick()
            if r['state'] != 'running': return r
        self.fail('Did not terminate')
    def test_deadline_expiring_during_publication_prevents_commit(self):
        self.begin(commit=True); self.tick()
        (self.repo / 'a.txt').write_text('fixed'); self.result()
        while b.get(self.store, self.ident)['phase'] != 'publish': self.tick()
        head = b.git(self.repo, 'rev-parse', 'HEAD')
        real_git = b.git
        clock = [time.time()]
        def delayed_git(root, *args):
            value = real_git(root, *args)
            if args == ('write-tree',): clock[0] += 100000
            return value
        with patch.object(b, 'git', side_effect=delayed_git), patch.object(b.time, 'time', side_effect=lambda: clock[0]):
            r = self.tick()
        self.assertEqual(r['state'], 'paused')
        self.assertIn('deadline', r['reason'])
        self.assertEqual(b.git(self.repo, 'rev-parse', 'HEAD'), head)
        self.assertFalse(self.forge.replies)

    def test_waiting_defers_remote_reads_until_report(self):
        self.begin(); self.tick()
        with patch.object(self.forge, 'workflow_feedback', wraps=self.forge.workflow_feedback) as read:
            self.tick(); self.tick()
            self.assertEqual(read.call_count, 0)
            self.result(); self.tick()
            self.assertEqual(read.call_count, 1)

    def test_retention_keeps_active_and_paused_and_removes_owned_evidence(self):
        active = self.begin()
        self.store.set_preference('workflow-history-limit', 2)
        paused = {**copy.deepcopy(active), 'id': 'paused', 'state': 'paused'}; b.save(self.store, paused)
        for i in range(4):
            r = {**copy.deepcopy(active), 'id': 'finished-' + str(i), 'state': 'completed'}
            self.store.observe('check-' + r['id'], self.record['id'], {'kind': 'validation', 'workflow_run': r['id'], 'state': 'passed'})
            b.save(self.store, r)
        self.assertEqual({r['id'] for r in b.runs(self.store)}, {active['id'], 'paused', 'finished-2', 'finished-3'})
        self.assertEqual({r['id'] for r in b.runs(self.store, active=True)}, {active['id'], 'paused'})
        self.assertEqual(len(b.runs(self.store, limit=1, offset=1)), 1)
        self.assertFalse(any(e.get('workflow_run') == 'finished-0' for e in self.store.evidence()))
        self.assertFalse(any(x['source'] == 'check-finished-0' for x in self.store.timeline(self.record['id'])))

    def test_running_attention_and_preflight(self):
        from luvus_tasks import attention, bounded_ui
        from luvus_tasks.operations import session_key
        r = self.begin(); self.host.status = 'idle'
        items = attention.project(self.store, self.host.agents())
        item = next(x for x in items if x['source'] == 'workflow')
        self.assertEqual(item['target']['workflow'], r['id'])
        self.assertIn('attempt 1', item['title'])
        with self.assertRaises(TaskError): bounded_ui.preflight(self.store, self.host, self.record)
        self.store.set_preference('workflow-helper:' + session_key(), {'at': time.time(), 'generation': 'g'})
        original = self.host.call
        with patch.object(self.host, 'call', side_effect=lambda method, **kw: {'enabled': True, 'runnable': True} if method == 'module.info' else original(method, **kw)), patch.object(workflow, 'bridge', return_value={'actions': ['run', 'status', 'evidence']}):
            self.assertIn('compatible', bounded_ui.preflight(self.store, self.host, self.record))
        values = {k: v for k, _, v, _ in bounded_ui.fields(b.DEFAULT, [self.definition])}
        with self.assertRaisesRegex(TaskError, 'No checks selected'):
            bounded_ui.parse(values, [self.definition])
        self.assertIn('Permissions: commit off', bounded_ui.approval(r, 'Ready'))
        failures = [{'command': 'tests', 'state': 'failed', 'diagnostic': 'start\n' * 6000 + 'FAIL final assertion'}]
        context = b.failure_context(failures)
        self.assertLess(len(context), 24000)
        self.assertIn('FAIL final assertion', context)
        self.assertLess(len(b.failure_context(failures * 20)), 24000)

    def test_local_run_needs_report_and_keeps_drafts(self):
        self.begin(); self.tick(); self.tick()
        self.assertEqual(len(self.host.sent), 1)
        self.assertEqual(b.get(self.store, self.ident)['phase'], 'wait-agent')
        (self.repo/'a.txt').write_text('fixed'); self.result()
        r = self.finish(); self.assertEqual(r['state'], 'completed'); self.assertEqual(len(self.requests), 1)
        self.assertTrue(b.changed(self.repo)); self.assertFalse(self.forge.replies)
    def test_limits_recipe_concurrency_and_review_freshness(self):
        for data in [{'repairs': True}, {'push': True}, {'resolve': True}, {'step_minutes': 0}, {'checks': ['a', 'a']}]:
            with self.assertRaises(TaskError): b.recipe(data)
        prepared = b.prepare(self.store, self.host, self.record, {**b.DEFAULT, 'checks':['tests']}, ['thread:a'])
        self.forge.feedback[0]['comments'][0]['text'] = 'changed'
        with self.assertRaises(TaskError): b.start(self.store, self.host, prepared)
        self.begin()
        with self.assertRaises(TaskError): self.begin()
    def test_wrong_report_and_unreported_edits(self):
        self.begin(); self.tick(); (self.repo/'a.txt').write_text('fixed')
        with self.assertRaises(TaskError): self.result(files=[])
        with patch.dict(os.environ, {'LUVUS_PANE_ID': '99'}):
            with self.assertRaises(TaskError): self.result()
        self.result(); (self.repo/'a.txt').write_text('later')
        self.assertEqual(self.tick()['state'], 'paused')
    def test_failure_repair_and_repeated_failure_bound(self):
        self.states=['failed']; self.begin(); self.tick(); self.result()
        self.tick(); self.tick(); self.tick(); r=self.tick()
        self.assertEqual(r['attempt'],1); self.result(); r=self.finish()
        self.assertEqual(r['state'],'paused'); self.assertIn('Repeated',r['reason']); self.assertEqual(len(self.host.sent),2)
    def test_repair_zero_and_disagreement(self):
        self.states=['failed']; self.begin(repairs=0);self.tick();self.result();r=self.finish()
        self.assertEqual(r['attempt'],0); self.assertEqual(r['state'],'paused')
    def test_uncertain_prompt_not_replayed(self):
        self.begin(); original=self.host.call
        def call(method,**kw):
            if method=='agent.prompt': self.host.sent.append(kw['text']); raise TaskError('lost reply')
            return original(method,**kw)
        with patch.object(self.host,'call',side_effect=call): r=self.tick()
        self.assertEqual(r['state'],'paused')
        with self.assertRaises(TaskError): b.control(self.store,self.host,self.ident,'resume')
        self.result();b.control(self.store,self.host,self.ident,'resume');self.finish()
        self.assertEqual(len(self.host.sent),1)
    def test_crash_owner_pause_and_cancel(self):
        self.begin();self.tick(); b.tick(self.store,self.host,'new-owner')
        self.assertEqual(b.get(self.store,self.ident)['state'],'paused')
        b.control(self.store,self.host,self.ident,'cancel');self.tick()
        self.assertEqual(len(self.host.sent),1)
    def test_deadline_stops_dispatch(self):
        r=self.begin();r['deadline']=time.time()-1;b.save(self.store,r)
        self.assertEqual(self.tick()['state'],'paused');self.assertFalse(self.host.sent)
    def test_publish_commit_push_reply_resolve(self):
        original=self.forge.get
        def get(ident):
            p=original(ident);p['head']=b.git(self.repo,'ls-remote',str(self.remote),'refs/heads/feature').split()[0];return p
        with patch.object(self.forge,'get',side_effect=get):
            self.begin(commit=True,push=True,reply=True,resolve=True);self.tick();(self.repo/'a.txt').write_text('fixed');self.result();r=self.finish()
        self.assertEqual(r['state'],'completed',r.get('reason'));self.assertFalse(b.changed(self.repo))
        self.assertEqual(len(self.forge.replies),1);self.assertEqual(len(self.forge.resolutions),1)
        self.assertEqual(b.git(self.repo,'rev-parse','HEAD'),b.git(self.repo,'ls-remote',str(self.remote),'refs/heads/feature').split()[0])
    def test_remote_head_change_blocks_dispatch(self):
        self.begin();self.forge.pr['head']='other';r=self.tick();self.assertEqual(r['state'],'paused');self.assertFalse(self.host.sent)
    def test_uncertain_command_reconciles_same_request(self):
        self.begin();self.tick();self.result();self.tick();original=self.command
        def lost(*a,**kw):
            value=original(*a,**kw)
            if a[2]=='run':raise TaskError('lost command reply')
            return value
        with patch.object(workflow,'bridge',side_effect=lost): self.tick()
        b.control(self.store,self.host,self.ident,'resume');self.finish();self.assertEqual(len(self.requests),1)
    def test_resolution_or_content_change_pauses(self):
        self.begin(); self.tick(); self.result()
        self.forge.feedback[0]['resolved'] = True
        self.assertEqual(self.tick()['state'], 'paused')

    def test_reply_loss_reconciles_without_duplicate(self):
        self.begin(reply=True); self.tick(); self.result()
        original = self.forge.workflow_reply
        def lost(*args): original(*args); raise TaskError('lost reply acknowledgement')
        with patch.object(self.forge, 'workflow_reply', side_effect=lost): r = self.finish()
        self.assertEqual(r['state'], 'paused'); self.assertEqual(len(self.forge.replies), 1)
        b.control(self.store, self.host, self.ident, 'resume'); r = self.finish()
        self.assertEqual(r['state'], 'completed'); self.assertEqual(len(self.forge.replies), 1)

    def test_stale_producer_evidence_and_competing_followup(self):
        from luvus_tasks.operations import followup
        self.begin(); self.tick()
        with self.assertRaisesRegex(TaskError, 'reserved'):
            followup(self.store, self.host, self.record, 'other work')
        self.result(); self.tick()
        original = self.command
        def stale(*args, **kwargs): return {**original(*args, **kwargs), 'freshness': 'stale'}
        with patch.object(workflow, 'bridge', side_effect=stale): r = self.tick()
        self.assertEqual(r['state'], 'paused')

    def test_commit_response_loss_reconciles_tree_without_recommit(self):
        self.begin(commit=True); self.tick(); (self.repo/'a.txt').write_text('fixed'); self.result()
        original = b.git
        def lost(root, *args):
            value = original(root, *args)
            if args[0] == 'commit': raise TaskError('lost commit result')
            return value
        with patch.object(b, 'git', side_effect=lost): r = self.finish()
        self.assertEqual(r['state'], 'paused')
        b.control(self.store, self.host, self.ident, 'resume'); r = self.finish()
        self.assertEqual(r['state'], 'completed'); self.assertEqual(b.git(self.repo, 'rev-list', '--count', 'HEAD'), '2')

    def test_recipe_edit_isolated_from_run(self):
        self.begin();old=b.recipes(self.store)['default'];b.save_recipe(self.store,'default',{**old,'repairs':0},old)
        self.assertEqual(b.get(self.store,self.ident)['recipe']['repairs'],2)
        with self.assertRaises(TaskError):b.save_recipe(self.store,'default',old,old)


class ProviderTests(unittest.TestCase):
    def test_github_grouping_discussion_and_own_marker(self):
        gh=object.__new__(forge.GitHubPR)
        comments=[{'id':1,'thread':'T','body':'fix','user':{'login':'r'},'state':'comment','path':'a','line':2},
                  {'id':2,'thread':'T','in_reply_to_id':1,'body':'own <!-- luvus-workflow:x -->','state':'comment'}]
        with patch.object(gh,'review_comments',return_value=comments),patch.object(gh,'api',return_value=[]):
            value=gh.workflow_feedback('1')[0];self.assertEqual(len(value['comments']),1);self.assertEqual(value['reply_to'],'1')
        with patch.object(gh,'api',return_value={'id':3}) as api:
            gh.workflow_reply('1',value,'reply');self.assertEqual(api.call_args.args[0],'/pulls/1/comments/1/replies')
    def test_azure_feedback_and_mutations(self):
        az=object.__new__(forge.AzurePR);az.git='git/repositories/repo'
        with patch.object(az,'request',return_value={'value':[{'id':4,'status':'active','comments':[{'id':1,'content':'fix','commentType':'text'}]}]}) as req:
            f=az.workflow_feedback('2')[0];self.assertFalse(f['resolved'])
            az.workflow_reply('2',f,'reply');self.assertEqual(req.call_args.kwargs['method'],'POST')
            az.workflow_resolve('2',f);self.assertEqual(req.call_args.kwargs['data'],{'status':'fixed'})


if __name__ == '__main__': unittest.main()
