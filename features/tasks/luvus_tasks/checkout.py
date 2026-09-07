"""Local checkout observations; display snapshots never authorize a write."""
import os
from pathlib import Path
import subprocess

from .core import TaskError, now


def snapshot(path):
    def git(*args, optional=False):
        r = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True,
                           timeout=10, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        if r.returncode and not optional:
            raise TaskError('Checkout unavailable; refresh or choose its current location.')
        return r.stdout.strip() if not r.returncode else ''
    try:
        root = str(Path(git('rev-parse', '--show-toplevel')).resolve())
        directory = str(Path(git('rev-parse', '--absolute-git-dir')).resolve())
        branch = git('symbolic-ref', '-q', 'HEAD', optional=True)
        head = git('rev-parse', '--verify', 'HEAD', optional=True)
        changes = git('status', '--porcelain=v1', '-z')
        staged = len(list(filter(None, git('diff', '--cached', '--name-only', '-z').split('\0'))))
        conflicts = len(list(filter(None, git('diff', '--name-only', '--diff-filter=U', '-z').split('\0'))))
        upstream = git('rev-parse', '--abbrev-ref', '@{upstream}', optional=True)
        counts = git('rev-list', '--left-right', '--count', 'HEAD...@{upstream}', optional=True).split() if upstream else []
        stat = Path(directory).stat()
        return {'state': 'observed', 'root': root, 'gitdir': directory, 'directory_id': [stat.st_dev, stat.st_ino],
                'branch': branch, 'head': head, 'dirty': bool(changes), 'staged': staged, 'conflicts': conflicts, 'upstream': upstream,
                'ahead': int(counts[0]) if len(counts) == 2 else None,
                'behind': int(counts[1]) if len(counts) == 2 else None, 'at': now()}
    except (TaskError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'state': 'unknown', 'reason': str(exc), 'at': now()}


def identity(value):
    return [value.get(k) for k in ('root', 'gitdir', 'directory_id', 'branch')] + [value.get('head') if not value.get('branch') else None]


def badge(value):
    if not value or value.get('state') != 'observed':
        return 'checkout unknown'
    branch = value['branch'].removeprefix('refs/heads/') or ('detached ' + value['head'][:8] if value['head'] else 'unborn HEAD')
    sync = f"↑{value['ahead']} ↓{value['behind']} (local refs)" if value.get('ahead') is not None else 'no upstream' if not value.get('upstream') else 'upstream unknown'
    return branch + ' · ' + ('dirty' if value['dirty'] else 'clean') + f" · {value.get('staged', 0)} staged · {value.get('conflicts', 0)} conflicts · " + sync


def guard(record):
    target = record['target']
    fresh = snapshot(target['path'])
    if fresh.get('state') != 'observed' or fresh.get('root') != str(Path(target['path']).resolve()) or fresh.get('branch', '').removeprefix('refs/heads/') != target['branch']:
        raise TaskError('Checkout or branch changed. Refresh and reconcile the handover before sending.')
    approved = record.get('checkout_identity')
    if approved and identity(fresh) != approved:
        raise TaskError('Reviewed worktree identity changed. Reconcile the handover before sending.')
    return fresh
