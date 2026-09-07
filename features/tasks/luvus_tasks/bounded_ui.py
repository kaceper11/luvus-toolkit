"""Forms for fixed workflow recipes and reviewed PR-feedback runs."""
import copy
import json
import time
from pathlib import Path
import uuid

from . import bounded, forge, workflow
from .core import TaskError
from .workflow_ui import select, show, repair_connection


def fields(value, commands=None):
    result = [('name', 'Recipe name', value['name'], 'input'),
              ('instructions', 'Agent instructions', value['instructions'], 'text'),
              ('repairs', 'Additional repair attempts (0–10)', str(value['repairs']), 'input'),
              ('step_minutes', 'Agent-step deadline (minutes)', str(value['step_minutes']), 'input'),
              ('total_minutes', 'Total deadline (minutes)', str(value['total_minutes']), 'input')]
    if commands is None:
        result.append(('checks_text', 'Validation command IDs (one per line; selected again at launch)', '\n'.join(value['checks']), 'text'))
    else:
        result += [('check_' + str(i), c['title'] + ' · ' + c['id'], c['id'] in value['checks'], 'bool') for i, c in enumerate(commands)]
    result += [(key, label, value[key], 'bool') for key, label in [('commit', 'Commit verified changes'), ('push', 'Push to this PR source branch'),
                ('reply', 'Publish drafted replies after changes are published'), ('resolve', 'Resolve addressed review threads after push and replies')]]
    return result


def parse(values, commands=None):
    data = {k: values[k] for k in bounded.DEFAULT if k != 'checks'}
    try:
        for k in ('repairs', 'step_minutes', 'total_minutes'):
            data[k] = int(data[k])
    except ValueError as exc:
        raise TaskError('Limits must be whole numbers.') from exc
    data['checks'] = [x.strip() for x in values['checks_text'].splitlines() if x.strip()] if commands is None else [c['id'] for i, c in enumerate(commands) if values['check_' + str(i)]]
    if commands is not None and not data['checks']:
        raise TaskError('No checks selected. Select at least one validation command.')
    return bounded.recipe(data)


async def edit(app, value, commands=None, title='Edit workflow recipe'):
    current = fields(value, commands)
    error = ''
    while True:
        result = await app.form(title, current,
            message=error or 'Sequence: address feedback → checks → bounded repair → permitted publication. These permissions govern the runner, not an OS sandbox.', submit='Review' if commands is not None else 'Save')
        if result is None:
            return None
        try:
            return parse(result, commands)
        except TaskError as exc:
            error = str(exc)
            current = [(key, label, result[key], kind) for key, label, _, kind in current]


async def configure(app):
    items = bounded.recipes(app.store)
    chosen = await app.choice('Workflow recipes', [('New recipe', 'new'), ('Finished-run history limit', 'retention')] + [(r['name'], key) for key, r in items.items()])
    if chosen is None:
        return
    if chosen == 'retention':
        result = await app.form('Workflow history', [('limit', 'Finished runs to retain (1–5000)', str(app.store.preference('workflow-history-limit', 200)), 'input')], message='Oldest completed/cancelled runs and their workflow evidence are removed. Active and paused runs are retained.', submit='Save')
        if result is not None:
            try:
                keep = int(result['limit'])
            except ValueError as exc:
                raise TaskError('History limit must be a whole number.') from exc
            if not 1 <= keep <= 5000:
                raise TaskError('History limit must be between 1 and 5000.')
            with bounded.lock(app.store):
                app.store.set_preference('workflow-history-limit', keep)
                bounded.prune(app.store)
        return
    old = items.get(chosen)
    operation = 'edit' if chosen == 'new' else await app.choice(old['name'], [('Edit', 'edit'), ('Duplicate', 'duplicate'), ('Delete', 'delete')])
    if not operation:
        return
    if operation == 'delete':
        if await app.confirm('Delete recipe', 'Existing approved runs retain their own recipe snapshot.'):
            bounded.save_recipe(app.store, chosen, None, old)
        return
    key = uuid.uuid4().hex if chosen == 'new' or operation == 'duplicate' else chosen
    value = copy.deepcopy(old or bounded.DEFAULT)
    if operation == 'duplicate':
        value['name'] += ' copy'
    edited = await edit(app, value)
    if edited is not None:
        bounded.save_recipe(app.store, key, edited, old if key == chosen else None)


def preflight(store, host, record):
    from .operations import session_key
    from . import MODULE_ID
    live = bounded.session(host)
    helper = store.preference('workflow-helper:' + session_key(), {})
    info = host.call('module.info', id=MODULE_ID)
    if not info.get('enabled') or not info.get('runnable') or helper.get('generation') != live['generation'] or not 0 <= time.time() - helper.get('at', 0) <= 120:
        raise TaskError('Tasks helper is unavailable or has not checked in recently. Reload the Tasks module in this session and try again.')
    contract = workflow.bridge(store, 'project-commands', 'describe', worktree=record['target']['path'])
    if not {'run', 'status', 'evidence'} <= set(contract.get('actions', [])):
        raise TaskError('Project Commands bridge needs the evidence API. Update Project Commands and review its integration connection.')
    return 'Tasks helper: recently active in this session\nProject Commands: compatible evidence API'


def approval(prepared, readiness):
    r, settings = prepared['record'], prepared['recipe']
    lines = [readiness, 'PR: ' + prepared['pr']['url'], 'Checkout: ' + r['target']['path'], 'HEAD: ' + prepared['head'],
             'Worker: ' + r['name'], 'Recipe: ' + settings['name'], 'Sequence: address feedback → checks → repair → publication',
             'Checks: ' + ', '.join(c['title'] for c in prepared['commands']),
             f"Limits: {settings['repairs']} additional repairs; {settings['step_minutes']} min per agent step; {settings['total_minutes']} min total",
             'Permissions: ' + ', '.join(k + (' allowed' if settings[k] else ' off') for k in ('commit', 'push', 'reply', 'resolve')),
             'Instructions: ' + settings['instructions'], 'Selected feedback:']
    for f in prepared['feedback']:
        lines.append('• ' + (f.get('path') or f['id']))
        lines.extend('  ' + c.get('author', 'Reviewer') + ': ' + c['text'] for c in f['comments'])
    return '\n'.join(lines)


async def start(app, record):
    if not record.get('target'):
        raise TaskError('Launch/resume a handover before starting its workflow.')
    readiness = await app.io(lambda s: preflight(s, app.host, record))
    if not await repair_connection(app, record):
        return
    if not record.get('pr_id'):
        prs = await app.io(lambda s: forge.client(s, record).list(record['target']['branch']))
        pr = await select(app, 'Choose PR', prs)
        if not pr:
            return
        record = copy.deepcopy(record); record['pr_id'] = pr['id']
    feedback = await app.io(lambda s: forge.client(s, record).workflow_feedback(record['pr_id']))
    feedback = [f for f in feedback if not f['resolved'] and not f.get('outdated')]
    if not feedback:
        raise TaskError('No current unresolved feedback. Refresh the PR or inspect outdated threads manually.')
    if len(feedback) > 100:
        raise TaskError('More than 100 feedback items; narrow the PR feedback before running.')
    pick_fields = [('thread_' + str(i), (f.get('path') or f['id']) + ' · ' + ' / '.join(c['text'][:180] for c in f['comments']), False, 'bool') for i, f in enumerate(feedback)]
    selected = await app.form('Select feedback to address', pick_fields, message='Only selected items enter this run. New comments need a new review.', submit='Choose recipe')
    if selected is None:
        return
    ids = [f['id'] for i, f in enumerate(feedback) if selected['thread_' + str(i)]]
    if not ids:
        raise TaskError('Select at least one feedback item.')
    items = bounded.recipes(app.store)
    if not items:
        raise TaskError('Create a workflow recipe in Configuration first.')
    default_key = 'workflow-default:' + str(Path(record['target']['repo']).resolve())
    default = app.store.preference(default_key)
    options = sorted(items, key=lambda k: k != default)
    key = await app.choice('Workflow recipe', [(items[k]['name'] + (' · repository default' if k == default else ''), k) for k in options])
    if key is None:
        return
    commands = await app.io(lambda s: workflow.commands(s, record))
    commands = [c for c in commands if c['kind'] == 'validation']
    settings = await edit(app, items[key], commands, 'Configure this run')
    if settings is None:
        return
    prepared = await app.io(lambda s: bounded.prepare(s, app.host, record, settings, ids))
    text = approval(prepared, readiness)
    result = await app.form('Approve bounded workflow', [('remember', 'Use this recipe as this repository default', key == default, 'bool')],
                            message=text, submit='Start approved run')
    if result is None:
        return
    await app.io(lambda s: preflight(s, app.host, record))
    run = await app.io(lambda s: bounded.start(s, app.host, prepared))
    if result['remember']:
        app.store.set_preference(default_key, key)
    await show(app, 'Workflow queued', run['id'] + '\nContinues through the session-owned Tasks helper. Inspect status in Workflow runs. No helper means no dispatch.')
    app.paint_work()


async def manage(app, record=None, run_id=None):
    run = bounded.get(app.store, run_id) if run_id else None
    offset = 0
    while run is None:
        items = bounded.runs(app.store, limit=51, offset=offset, record=record['id'] if record else None)
        options = [(r['recipe']['name'] + ' · ' + r['state'] + ' · ' + r['phase'] + ' · ' + r['record']['target']['branch'], r['id']) for r in items[:50]]
        if offset: options.append(('Previous page', 'previous'))
        if len(items) > 50: options.append(('Next page', 'next'))
        chosen = await app.choice('Workflow runs', options)
        if chosen is None:
            return
        if chosen in ('previous', 'next'):
            offset += -50 if chosen == 'previous' else 50
        else:
            run = bounded.get(app.store, chosen)
    while True:
        run = bounded.get(app.store, run['id'])
        options = [('Details / journal / draft replies', 'details'), ('Refresh', 'refresh'), ('Open worker', 'worker')]
        if run['state'] not in bounded.TERMINAL:
            options += [('Resume and reconcile', 'resume')] if run['state'] == 'paused' else [('Pause subsequent steps', 'pause')]
            options += [('Cancel subsequent steps', 'cancel')]
        action = await app.choice(run['state'] + ' · ' + run['phase'] + '\n' + run.get('reason', ''), options)
        if action is None:
            return
        if action == 'details':
            await show(app, 'Workflow details', run)
        elif action == 'worker':
            await app.io(lambda s: bounded.worker(app.host, run['record']))
            await app.io(lambda s: app.host.call('pane.focus', pane=run['record']['pane']))
        elif action in ('resume', 'pause', 'cancel'):
            if await app.confirm(action.title() + ' workflow', 'Already-running work is not stopped. Resume reconciles the existing attempt and retains the original deadline and permissions.'):
                await app.io(lambda s: bounded.control(s, app.host, run['id'], action))
