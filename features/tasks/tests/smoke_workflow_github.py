"""Opt-in: create/close a draft test PR and delete its unique branch in --repo."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luvus_tasks.forge import GitHubPR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--confirm-test-writes', action='store_true', required=True)
    args = parser.parse_args()
    def gh(endpoint, data=None):
        cmd = ['gh', 'api', endpoint]
        if data is not None: cmd += ['--method', 'POST', '--input', '-']
        r = subprocess.run(cmd, input=json.dumps(data) if data is not None else None, text=True, capture_output=True, check=True, timeout=60)
        return json.loads(r.stdout)
    account = gh('user')['login']
    meta = gh('repos/' + args.repo)
    if not meta['private'] or meta['owner']['login'] != account:
        raise RuntimeError('Use a private repository owned by the authenticated test account.')
    prefix = 'repos/' + args.repo; branch = 'workflow-smoke-' + uuid.uuid4().hex[:12]; pr = None; pushed = False
    p = GitHubPR({'repository': args.repo, 'account': account}, args.repo)
    with tempfile.TemporaryDirectory(prefix='workflow-gh-') as folder:
        root = Path(folder) / 'repo'
        def git(*argv):
            return subprocess.run(['git', '-C', str(root), *argv], check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        subprocess.run(['git', 'clone', '--depth', '1', meta['clone_url'], str(root)], check=True, capture_output=True, timeout=60)
        try:
            git('switch', '-c', branch); git('config', 'user.name', 'Luvus workflow smoke test'); git('config', 'user.email', account + '@users.noreply.github.com')
            (root / 'workflow-smoke.txt').write_text('Disposable workflow provider test.\n')
            git('add', 'workflow-smoke.txt'); git('commit', '-m', 'Test bounded workflow provider operations')
            git('push', 'origin', 'HEAD:refs/heads/' + branch); pushed = True
            pr = gh(prefix + '/pulls', {'title': 'Disposable bounded workflow smoke test', 'head': branch, 'base': meta['default_branch'], 'draft': True, 'body': 'Tests feedback read, reply, reconciliation, and resolution. Closed after the test.'})
            print('Test PR:', pr['html_url'], flush=True)
            gh(prefix + f"/pulls/{pr['number']}/comments", {'body': 'Please verify this test-only line.', 'commit_id': git('rev-parse', 'HEAD'), 'path': 'workflow-smoke.txt', 'line': 1, 'side': 'RIGHT'})
            item = next(f for f in p.workflow_feedback(pr['number']) if f['resolvable'])
            marker = '<!-- luvus-workflow:' + branch + ' -->'
            p.workflow_reply(pr['number'], item, 'Verified the disposable line.\n\n' + marker)
            assert p.workflow_find_reply(pr['number'], item, marker)
            fresh = next(f for f in p.workflow_feedback(pr['number']) if f['id'] == item['id'])
            assert fresh['signature'] == item['signature']
            p.workflow_resolve(pr['number'], item)
            assert next(f for f in p.workflow_feedback(pr['number']) if f['id'] == item['id'])['resolved']
            print('PASS: GitHub thread read, reply/readback, input signature, resolution/readback', flush=True)
        finally:
            if pr:
                subprocess.run(['gh','api', prefix + '/pulls/' + str(pr['number']), '--method','PATCH','--input','-'], input='{"state":"closed"}', text=True, check=True, capture_output=True, timeout=60)
            if pushed:
                git('push', 'origin', '--delete', branch)


if __name__ == '__main__': main()
