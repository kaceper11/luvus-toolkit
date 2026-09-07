"""Local task lifecycle and reusable draft operations. No implicit agent dispatch."""
import copy
import json
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from .core import handover_tickets, handover_label, has_ticket, TaskError, now, ticket_key
from .handover import attach, file_context, matching_agent, save_record
from . import operations as ops


def identity(ticket):
    return json.dumps([ticket['connection'], ticket['project'], ticket['id']])


def candidates(store, ticket):
    return [r for r in store.records('handovers') if not r.get('archived_at') and has_ticket(r, ticket)]


def pending(record):
    return (record.get('error') or record.get('orch_pending') or record['stage'].endswith('pending')
            or any(f['state'] == 'pending' for f in record.get('followups', [])))


def archive(store, host, ident, restore=False):
    from .attention import project
    from .bounded import lock as workflow_lock
    with ExitStack() as locks:
        locks.enter_context(workflow_lock(store))
        for operation in ('archive', 'launch', 'followup', 'writeback', 'status', 'orch', 'pr-publication', 'review-start'):
            locks.enter_context(store.lock(operation))
        record = next((r for r in store.records('handovers') if r['id'] == ident), None)
        if record is None:
            raise TaskError('Handover was removed. Refresh the list.')
        if restore:
            record.pop('archived_at', None)
        else:
            from .bounded import reservation
            reservation(store, record)
            agents = host.agents()  # Failure is not evidence that the worker stopped.
            conditions = project(store, agents)
            record = next(r for r in store.records('handovers') if r['id'] == ident)
            draft = record['stage'] == 'draft' and not record.get('approved')
            if pending(record) or any(e['handover'] == ident for e in conditions):
                raise TaskError('Resolve attention and pending operations before archiving.')
            agent = matching_agent(agents, record)
            reviewed = record.get('review', {}).get('changes') and record.get('review', {}).get('criteria')
            if not draft and (record['stage'] != 'delivered' or not reviewed):
                raise TaskError('Complete the completion review before archiving this handover.')
            if agent and agent.get('status') not in ('done', 'idle'):
                raise TaskError('An active worker cannot be archived.')
            if record.get('pane') and not agent:
                raise TaskError('Worker state is unknown. Reconcile it before archiving.')
            record['archived_at'] = now()
        save_record(store, record)
        store.observe('archive:' + ident, ident, {'kind': 'archive', 'title': 'Handover archive',
                      'state': 'restored' if restore else 'archived'})
        return record


def queue(store, ticket=None, action='add'):
    def change(items):
        items = list(items or [])
        key = identity(ticket)
        index = next((i for i, t in enumerate(items) if identity(t) == key), None)
        if action == 'add' and index is None:
            items.append(copy.deepcopy(ticket))
        elif action == 'remove' and index is not None:
            items.pop(index)
        elif action in ('up', 'down') and index is not None:
            dest = index + (-1 if action == 'up' else 1)
            if 0 <= dest < len(items):
                items.insert(dest, items.pop(index))
        return items
    return store.update_preference('up-next', change, []) if ticket else store.preference('up-next', [])


def packs(store, repo):
    return store.preference('context-packs:' + str(Path(repo).resolve()), {})


def save_pack(store, repo, name, context, old_name=None, delete=False):
    if not repo or not name.strip():
        raise TaskError('Choose a repository and name the context pack.')
    name = name.strip()
    entries = []
    for item in context:
        if item.get('relative'):
            entries.append({k: item.get(k) for k in ('label', 'relative', 'mode', 'start', 'end')})
        elif item.get('mode') == 'text':
            entries.append({k: item.get(k, '') for k in ('label', 'mode', 'text')})
        else:
            raise TaskError('Packs support notes and repository references; deselect attachments and tracker snapshots.')
    def change(current):
        current = dict(current or {})
        if old_name and old_name != name and name in current:
            raise TaskError('A pack with that name already exists.')
        if old_name:
            current.pop(old_name, None)
        if delete:
            current.pop(name, None)
        else:
            current[name.strip()] = entries
        return current
    return store.update_preference('context-packs:' + str(Path(repo).resolve()), change, {})


def apply_pack(store, record, name):
    if record.get('approved'):
        raise TaskError('Approved handovers cannot be edited.')
    repo = record['inputs']['repo']
    entries = copy.deepcopy(packs(store, repo).get(name))
    if entries is None:
        raise TaskError('Context pack was removed.')
    plan = ops.prepare(store, record)['target']
    for item in entries:
        if item.get('relative'):
            try:
                resolved = file_context(plan, item['relative'], item['mode'] == 'snapshot', item.get('start'), item.get('end'))
                item = {**resolved, 'label': item['label']}
            except (TaskError, OSError) as exc:
                item['error'] = str(exc)
        record['context'].append(item)
    ops.update_prompt(store, record)
    save_record(store, record)
    return record


def phase_draft(store, parent, phase, text, selected_context=()):
    from .handover import git, target_plan
    target = parent.get('target') or {}
    if not target.get('path'):
        raise TaskError('The previous handover has no prepared checkout.')
    branch = git(target['path'], 'branch', '--show-current')
    if branch != target['branch']:
        raise TaskError('The checkout branch changed. Review its target first.')
    child = ops.draft(store, parent['ticket'], target['path'], persist=False)
    child['inputs'].update(repo=target['path'], branch=branch, new=False)
    child.update(preset=phase, notes=text, phase=phase, parent_handover=parent['id'], tickets=copy.deepcopy(handover_tickets(parent)), title=parent.get('title', ''))
    plan = target_plan(target['path'], branch, '', False, store.root)
    for original in selected_context:
        item = copy.deepcopy(original)
        if item.get('mode') == 'attachment':
            item = attach(store, child['id'], item['target'])
        elif item.get('relative'):
            item = file_context(plan, item['relative'], item['mode'] == 'snapshot', item.get('start'), item.get('end'))
        child['context'].append(item)
    ops.update_prompt(store, child)
    save_record(store, child)
    store.observe('phase:' + child['id'], parent['id'], {'kind': 'phase', 'title': phase,
                  'state': 'draft', 'child': child['id']})
    return child


SNOOZABLE = {'agent-done', 'pr-feedback', 'review'}


def snoozed(store, item):
    value = store.preference('snooze:' + item['id'], {})
    return (item['source'] in SNOOZABLE and value.get('signature') == item['signature']
            and value.get('until', 0) > time.time())


def snooze(store, item, seconds):
    if item['source'] not in SNOOZABLE:
        raise TaskError('This condition must resolve at its source and cannot be snoozed.')
    if seconds not in (0, 900, 3600, 86400):
        raise TaskError('Choose an available snooze duration.')
    store.set_preference('snooze:' + item['id'], {'signature': item['signature'], 'until': time.time() + seconds})


def new_capture(store, text='', repo=''):
    capture = {'id': str(uuid.uuid4()), 'title': '', 'body': text, 'repo': repo,
               'state': 'draft', 'created': now()}
    store.save_capture(capture)
    return capture


def publish_capture(store, ident, destination, fields, factory=None, expected_connection=None, expected_capture=None):
    from .providers import provider, ProviderError
    from .capture_providers import create_issue, fields as creation_fields, field_value
    with store.lock('capture-' + ident):
        capture = next((c for c in store.captures() if c['id'] == ident), None)
        if capture is None:
            raise TaskError('Capture was removed. Reopen the capture list.')
        if capture['state'] != 'draft':
            raise TaskError('Publication already submitted. Link/read back the actual issue before retrying.')
        connection = next((c for c in store.config()['connections'] if c['id'] == destination['connection']), None)
        if not connection:
            raise TaskError('The destination connection was removed.')
        if expected_connection is not None and connection != expected_connection:
            raise TaskError('Connection changed after review. Review the destination again.')
        if expected_capture is not None and capture != expected_capture:
            raise TaskError('Capture changed after review. Review the content again.')
        client = (factory or provider)(connection)
        if not capture['title'].strip():
            raise TaskError('The issue needs a title.')
        required = creation_fields(client, destination)
        for field in required:
            field_value(field, fields.get(field['id'], field.get('default')))
        if set(fields) - {f['id'] for f in required}:
            raise TaskError('Creation fields changed. Review the form again.')
        if expected_connection is not None and connection not in store.config()['connections']:
            raise TaskError('Connection changed during metadata validation.')
        latest = next(c for c in store.captures() if c['id'] == ident)
        if latest != capture:
            raise TaskError('Capture changed during validation. Review it again.')
        capture.update(destination=destination, fields=fields, state='pending')
        store.save_capture(capture)
        try:
            ticket = create_issue(client, destination, capture['title'], capture['body'], fields, required=required)
            capture['ticket'] = ticket
            store.save_capture(capture)  # Preserve returned identity even if readback fails.
            if ticket['provider'] == 'GitHub':
                client = client.scoped(ticket['project'])
            checked = client.get(ticket['id'])
            if identity(checked) != identity(ticket):
                raise TaskError('Created issue readback identity differs.')
            capture.update(state='published', ticket=checked)
        except ProviderError as exc:
            if not exc.ambiguous and not capture.get('ticket'):
                capture['state'] = 'draft'
            capture['error'] = str(exc)
            store.save_capture(capture, allow_retry=capture['state'] == 'draft')
            raise
        store.save_capture(capture)
        return capture
