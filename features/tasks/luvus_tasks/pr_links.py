"""Local PR associations and compact, read-only branch observations."""
import copy
import hashlib
import json
import re
import urllib.parse
from pathlib import Path

from .core import TaskError, handover_tickets, now
from . import forge


def key(link, ident):
    return json.dumps([link['kind'], link['connection'], link['repository'].casefold(), str(ident)])


def references(body, tickets, repository, azure_organization=None):
    """Match explicit issue references, never title/branch similarity or bare numbers."""
    body = body or ''
    for t in tickets:
        url = t.get('url', '').rstrip('/')
        if url and re.search(re.escape(url) + r'(?![\w/\d])', body, re.I):
            return True
        qualified = t.get('key', '')
        if t.get('provider') == 'GitHub':
            number = t['id'].rsplit('#', 1)[-1]
            qualified = t['project'] + '#' + number
            if t['project'].casefold() == repository.casefold() and re.search(r'(?<![\w/#])#' + re.escape(number) + r'(?!\d)', body):
                return True
        if t.get('provider') == 'Azure DevOps' and azure_organization:
            source = urllib.parse.urlsplit(t.get('url', ''))
            if source.hostname != 'dev.azure.com' or source.path.strip('/').split('/')[0].casefold() != azure_organization.casefold():
                qualified = ''  # AB# numbers are scoped to an Azure organization.
        if qualified and re.search(r'(?<![\w/])' + re.escape(qualified) + r'(?![\w])', body, re.I):
            return True
    return False


def read_client(store, record):
    """Remote reads may use a saved authorized identity after a local checkout disappears."""
    link = record.get('forge')
    path = (record.get('target') or {}).get('path', '')
    if path and Path(path).is_dir():
        link = forge.preferred(store, record)
    if not link:
        raise TaskError('Choose a PR connection for this repository in handover details.')
    c = next((c for c in store.config()['connections'] if c['id'] == link['connection']), None)
    if not c or c['provider'] != link['kind'] or record.get('forge_account', c.get('account', '')) != c.get('account', ''):
        raise TaskError('PR account or connection changed; choose the connection again.')
    if link['kind'] == 'github':
        from .providers import github_repositories
        if link['repository'].casefold() not in [r.casefold() for r in github_repositories(c)]:
            raise TaskError('PR repository is no longer selected in this connection.')
        return forge.GitHubPR(c, link['repository']), link
    if any(str(c.get(k, '')).casefold() != str(link.get(k, '')).casefold() for k in ('organization', 'project')):
        raise TaskError('Azure PR connection scope changed.')
    return forge.AzurePR(c, link['repository']), link


def candidates(client, link, record):
    branch = record['target']['branch']
    result = [(p, 'Branch match') for p in client.list(branch)]
    # ponytail: scan at most 500 PRs; use provider search when larger histories need exhaustive discovery.
    notice = ''
    for page in range(5):
        if link['kind'] == 'github':
            raw = client.api(f'/pulls?state=all&sort=updated&direction=desc&per_page=100&page={page + 1}')
        else:
            raw = client.request(client.git + '/pullrequests', query={'searchCriteria.status': 'all', '$top': 100, '$skip': page * 100})['value']
        for value in raw:
            p = client.normalize(value)
            if p.get('head_branch') == branch:
                result.append((p, 'Branch match'))
            if references(p.get('title', '') + '\n' + p.get('body', ''), handover_tickets(record), link['repository'], link.get('organization')):
                result.append((p, 'Issue reference'))
        if len(raw) < 100:
            break
    else:
        notice = 'Issue-reference discovery checked 500 PRs; older PRs can be linked manually.'
    if link['kind'] == 'azure':
        # Native links can identify older PRs without scanning the full PR history.
        for t in handover_tickets(record):
            url = urllib.parse.urlsplit(t.get('url', ''))
            parts = [urllib.parse.unquote(x) for x in url.path.strip('/').split('/')]
            same_org = url.hostname == 'dev.azure.com' and parts and parts[0].casefold() == link['organization'].casefold()
            if t.get('provider') != 'Azure DevOps' or not same_org or not str(t['id']).isdigit():
                continue
            try:
                work = client.azure.request('workitems/' + str(t['id']), query={'$expand': 'relations'})
                for relation in work.get('relations', []):
                    if relation.get('rel') != 'ArtifactLink':
                        continue
                    artifact = urllib.parse.unquote(relation.get('url', ''))
                    match = re.fullmatch(r'vstfs:///Git/PullRequestId/([^/]+)/([^/]+)/(\d+)', artifact, re.I)
                    if match and match[1].casefold() == client.repository['project']['id'].casefold() and match[2].casefold() == client.repo.casefold():
                        result.append((client.get(match[3]), 'Azure work-item link'))
            except TaskError:
                notice = (notice + ' Native Azure work-item links could not be fully read; check Work Items read permission.').strip()
    return result, notice


def evidence_id(record, link, ident):
    return 'linked-pr:' + record['id'] + ':' + hashlib.sha256(key(link, ident).encode()).hexdigest()[:24]


def observe(store, record, client, link, pr):
    ident = evidence_id(record, link, pr['id'])
    prior = next((e for e in store.evidence(record['id']) if e['id'] == ident), {})
    try:
        snapshot = client.snapshot(pr['id'])
        if snapshot.get('errors'):
            raise TaskError('; '.join(snapshot['errors']))
        if str(snapshot.get('id')) != str(pr['id']) or str(snapshot.get('repository', '')).casefold() != str(client.repo).casefold():
            raise TaskError('PR response does not match the selected repository and PR.')
        item = {'kind': 'linked-pr', 'title': 'PR #' + pr['id'], 'state': snapshot['state'], 'snapshot': snapshot,
                'forge': link, 'stale': False, 'error': '', 'observed_at': now()}
    except TaskError as exc:
        item = {**prior, 'kind': 'linked-pr', 'title': 'PR #' + pr['id'], 'state': 'unknown', 'stale': True, 'error': str(exc)}
    current = next((r for r in store.records('handovers') if r['id'] == record['id']), {})
    if any(current.get(k) != record.get(k) for k in ('target', 'forge', 'ignored_prs', 'linked_prs')):
        return prior
    return store.observe(ident, record['id'], item)


def refresh(store, record, client=None, link=None):
    original = copy.deepcopy(next((r for r in store.records('handovers') if r['id'] == record['id']), record))
    if any(original.get(k) != record.get(k) for k in ('target', 'tickets', 'ticket')):
        raise TaskError('Handover changed; refresh again.')
    if client is None:
        client, link = read_client(store, record)
    links = {p['key']: p for p in record.get('linked_prs', [])}
    ignored = set(record.get('ignored_prs', []))
    found, notice = candidates(client, link, record)
    branch_matches = {p['id'] for p, source in found if source == 'Branch match' and key(link, p['id']) not in ignored}
    if record.get('pr_id'):
        ident = str(record['pr_id'])
        links.setdefault(key(link, ident), {'key': key(link, ident), 'id': ident, 'forge': link, 'sources': ['Branch match']})
    for p, source in found:
        k = key(link, p['id'])
        if k in ignored:
            continue
        old = links.get(k, {})
        links[k] = {**old, 'key': k, 'id': p['id'], 'url': p['url'], 'branch': p['head_branch'], 'forge': link,
                    'sources': list(dict.fromkeys(old.get('sources', []) + [source]))}
    # Do not overwrite an editor or a changed association after network I/O.
    current = next((r for r in store.records('handovers') if r['id'] == record['id']), None)
    if current is None or any(current.get(k) != original.get(k) for k in ('target', 'forge', 'forge_account', 'tickets', 'ticket', 'pr_id', 'linked_prs', 'ignored_prs')):
        return
    store.set_preference('pr-discovery-error:' + record['id'], '')
    current.update(linked_prs=list(links.values()), pr_discovery_notice=notice, forge=link)
    c = next(c for c in store.config()['connections'] if c['id'] == link['connection'])
    current['forge_account'] = c.get('account', '')
    if not current.get('pr_id') and len(branch_matches) == 1:
        current['pr_id'] = next(iter(branch_matches))
    from .handover import save_record
    if current != record:
        save_record(store, current)
    observations = {e['id']: e.get('observed_at', '') for e in store.evidence(record['id'])}
    pending = sorted((p for p in current['linked_prs'] if p['key'] not in ignored and p.get('forge') == link), key=lambda p: observations.get(evidence_id(record, p['forge'], p['id']), ''))
    for pr in pending[:4]:
        observe(store, current, client, link, pr)


def link_url(store, record, url):
    client, link = read_client(store, record)
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        raise TaskError('Use a full HTTPS PR URL.')
    if link['kind'] == 'github':
        match = re.fullmatch(r'/([^/]+/[^/]+)/pull/(\d+)/?', parsed.path)
        valid = parsed.hostname == 'github.com' and match and match[1].casefold() == link['repository'].casefold()
    else:
        base = urllib.parse.urlsplit(client.repository['webUrl'])
        match = re.fullmatch(re.escape(base.path.rstrip('/')) + r'/pullrequest/(\d+)/?', parsed.path, re.I)
        valid = parsed.hostname == base.hostname and match
    if not valid:
        raise TaskError('This PR URL does not belong to the handover repository.')
    ident = match[2] if link['kind'] == 'github' else match[1]
    p = client.get(ident)
    k = key(link, p['id'])
    current = next(r for r in store.records('handovers') if r['id'] == record['id'])
    if any(current.get(x) != record.get(x) for x in ('target', 'forge', 'forge_account')):
        raise TaskError('Handover target changed; link the PR again.')
    links = {p['key']: p for p in current.get('linked_prs', [])}
    links[k] = {'key': k, 'id': p['id'], 'url': p['url'], 'branch': p['head_branch'], 'forge': link,
                'sources': list(dict.fromkeys(links.get(k, {}).get('sources', []) + ['Manually linked']))}
    current.update(linked_prs=list(links.values()), ignored_prs=[x for x in current.get('ignored_prs', []) if x != k], forge=link)
    current['forge_account'] = next(c for c in store.config()['connections'] if c['id'] == link['connection']).get('account', '')
    from .handover import save_record
    save_record(store, current)
    observe(store, current, client, link, links[k])
    return current


def items(store, record):
    evidence = store.evidence(record['id'])
    ignored = set(record.get('ignored_prs', []))
    result = []
    for pr in record.get('linked_prs', []):
        if pr['key'] not in ignored:
            item = next((e for e in evidence if e['id'] == evidence_id(record, pr['forge'], pr['id'])), {})
            result.append((pr, item))
    if not result:
        item = next((e for e in evidence if e['id'] == 'pr:' + record['id']), None)
        if item:
            p = item.get('snapshot', {})
            if p.get('id') and item.get('forge') and key(item['forge'], p['id']) in ignored:
                return []
            result.append(({'id': p.get('id', ''), 'url': p.get('url', ''), 'branch': p.get('head_branch', ''), 'sources': ['Branch match']}, item))
    return result


def ci_state(item):
    from .workflow import ci_results
    p = item.get('snapshot', {})
    revisions = {p.get('head'), p.get('merge')} - {None, ''}
    states = [c['state'] for c in ci_results(p) if c.get('revision') in revisions]
    return 'failed' if 'failed' in states else 'running' if 'running' in states else 'passed' if states and all(s == 'passed' for s in states) else 'unknown'


def short_status(pr, item):
    from .attention import age
    p = item.get('snapshot', {})
    if p.get('state') == 'unpublished':
        return 'Not published · no PR' + (' · stale' if item.get('stale') or item.get('error') or age(item.get('observed_at')) > 120 else '')
    status = ('PR #' + str(pr['id']) + ' ' + ('draft' if p.get('draft') else p.get('state', 'unknown'))) if pr.get('id') else 'No PR'
    count = sum(bool(f.get('text')) and f.get('state') not in ('resolved', 'DISMISSED') for f in p.get('feedback', []))
    return status + ' · CI ' + ci_state(item) + (f' · {count} feedback' if count else '') + (' · stale' if item.get('stale') or item.get('error') or age(item.get('observed_at')) > 120 else '')


def summary(store, record):
    values = items(store, record)
    if not values:
        return 'PR/CI not checked'
    error = store.preference('pr-discovery-error:' + record['id'], '')
    if len(values) == 1:
        value = short_status(*values[0])
        return value + (' · stale' if error and 'stale' not in value else '')
    states = [ci_state(e) for _, e in values]
    state = 'failed' if 'failed' in states else 'running' if 'running' in states else 'passed' if all(s == 'passed' for s in states) else 'unknown'
    from .attention import age
    stale = bool(error) or any(e.get('stale') or e.get('error') or age(e.get('observed_at')) > 120 for _, e in values)
    return f'{len(values)} PRs · CI {state}' + (' · stale' if stale else '')
