"""Issue creation on the existing authenticated provider adapters."""
import html
import math
from datetime import date

from .core import TaskError
from .providers import GitHub, Jira, Azure, adf, segment


def types(client, project):
    if isinstance(client, GitHub):
        client.scoped(project)
        return [{'id': 'issue', 'name': 'Issue'}]
    if isinstance(client, Jira):
        rows, start = [], 0
        while True:
            page = client.http.request('/rest/api/3/issue/createmeta/' + segment(project) + '/issuetypes',
                                       query={'startAt': start, 'maxResults': 50})
            batch = page.get('issueTypes', page.get('values', []))
            rows.extend(batch)
            start += len(batch)
            if page.get('isLast') or start >= page.get('total', start):
                return rows
            if not batch:
                raise TaskError('Incomplete issue type metadata.')
    if project != client.c['project']:
        raise TaskError('Project is outside the configured Azure connection.')
    return [{'id': t['name'], 'name': t['name']} for t in client.request('workitemtypes')['value']]


def fields(client, destination):
    project, kind = destination['project'], destination['type']
    if isinstance(client, GitHub):
        client.scoped(project)
        return []
    if isinstance(client, Jira):
        rows, start = [], 0
        while True:
            page = client.http.request('/rest/api/3/issue/createmeta/' + segment(project) + '/issuetypes/' + segment(kind),
                                       query={'startAt': start, 'maxResults': 50})
            batch = page.get('fields', page.get('values', []))
            rows.extend(batch)
            start += len(batch)
            if page.get('isLast') or start >= page.get('total', start):
                break
            if not batch:
                raise TaskError('Incomplete required-field metadata.')
        return [{'id': f.get('fieldId', f.get('key')), 'title': f['name'], 'type': f.get('schema', {}).get('type', 'unsupported'),
                 'choices': f.get('allowedValues', []), 'default': f.get('defaultValue'),
                 'custom': f.get('schema', {}).get('custom', '')}
                for f in rows if f.get('required') and f.get('fieldId', f.get('key')) not in ('summary', 'description', 'project', 'issuetype')]
    if project != client.c['project']:
        raise TaskError('Project is outside the configured Azure connection.')
    rows = client.request('workitemtypes/' + segment(kind) + '/fields', query={'$expand': 'All'})['value']
    schema = {f['referenceName']: f for f in client.request('fields')['value']}
    return [{'id': f['referenceName'], 'title': f['name'], 'type': schema.get(f['referenceName'], {}).get('type', 'unsupported'),
             'choices': f.get('allowedValues', []), 'default': f.get('defaultValue')}
            for f in rows if f.get('alwaysRequired') and f['referenceName'] not in ('System.Title', 'System.Description', 'System.TeamProject', 'System.WorkItemType')
            and not schema.get(f['referenceName'], {}).get('readOnly') and not f.get('defaultValue')]


def field_value(field, value):
    if field['type'].lower() == 'array':
        raise TaskError('Complete this multi-value field in the provider browser: ' + field['title'])
    if field.get('choices'):
        match = next((x for x in field['choices'] if str(x.get('id', x.get('value', ''))) == str(value)), None) if isinstance(field['choices'][0], dict) else next((x for x in field['choices'] if str(x) == str(value)), None)
        if match is None:
            raise TaskError('Choose an allowed value for ' + field['title'])
        return {'id': str(match['id'])} if isinstance(match, dict) and 'id' in match else match
    kind = field['type'].lower()
    if kind == 'boolean':
        if not isinstance(value, bool):
            raise TaskError('Expected a boolean field.')
        return value
    if kind in ('number', 'double', 'integer'):
        parsed = int(value) if kind == 'integer' else float(value)
        if not math.isfinite(parsed):
            raise TaskError('Expected a finite number.')
        return parsed
    if value is None or not str(value).strip():
        raise TaskError(field['title'] + ' is required.')
    if kind == 'date' or field.get('custom', '').endswith(':datepicker'):
        return date.fromisoformat(str(value)).isoformat()
    if kind in ('string', 'text', 'plaintext'):
        return adf(str(value)) if field.get('custom', '').endswith(':textarea') else str(value)
    raise TaskError('Complete unsupported field in the provider browser: ' + field['title'])


def create_issue(client, destination, title, body, values, required=None):
    # Recheck provider metadata/scope before publishing; a changed form cannot widen scope.
    required = fields(client, destination) if required is None else required
    allowed = {f['id'] for f in required}
    if set(values) - allowed:
        raise TaskError('Creation fields changed; review the destination again.')
    validated = {f['id']: field_value(f, values.get(f['id'], f.get('default'))) for f in required}
    project, kind = destination['project'], destination['type']
    if isinstance(client, GitHub):
        scope = client.scoped(project)
        return scope.normalize(scope.api(scope.prefix, 'POST', {'title': title, 'body': body}))
    if isinstance(client, Jira):
        result = client.http.request('/rest/api/3/issue', 'POST', {'fields': {
            **validated, 'project': {'key': project}, 'issuetype': {'id': kind}, 'summary': title, 'description': adf(body)}}, write=True)
        # Return the durable identity before the caller performs readback.
        return {'provider': 'Jira', 'connection': client.c['id'], 'project': project, 'id': str(result['id']), 'key': result['key']}
    data = [{'op': 'add', 'path': '/fields/' + key, 'value': value} for key, value in {
        **validated, 'System.Title': title, 'System.Description': html.escape(body).replace('\n', '<br>')}.items()]
    client.request('workitems/$' + segment(kind), 'POST', data, query={'validateOnly': 'true'}, patch=True)
    return client.normalize(client.request('workitems/$' + segment(kind), 'POST', data, patch=True, write=True))


def browser_url(client, project):
    if isinstance(client, GitHub):
        client.scoped(project)
        return 'https://github.com/' + project + '/issues/new'
    if isinstance(client, Jira):
        return client.site + '/secure/CreateIssue!default.jspa'
    if project != client.c['project']:
        raise TaskError('Project is outside the configured Azure connection.')
    return client.site + '/' + segment(project) + '/_workitems/create'
