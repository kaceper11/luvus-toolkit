"""Local checkout identity and badges for reviewed delivery."""
import os
from pathlib import Path
import subprocess


def observe(path):
    path = Path(path).resolve(strict=True)
    def git(*args):
        r = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=10,
                           env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        return r.stdout.strip() if r.returncode == 0 else ''
    root = git('rev-parse', '--show-toplevel')
    if not root:
        stat = path.stat()
        return {'identity': [str(path), stat.st_dev, stat.st_ino, 'directory'], 'badge': 'outside Git'}
    directory = Path(git('rev-parse', '--absolute-git-dir')).resolve(strict=True)
    stat = directory.stat()
    branch = git('symbolic-ref', '-q', 'HEAD')
    head = git('rev-parse', '--verify', 'HEAD')
    r = subprocess.run(['git', '-C', str(path), 'status', '--porcelain=v1', '-z'], capture_output=True, timeout=10,
                       env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    if r.returncode:
        raise ValueError('Checkout status unavailable. Refresh before sending.')
    staged = len(list(filter(None, git('diff', '--cached', '--name-only', '-z').split('\0'))))
    conflicts = len(list(filter(None, git('diff', '--name-only', '--diff-filter=U', '-z').split('\0'))))
    counts = git('rev-list', '--left-right', '--count', 'HEAD...@{upstream}').split()
    sync = f'↑{counts[0]} ↓{counts[1]} (local refs)' if len(counts) == 2 else 'no upstream / unknown'
    label = branch.removeprefix('refs/heads/') or ('detached ' + head[:8] if head else 'unborn HEAD')
    return {'identity': [str(Path(root).resolve()), str(directory), stat.st_dev, stat.st_ino, branch, head if not branch else None],
            'badge': label + ' · ' + ('dirty' if r.stdout else 'clean') + f' · {staged} staged · {conflicts} conflicts · ' + sync}


def review(record):
    paths = {str(Path(x).resolve()) for x in (record['cwd'], (record.get('target') or {}).get('cwd', record['cwd']))}
    return {path: observe(path) for path in paths}


def validate(record):
    approved = record.get('reviewed_checkouts')
    if not approved:
        raise ValueError('Review the source and recipient checkouts before sending.')
    paths = {str(Path(x).resolve()) for x in (record['cwd'], (record.get('target') or {}).get('cwd', record['cwd']))}
    if paths != set(approved):
        raise ValueError('Source or recipient checkout changed. Review again.')
    for path in paths:
        if observe(path)['identity'] != approved[path]['identity']:
            raise ValueError('Checkout or branch changed since review. Refresh and review again.')
