"""Reviewed UI flows for task lifecycle, reuse, queue, and capture."""
import copy
import json
import uuid

from textual.widgets import Select, TabbedContent, Input, DataTable

from .core import TaskError, clean, now, ticket_key
from .handover import live_agent, save_record
from .providers import provider, github_repositories, lookup_candidates, GitHub
from . import productivity as tasks, operations as ops, workflow


async def choose_many(app, title, items, label):
    if not items:
        return []
    value = await app.form(title, [('item-' + str(i), label(item), False, 'bool') for i, item in enumerate(items)])
    return None if value is None else [item for i, item in enumerate(items) if value['item-' + str(i)]]


async def continue_task(app, ticket=None, record=None, new=False):
    ticket = ticket or record['ticket']
    if record is None and not new:
        matches = tasks.candidates(app.store, ticket)
        if len(matches) == 1:
            record = matches[0]
        elif matches:
            selected = await app.choice('Start / Continue', [(r['stage'] + ' · ' + (r.get('target') or r['inputs']).get('branch', '') + ' · ' + r['name'], r['id']) for r in matches] + [('New handover', 'new')])
            if selected is None:
                return
            record = next((r for r in matches if r['id'] == selected), None)
    if record:
        app.selected_handover = record['id']
        context = app.action_context.get()
        if context is not None:
            context['record'] = copy.deepcopy(record)
        if record['stage'] == 'draft' and not record.get('approved'):
            await app.wizard(record)
            return
        options = [('Inspect saved handover / recovery', 'inspect'), ('Open / Resume', 'resume'),
                   ('Prepare follow-up', 'follow-up'), ('New handover', 'new')]
        selected = await app.choice('Continue ' + ticket['key'], options)
        if selected == 'new':
            return await continue_task(app, ticket, new=True)
        if selected in ('resume', 'follow-up'):
            return await app.history_action(selected)
        if selected:
            app.query_one('#history-filter', Select).value = 'all'
            app.show_tab('handovers')
        return
    fresh = await app.io(lambda store: provider(ops.connection(store, ticket)).get(ticket['id']))
    record = await app.io(lambda store: ops.draft(store, fresh))
    # A configured target can go straight to the existing review; missing choices open the editor.
    if record['inputs']['repo'] and record['agent']:
        try:
            result = await app.io(lambda store: ops.preflight(store, app.host, record))
            record = result['record']
        except TaskError:
            await app.wizard(record)
            return
        await app.review_launch(record)
        if not record.get('approved'):
            await app.wizard(record)
    else:
        await app.wizard(record)
    if record.get('stage') == 'delivered' and any(tasks.identity(t) == tasks.identity(ticket) for t in tasks.queue(app.store)):
        if await app.confirm('Remove from Up next', ticket['key'] + ' was launched. Remove this queue entry?'):
            tasks.queue(app.store, ticket, 'remove')


async def pack_menu(app, editor):
    if not editor.flush():
        return
    record = editor.record
    repo = record['inputs']['repo']
    if not repo:
        raise TaskError('Choose a repository before using context packs.')
    action = await app.choice('Context packs', [(x, x) for x in ('Apply pack', 'Save selected context', 'Rename pack', 'Edit notes', 'Delete pack')])
    if not action:
        return
    available = tasks.packs(app.store, repo)
    if action == 'Save selected context':
        chosen = await choose_many(app, 'Select notes / references', record['context'], lambda x: x['label'])
        if not chosen:
            return
        value = await app.form('Save context pack', [('name', 'Pack name', '', 'input')])
        if value:
            name = value['name'].strip()
            if name in available and not await app.confirm('Replace pack', name):
                return
            tasks.save_pack(app.store, repo, name, chosen)
        return
    name = await app.choice('Repository context packs', [(n, n) for n in available])
    if name is None:
        return
    if action == 'Apply pack':
        if await app.confirm('Apply context pack', json.dumps(available[name], ensure_ascii=False, indent=2)):
            result = await app.io(lambda store: tasks.apply_pack(store, copy.deepcopy(record), name))
            editor.record.update(result)
            editor.paint_context()
            editor.refresh_prompt()
            editor.changed()
    elif action == 'Delete pack':
        if await app.confirm('Delete context pack', name + '\nExisting handovers keep their copied context.'):
            tasks.save_pack(app.store, repo, name, [], delete=True)
    elif action == 'Rename pack':
        value = await app.form('Rename context pack', [('name', 'Name', name, 'input')])
        if value:
            tasks.save_pack(app.store, repo, value['name'], available[name], old_name=name)
    else:
        entries = copy.deepcopy(available[name])
        value = await app.form('Edit pack notes', [('note-' + str(i), x['label'], x.get('text', ''), 'text') for i, x in enumerate(entries) if x['mode'] == 'text'],
                               message='Change references in a draft, then replace the pack using Save selected context.')
        if value:
            for i, x in enumerate(entries):
                if x['mode'] == 'text':
                    x['text'] = value['note-' + str(i)]
            tasks.save_pack(app.store, repo, name, entries)


async def next_phase(app, record):
    phase = await app.choice('Next phase', [(x, x) for x in ('Investigate', 'Plan', 'Implement', 'Review')])
    if not phase:
        return
    evidence = [e for e in app.store.evidence(record['id']) if e['kind'] != 'attention']
    for item in evidence:
        if item.get('checkout') and (record.get('target') or {}).get('path'):
            item['current_freshness'] = await app.io(lambda s, e=item: workflow.freshness(e, record['target']['path']))
    selected = await choose_many(app, 'Carry evidence into next phase', evidence, lambda x: x['title'] + ' · ' + x['state'])
    if selected is None:
        return
    config = app.store.config()
    text = config['presets'].get(phase, phase) + '\n\nAcceptance criteria:\n' + record['ticket'].get('acceptance', '')
    context = []
    for item in selected:
        if item.get('path'):
            context.append({'mode': 'attachment', 'target': item['path']})
        text += '\n\nSelected evidence (recorded observation):\n' + json.dumps(item, ensure_ascii=False, indent=2)
    value = await app.form('Review next-phase instructions', [('text', 'Instructions', text, 'text')],
                           message='Review preset is a conversation prompt. Independent read-only review remains a separate workflow.')
    if not value:
        return
    agent = await app.io(lambda store: live_agent(app.host, record))
    action = await app.choice('Continue conversation', ([('Reviewed follow-up to exact agent', 'followup')] if agent else [('Try saved session recovery', 'resume')]) + [('New linked handover in this checkout', 'new')])
    if action == 'resume':
        return await app.history_action('resume')
    if action == 'followup' and await app.confirm('Send next-phase instructions', record['name'] + '\n\n' + value['text']):
        await app.io(lambda store: ops.followup(store, app.host, record, value['text']))
        app.store.observe('phase:' + uuid.uuid4().hex, record['id'], {'kind': 'phase', 'title': phase, 'state': 'delivered'})
    elif action == 'new':
        chosen = await choose_many(app, 'Copy previous handover context', record['context'], lambda x: x['label'])
        if chosen is not None:
            child = await app.io(lambda store: tasks.phase_draft(store, record, phase, value['text'], chosen + context))
            await app.wizard(child)


async def queue_menu(app):
    items = tasks.queue(app.store)
    if not items:
        await app.form('Up next', message='Select issues and choose Add to Up next.', submit='Close')
        return
    status = []
    for t in items:
        workers = tasks.candidates(app.store, t)
        note = ' · '.join(r['name'] + ': ' + app.activity(r) for r in workers)
        linked = [r.get('orch_id') for r in workers if r.get('orch_id')]
        dependencies = [task for task in app.orch if task['id'] in linked]
        status.append(t['project'] + ' · ' + t['key'] + ' · ' + t['title'] + ' · ' + (note or 'Not started') +
                      '\nNative dependencies: ' + (json.dumps(dependencies) if dependencies else 'unknown / not linked'))
    selected = await app.choice('Up next · manual order', [(label, str(i)) for i, label in enumerate(status)])
    if selected is None:
        return
    ticket = items[int(selected)]
    action = await app.choice('Queued task', [('Start / Continue', 'start'), ('Move up', 'up'), ('Move down', 'down'), ('Remove', 'remove')])
    if action == 'start':
        return await continue_task(app, ticket)
    if action:
        tasks.queue(app.store, ticket, action)


async def batch(app):
    cached, _ = await app.io(lambda s: ops.refresh(s, False))
    all_tickets = {ticket_key(t): t for t in cached + app.tasks}
    tickets = [all_tickets[k] for k in app.checked if k in all_tickets]
    if not tickets:
        raise TaskError('Select issues first, including any hidden selections you want to edit.')
    value = await app.form('Prepare / Edit selected drafts', [
        ('agent', 'Agent (blank preserves)', '', 'input'), ('preset', 'Preset (blank preserves)', '', 'input'),
        ('pack', 'Repository context pack (blank skips)', '', 'input'),
        ('replace-notes', 'Replace extra notes', False, 'bool'), ('notes', 'Extra notes', '', 'text')],
        message='All selected issues, including hidden rows:\n' + '\n'.join(t['project'] + ' · ' + t['key'] for t in tickets))
    if not value:
        return
    if value['agent'] and value['agent'] not in (await app.io(lambda s: app.host.available_agents())).values():
        raise TaskError('Selected agent is unavailable.')
    if value['preset'] and value['preset'] not in app.store.config()['presets']:
        raise TaskError('Choose an existing preset.')
    records, failures, targets = [], [], set()
    originals = {}
    for ticket in tickets:
        try:
            matches = [r for r in tasks.candidates(app.store, ticket) if r['stage'] == 'draft' and not r.get('approved')]
            if len(matches) > 1:
                ident = await app.choice('Draft for ' + ticket['key'], [(r['name'], r['id']) for r in matches])
                if ident is None:
                    continue
                record = next(r for r in matches if r['id'] == ident)
            else:
                record = matches[0] if matches else await app.io(lambda s, t=ticket: ops.draft(s, t, persist=False))
            originals[record['id']] = copy.deepcopy(record) if matches else None
            for key in ('agent', 'preset'):
                if value[key]:
                    record[key] = value[key]
                    record.setdefault('manual_fields', [])
                    if key not in record['manual_fields']:
                        record['manual_fields'].append(key)
            if value['replace-notes']:
                record['notes'] = value['notes']
            # Validate a copy without replacing a live editor's unsaved draft.
            plan = await app.io(lambda s, r=record: workflow_target(s, r))
            if plan['path'] in targets:
                raise TaskError('Duplicate batch target: ' + plan['path'])
            targets.add(plan['path'])
            records.append((record, plan))
        except (TaskError, ValueError, OSError) as exc:
            failures.append(ticket['key'] + ': ' + str(exc))
    if not records:
        raise TaskError('\n'.join(failures))
    preview = '\n'.join(r['ticket']['key'] + ' · ' + p['branch'] + ' · ' + p['path'] for r, p in records)
    if not await app.confirm('Save batch drafts (no launches)', preview + '\n\n' + '\n'.join(failures)):
        return
    for record, _ in records:
        try:
            current = next((r for r in app.store.records('handovers') if r['id'] == record['id']), None)
            if current != originals[record['id']]:
                raise TaskError('Draft changed while reviewing; reopen batch editing.')
            if value['pack']:
                record = await app.io(lambda s, r=record: tasks.apply_pack(s, r, value['pack']))
            ops.update_prompt(app.store, record)
            save_record(app.store, record)
            editor = app.editors.get(record['id'])
            if editor:
                await editor.remove()
                app.editors.pop(record['id'], None)
        except (TaskError, OSError) as exc:
            failures.append(record['ticket']['key'] + ': ' + str(exc))
    app.show_tab('handovers')
    if failures:
        await app.form('Batch results', message='Saved valid drafts. Remaining items:\n' + '\n'.join(failures), submit='Close')


def workflow_target(store, record):
    from .handover import target_plan
    v = record['inputs']
    return target_plan(v['repo'], v['branch'], v['base'], v['new'], store.root, v.get('worktree_parent', ''))


async def capture(app, value=None, context=None):
    from . import capture_providers as adapter
    context = context or {}
    if context.get('pane') and not clean(context.get('selection', '')).strip():
        raise TaskError('Select terminal text first, or use Capture in the Tasks console.')
    if value is None:
        value = tasks.new_capture(app.store, clean(context.get('selection', '')), context.get('workspace', {}).get('cwd', ''))
    if value['state'] == 'draft':
        def save(changes):
            value.update(changes)
            app.store.save_capture(value)
        edited = await app.form('Capture task · ' + value['id'], [('title', 'Title', value['title'], 'input'),
            ('body', 'Description / selected output', value['body'], 'text'), ('repo', 'Local repository', value.get('repo', ''), 'input')], autosave=save,
            message='Saved locally. Publish to a tracker before starting an agent.', submit='Save / actions')
        if edited is None:
            return
        save(edited)
    options = [('Save & close', 'close'), ('Link an existing / created issue', 'link')]
    if value['state'] == 'draft':
        options.insert(0, ('Publish to tracker', 'publish'))
    if value.get('ticket') and value['state'] == 'published':
        options.insert(0, ('Start / Continue', 'start'))
    action = await app.choice('Captured task · ' + value['state'], options)
    if action == 'start':
        return await continue_task(app, value['ticket'])
    if action == 'link':
        field = await app.form('Link actual issue', [('url', 'Issue URL', value.get('ticket', {}).get('url', ''), 'input')])
        if not field:
            return
        choices = lookup_candidates(app.store.config(), field['url'])
        selected = await app.choice('Authorized connection', [(c['id'] + ' · ' + ident, str(i)) for i, (c, ident) in enumerate(choices)])
        if selected is not None:
            connection, ident = choices[int(selected)]
            ticket = await app.io(lambda s: provider(connection).get(ident))
            if value.get('destination') and (ticket['connection'] != value['destination']['connection'] or ticket['project'] != value['destination']['project']):
                raise TaskError('The issue is outside the submitted destination.')
            if await app.confirm('Confirm actual issue', ticket['url'] + '\n' + ticket['title']):
                value.update(state='published', ticket=ticket)
                app.store.save_capture(value)
        return
    if action != 'publish':
        return
    connections = app.store.config()['connections']
    selected = await app.choice('Publication connection', [(c['id'], c['id']) for c in connections])
    if selected is None:
        return
    connection = next(c for c in connections if c['id'] == selected)
    client = await app.io(lambda s: provider(connection))
    if connection['provider'] == 'github':
        project = await app.choice('GitHub repository', [(r, r) for r in github_repositories(connection)])
    elif connection['provider'] == 'azure':
        project = connection['project']
    else:
        field = await app.form('Jira project', [('project', 'Project key', '', 'input')])
        project = field['project'].strip() if field else None
    if not project:
        return
    kinds = await app.io(lambda s: adapter.types(client, project))
    kind = await app.choice('Issue type', [(k['name'], str(k['id'])) for k in kinds])
    if not kind:
        return
    destination = {'connection': connection['id'], 'project': project, 'type': kind}
    required = await app.io(lambda s: adapter.fields(client, destination))
    fields = []
    for i, f in enumerate(required):
        choices = f.get('choices', [])
        field_type = 'bool' if f['type'].lower() == 'boolean' else 'input'
        default = f.get('default') if f.get('default') is not None else (False if field_type == 'bool' else '')
        if choices and f['type'].lower() != 'array':
            field_type = [(str(x.get('name', x.get('value', x.get('id')))), str(x.get('id', x.get('value')))) if isinstance(x, dict) else (str(x), str(x)) for x in choices]
            default = field_type[0][1]
        elif f['type'].lower() not in ('boolean', 'string', 'text', 'plaintext', 'number', 'integer', 'double', 'date'):
            import webbrowser
            if await app.confirm('Complete required fields in provider', f['title'] + ' is unsupported. Open the provider, then link the created issue here?'):
                webbrowser.open(adapter.browser_url(client, project))
            return
        fields.append(('field-' + str(i), f['title'], default, field_type))
    values = await app.form('Review issue publication', fields,
        message=connection['id'] + ' · ' + project + ' · ' + kind + '\n\n' + value['title'] + '\n\n' + value['body'], submit='Publish')
    if values is None:
        return
    converted = {f['id']: values['field-' + str(i)] for i, f in enumerate(required)}
    result = await app.io(lambda s: tasks.publish_capture(s, value['id'], destination, converted, expected_connection=connection, expected_capture=value))
    await app.form('Issue published', message=result['ticket']['url'] + '\nUse Captures → Start / Continue.', submit='Close')


async def action(app, name, payload=None):
    if name == 'task-start':
        context = app.action_context.get()
        record = app.record() if app.query_one('#tabs', TabbedContent).active in ('handovers', 'work') or context and context.get('record') and context.get('issue') is None else None
        return await continue_task(app, record=record, ticket=None if record else app.issue())
    if name == 'task-archive':
        record = app.record()
        restore = bool(record.get('archived_at'))
        if await app.confirm('Restore handover' if restore else 'Archive handover', record['ticket']['key'] + '\nHistory and files are retained.'):
            editor = app.editors.get(record['id'])
            if editor and (editor.operation_busy or not editor.flush()):
                raise TaskError('Finish and save this draft before archiving.')
            await app.io(lambda s: tasks.archive(s, app.host, record['id'], restore))
            if editor:
                await editor.remove()
                app.editors.pop(record['id'], None)
            app.refresh_attention()
            if restore:
                app.refresh_linked_prs()
        return
    if name == 'task-next-phase':
        return await next_phase(app, app.record())
    if name == 'task-followup':
        return await app.history_action('follow-up')
    if name == 'task-evidence':
        app.record()
        app.show_tab('work')
        app.paint_work()
        return
    if name == 'task-review':
        editor = app.active_editor()
        if editor:
            editor.action('editor-review')
        return
    if name == 'task-queue':
        return await queue_menu(app)
    if name == 'task-enqueue':
        cached, _ = await app.io(lambda s: ops.refresh(s, False))
        known = {ticket_key(t): t for t in cached + app.tasks}
        selected = [known[k] for k in app.checked if k in known] if app.checked else [app.issue()]
        if len(selected) == 1 or await app.confirm('Add selected issues to Up next', '\n'.join(t['project'] + ' · ' + t['key'] for t in selected)):
            for ticket in selected:
                tasks.queue(app.store, ticket)
        return
    if name == 'task-batch':
        for editor in app.editors.values():
            if editor.operation_busy or not editor.flush():
                raise TaskError('Finish and save open drafts before batch editing.')
        return await batch(app)
    if name == 'task-capture':
        return await capture(app, context=payload)
    if name == 'task-captures':
        captures = app.store.captures()
        selected = await app.choice('Saved captures', [((c['title'] or 'Untitled') + ' · ' + c['state'], c['id']) for c in captures])
        if selected:
            return await capture(app, next(c for c in captures if c['id'] == selected))
    if name in ('task-snooze', 'task-unsnooze'):
        item = next((x for x in app.attention_items if x['id'] == app.selected_attention), None)
        if not item:
            raise TaskError('Select an attention observation.')
        seconds = '0' if name == 'task-unsnooze' else await app.choice('Snooze: ' + item['title'], [('15 minutes', '900'), ('1 hour', '3600'), ('1 day', '86400')])
        if seconds is not None:
            tasks.snooze(app.store, item, int(seconds))
            app.paint_attention()
    if name == 'task-expand-attention':
        item = next((x for x in app.attention_items if x['id'] == app.selected_attention), None)
        if item:
            key = item.get('handover') or item['id']
            app.expanded_attention.symmetric_difference_update({key})
            app.paint_attention()
    if name == 'task-next-attention':
        app.show_tab('attention')
        table = app.query_one('#attention-table', DataTable)
        if table.row_count:
            table.move_cursor(row=(table.cursor_row + 1) % table.row_count)
            table.focus()
