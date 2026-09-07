"""Controlled old-vs-new search path benchmark; isolated Git/SQLite, no host calls.

The baseline reconstructs the previous synchronous attention refresh on each search,
including per-handover hashing. Both paths use today's same table renderer, so this
does not claim a whole-release or desktop benchmark.
"""
import asyncio
import inspect
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

from textual.widgets import Input
from luvus_tasks import attention
from luvus_tasks.console import Cockpit
from luvus_tasks.handover import save_record
from test_console import Host, TICKET, CONNECTION


async def main():
    with tempfile.TemporaryDirectory() as folder:
        repo = Path(folder) / 'repo'
        repo.mkdir()
        def git(*args):
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        git('init')
        (repo / 'source.txt').write_text('source\n' * 20000)
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'Fixture')
        app = Cockpit(str(Path(folder) / 'state'), Host(), network=False)
        config = app.store.config()
        config['connections'] = [CONNECTION]
        app.store.save_config(config)
        for i in range(100):
            record = {'id': str(i), 'ticket': TICKET, 'name': 'task-' + str(i), 'stage': 'delivered', 'context': [], 'created': '2026-01-01',
                      'target': {'repo': str(repo), 'path': str(repo), 'branch': 'feature'}, 'prompt': ''}
            save_record(app.store, record)
            app.store.observe('pr:' + str(i), str(i), {'kind': 'pr', 'title': 'PR', 'state': 'observed', 'snapshot': {'state': 'closed'}})
        source = inspect.getsource(attention.project).replace('if path not in fingerprints:', 'if True:')
        namespace = dict(attention.__dict__)
        exec(compile(source, '<prechange-attention-path>', 'exec'), namespace)
        baseline = namespace['project']
        try:
            async with app.run_test(size=(120, 45)) as pilot:
                await pilot.pause()
                app.tasks = [{**TICKET, 'id': str(i), 'key': 'AB#' + str(i), 'title': 'Task ' + str(i)} for i in range(1000)]
                search = app.query_one('#search', Input)
                results = {}
                for label in ('previous_search_path', 'cached_search_path'):
                    samples = []
                    for query in ('Task 1', 'Task 2', 'Task 3', 'Task 4', 'Task 5'):
                        start = time.perf_counter()
                        with search.prevent(Input.Changed):
                            search.value = query
                        if label == 'previous_search_path':
                            app.attention_items = baseline(app.store, [], [], True)
                        app.paint(False)
                        samples.append((time.perf_counter() - start) * 1000)
                    results[label] = {'median_ms': round(statistics.median(samples), 2), 'samples_ms': [round(x, 2) for x in samples]}
                print(json.dumps({'issues': 1000, 'handovers': 100, 'source_bytes': (repo / 'source.txt').stat().st_size,
                                  'comparison': 'controlled reconstructed search path, not desktop latency', 'results': results}, indent=2))
        finally:
            app.store.db.close()


if __name__ == '__main__':
    asyncio.run(main())
