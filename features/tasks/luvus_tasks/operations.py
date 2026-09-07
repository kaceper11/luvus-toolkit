"""Shared operations for the console and native module actions. No UI dependencies."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from . import MODULE_ID
from .core import handover_tickets, handover_label, has_ticket, GITHUB_DEFAULT_QUERY, TaskError, clean, compose, default_filters, now, ticket_key
from .handover import branch_name, default_base, file_context, git, launch, live_agent, matching_agent, repository, save_record, target_plan, validate_issue_repository
from .providers import ProviderError, github_repositories, provider


def connection(store, ticket):
    found = next((c for c in store.config()["connections"] if c["id"] == ticket["connection"]), None)
    if not found:
        raise TaskError("Connection was removed. Restore it in Configuration.")
    return found


def refresh(store, network=False, factory=None, scope=None, on_group=None):
    factory = factory or provider
    tasks, errors = {}, []
    original_config = store.config()
    connections = []
    for c in store.config()["connections"]:
        try:
            connections.extend([{**c, "_scope_repository": repo} for repo in github_repositories(c)] if c["provider"] == "github" else [c])
        except TaskError as exc:
            errors.append(f"{c['id']}: {exc}")
            connections.append({**c, "_scope_repository": c.get("repository", ""), "_scope_error": str(exc)})
    for c in connections:
        for view in c.get("filters", default_filters(c["provider"])):
            query = (view["query"] or GITHUB_DEFAULT_QUERY) if c["provider"] == "github" else view["query"]
            repository_scope = c.get("_scope_repository", "")
            # Keep legacy cache keys for the original repository.
            suffix = "" if repository_scope == c.get("repository", "") else repository_scope + ":"
            key = c["id"] + ":" + suffix + hashlib.sha256(query.encode()).hexdigest()
            cached, at = store.cached(key)
            error = c.get("_scope_error")
            fetch = network and (scope is None or (c["id"], scope[1]) == scope and (not c.get("_scope_repository") or c["_scope_repository"] == scope[1]))
            if fetch and not error:
                try:
                    partial_jira = scope is not None and c['provider'] == 'jira'
                    selected = {**c, '_scope_project': scope[1]} if partial_jira else c
                    incoming = factory(selected).query(query)
                    if partial_jira:
                        if any(t['project'] != scope[1] for t in incoming):
                            raise TaskError('Jira returned an issue outside the selected project.')
                        preserved = [{**t, '_refreshed': t.get('_refreshed', at)} for t in cached if t['project'] != scope[1]]
                        cached = preserved + [{**t, '_refreshed': now()} for t in incoming]
                    else:
                        cached = incoming
                    if store.config() != original_config:
                        return refresh(store, False)
                    store.put("cache", key, cached)
                    at = now()
                except TaskError as exc:
                    error = f"{c['id']} / {repository_scope} / {view['name']}: {exc}"
                    errors.append(error)
            for ticket in cached:
                identity = ticket_key(ticket)
                prior = tasks.get(identity, {})
                tasks[identity] = {**ticket, "refreshed": ticket.get("_refreshed", at), "stale": bool(error) or not fetch or bool(scope and c["provider"] == "jira" and ticket["project"] != scope[1]),
                                   "views": prior.get("views", []) + [c["id"] + " / " + view["name"]]}
            if on_group and fetch:
                snapshot, _ = refresh(store, False)
                snapshot = [{**t, 'stale': tasks.get(ticket_key(t), {}).get('stale', True)} for t in snapshot]
                on_group(snapshot, list(errors))
    if network and scope is None:
        store.set_preference("issue-refresh-errors", errors)
    return list(tasks.values()), errors


def draft(store, ticket, repo="", persist=True):
    config = store.config()
    c = connection(store, ticket)
    repo = repo or c.get("repositories", {}).get(ticket["project"], "")
    defaults = {**config.get("branch_defaults", {}), **config["repositories"].get(repo, {})}
    ident = str(uuid.uuid4())
    record = {"id": ident, "name": "task-" + ident[:16], "ticket": ticket, "created": now(),
              "stage": "draft", "context": [], "notes": "", "prompt": "", "prompt_mode": "generated",
              "repository_source": "Saved mapping" if repo else "Choose a workspace or enter a local checkout",
              "agent": defaults.get("agent", "codex"), "preset": defaults.get("preset", "Implement"),
              "inputs": {"repo": repo, "branch": branch_name(defaults.get("branch_pattern", "feature/{number}-{slug}"), ticket),
                         "base": default_base(repo, defaults.get("base", "")), "new": True,
                         "worktree_parent": defaults.get("worktree_parent", "")}}
    if persist:
        save_record(store, record)
    return record


def update_prompt(store, record, regenerate=False):
    record.setdefault("inputs", dict(record.get("target") or {}))
    config = store.config()
    config["presets"].setdefault("Custom", "Follow the user-approved handover instructions below.")
    if record["preset"] not in config["presets"]:
        record["preset"] = "Custom"
    record.setdefault("prompt_mode", "custom" if record.get("prompt") else "generated")
    generated = compose(config, config["repositories"].get(record["inputs"]["repo"], {}), record["preset"],
                        record["ticket"], record.get("notes", ""), record["context"], handover_tickets(record))
    if regenerate or record["prompt_mode"] == "generated":
        record.update(prompt=generated, prompt_mode="generated", generated_prompt=generated, prompt_outdated=False)
    else:
        record["prompt_outdated"] = generated != record.get("generated_prompt", generated)
        record.setdefault("generated_prompt", generated)


def set_group_issues(store, record, tickets, title=None):
    current = next((r for r in store.records('handovers') if r['id'] == record['id']), record)
    if any(r.get('approved') or r.get('pane') or r.get('stage') != 'draft' for r in (current, record)):
        raise TaskError('Issue membership is frozen once launch is reviewed or started. Prepare a new handover.')
    unique = {(t['connection'], t['project'], t['id']): t for t in tickets}
    if not unique:
        raise TaskError('A handover needs at least one issue.')
    tickets = list(unique.values())
    if len({t['project'].casefold() for t in tickets if t.get('provider') == 'GitHub'}) > 1:
        raise TaskError('Grouped GitHub issues must belong to one code repository.')
    candidate = {**record, 'ticket': tickets[0], 'tickets': tickets}
    if candidate['inputs'].get('repo'):
        validate_group_repository(store.config(), candidate, candidate['inputs']['repo'])
    record.update(ticket=tickets[0], tickets=tickets)
    if title is not None:
        record['title'] = title.strip() or tickets[0]['title']
    record.pop('approved', None)
    update_prompt(store, record)
    return record


def validate_group_repository(config, record, repo):
    tickets = handover_tickets(record)
    for ticket in tickets:
        validate_issue_repository(ticket, repo)
        if len(tickets) > 1 and not ticket_matches(config, ticket, repo):
            raise TaskError(ticket['key'] + ': map this issue to the chosen code repository before grouping.')


def prepare(store, record):
    values = record["inputs"]
    validate_group_repository(store.config(), record, values["repo"])
    plan = record["target"] if record.get("target") and record.get("prepared_inputs") == values else target_plan(values["repo"], values["branch"], values["base"], values["new"], store.root, values.get("worktree_parent", ""))
    old = record.get("target")
    if record["context"]:
        refreshed = []
        for item in record["context"]:
            try:
                if item.get("relative"):
                    item = file_context(plan, item["relative"], item["mode"] == "snapshot", item.get("start"), item.get("end"))
                elif item["mode"] == "reference" and old != plan:
                    raise TaskError("Remove this legacy reference and add it again for the new target.")
                item.pop("error", None)
            except (TaskError, OSError) as exc:
                item = {**item, "error": str(exc)}
            refreshed.append(item)
        record["context"] = refreshed
    record["target"] = plan
    record["prepared_inputs"] = dict(values)
    update_prompt(store, record)
    save_record(store, record)
    return record


def preflight(store, host, record):
    host.capabilities()
    if record["agent"] not in host.available_agents().values():
        raise TaskError("Selected agent is unavailable. Install it or choose another agent.")
    if not record.get("approved"):
        prepare(store, record)
    plan = record["target"]
    validate_group_repository(store.config(), record, plan["repo"])
    if record.get("prompt_outdated"):
        raise TaskError("Context or defaults changed. Regenerate or explicitly keep the edited prompt before review.")
    invalid = [x["label"] + ": " + x["error"] for x in record["context"] if x.get("error")]
    if invalid:
        raise TaskError("Fix or remove invalid context:\n" + "\n".join(invalid))
    missing = [x["label"] for x in record["context"] if x["mode"] == "attachment" and not Path(x["target"]).is_file()]
    if missing:
        raise TaskError("Missing attachments: " + ", ".join(missing))
    agents = [a for a in host.agents() if a.get("cwd") and Path(a["cwd"]).resolve() == Path(plan["path"]).resolve()]
    own = matching_agent(agents, record)
    other = [a for a in agents if a != own]
    if other:
        raise TaskError("An agent already uses this checkout. Open its handover for follow-up, or choose an isolated branch.")
    dirty = git(plan["path"], "status", "--short") if Path(plan["path"]).exists() else ""
    return {"record": record, "dirty": dirty,
            "images": any(x.get("image") for x in record["context"])}


def review_diff(host, record):
    """Focus the verified worker explicitly, then validate the native DIFF repository."""
    host.validate_terminal(record)
    host.call("pane.focus", pane=record["pane"])
    result = host.call("diff.list")
    if Path(result["repo"]).resolve() != Path(record["target"]["path"]).resolve():
        raise TaskError("Native DIFF is showing a different checkout. No file was opened.")
    if result.get("refreshing"):
        raise TaskError("Native DIFF is refreshing. Retry after it finishes.")
    return result


def open_review_diff(host, record, file):
    snapshot = review_diff(host, record)
    if not any(f.get("path") == file.get("path") and f.get("layer") == file.get("layer") for f in snapshot["files"]):
        raise TaskError("DIFF selection changed; select the file again.")
    return host.call("diff.open", path=file["path"], layer=file["layer"], placement="tab")


def publish_comment(store, ticket, text):
    if not text.strip():
        raise TaskError("Comment is empty.")
    p = provider(connection(store, ticket))
    key = ticket_key(ticket)
    digest = hashlib.sha256((key + "\0" + text).encode()).hexdigest()
    marker = "[luvus-tasks:" + digest[:20] + "]"
    with store.lock("writeback"):
        prior = next((x for x in store.records("writes", key) if x["id"] == digest), None)
        if prior:
            if prior["state"] == "published" or any(marker in x["text"] for x in p.comments(ticket)):
                return "Already published; duplicate prevented."
            if prior["state"] == "pending":
                raise TaskError("Previous comment outcome is uncertain. Inspect the tracker; automatic retry is disabled.")
        record = {"id": digest, "state": "pending", "text": text + "\n\n" + marker, "at": now()}
        store.put("writes", digest, record, key)
        try:
            p.comment(ticket, record["text"])
        except ProviderError as exc:
            if not exc.ambiguous:
                record["state"] = "rejected"
                store.put("writes", digest, record, key)
            raise
        if not any(marker in x["text"] for x in p.comments(ticket)):
            raise TaskError("Comment submitted; readback not visible. Inspect the tracker before retrying.")
        record["state"] = "published"
        store.put("writes", digest, record, key)
    return "Comment published and verified."


def transition(store, ticket, action, fields):
    key = ticket_key(ticket)
    with store.lock("status"):
        p = provider(connection(store, ticket))
        fresh = p.get(ticket["id"])
        if fresh != ticket:
            raise TaskError("Ticket changed since review. Reload available transitions and review again.")
        ident = hashlib.sha256(json.dumps([key, ticket, action, fields], sort_keys=True).encode()).hexdigest()
        prior = next((x for x in store.records("writes", key) if x["id"] == ident), None)
        if prior and prior["state"] in ("pending", "published"):
            raise TaskError("This transition was already submitted. Inspect the current tracker state before proceeding.")
        entry = {"id": ident, "state": "pending", "kind": "status", "at": now()}
        store.put("writes", ident, entry, key)
        try:
            result = p.transition(ticket, action, fields)
        except ProviderError as exc:
            if not exc.ambiguous:
                entry["state"] = "rejected"
                store.put("writes", ident, entry, key)
            raise
        entry["state"] = "published"
        store.put("writes", ident, entry, key)
        return result


def followup(store, host, record, text):
    if not text.strip():
        raise TaskError("Follow-up instructions are empty.")
    from .bounded import lock as workflow_lock
    with workflow_lock(store), store.lock("followup"):
        from .bounded import reservation
        reservation(store, record)
        current = next(r for r in store.records("handovers") if r["id"] == record["id"])
        if any(f["text"] == text and f["state"] in ("pending", "delivered") for f in current.get("followups", [])):
            raise TaskError("Identical follow-up already submitted; inspect the agent before retrying.")
        agent = live_agent(host, current)
        if not agent:
            raise TaskError("Exact live conversation is unavailable. Resume it first.")
        host.validate_terminal(current)
        from .checkout import guard
        guard(current)
        if current.get('orch_enabled'):
            from .orch import coordinate
            coordinate(store, host, current)
        guard(current)
        entry = {"text": text, "at": now(), "state": "pending"}
        current.setdefault("followups", []).append(entry)
        save_record(store, current)
        host.call("agent.prompt", target=agent["pane"], text=text)
        entry["state"] = "delivered"
        save_record(store, current)


def orch_snapshot(host):
    methods = {m["method"] for m in host.capabilities()["method_contracts"]}
    if not {"task.list", "lease.list", "task.claim", "task.add", "task.done"} <= methods:
        raise TaskError("Installed Luvus lacks the required ORCH methods.")
    return {"tasks": host.call("task.list")["tasks"], "leases": host.call("lease.list")}


def link_orch(store, host, record, task_id="", paths=(), deps=()):
    with store.lock("orch"):
        current = next(r for r in store.records("handovers") if r["id"] == record["id"])
        if current.get("orch_id") and not task_id:
            host.call("task.get", id=current["orch_id"])
            return current
        if current.get("orch_pending") and not task_id:
            raise TaskError("ORCH creation outcome is uncertain. Inspect native ORCH and explicitly link the created task; creating another is disabled.")
        state = orch_snapshot(host)
        if not task_id:
            current["orch_pending"] = True
            save_record(store, current)
            task = host.call("task.add", title=current["ticket"]["key"] + " · " + current["ticket"]["title"],
                             paths=list(paths), deps=list(deps))["task"]
            task_id = task["id"]
        else:
            task = next((t for t in state["tasks"] if t["id"] == task_id), None)
            if task is None:
                raise TaskError("Selected ORCH task no longer exists.")
        current.update(orch_id=task_id, orch_pending=False)
        save_record(store, current)
        return current


def claim_orch(store, host, record):
    host.validate_terminal(record)
    if not live_agent(host, record):
        raise TaskError("Exact worker is not live; cannot claim ORCH task.")
    state = orch_snapshot(host)
    task = host.call("task.get", id=record["orch_id"])["task"]
    if task.get("status") in ("done", "merged"):
        raise TaskError("ORCH task is complete. Create a new phase or explicitly reopen it before sending.")
    if task.get("assignee") is not None and str(task["assignee"]) != str(record["pane"]):
        raise TaskError("Task belongs to a different worker.")
    if task.get("assignee") is None:
        host.call("task.claim", id=record["orch_id"], pane=record["pane"])
    if task.get("paths") and not any(l["task"] == task["id"] and str(l["pane"]) == str(record["pane"]) and
                                      l["paths"] == task["paths"] for l in state["leases"]["leases"]):
        # Native lease acquisition owns overlap semantics. A rejection leaves the task claimed, not the worker stopped.
        host.call("lease.acquire", task=task["id"], pane=record["pane"], paths=task["paths"])
    return host.call("task.get", id=task["id"])


def orch_action(store, host, record, reviewed, action):
    if action not in ("done", "release"):
        raise TaskError("Unsupported ORCH action.")
    with store.lock("orch"):
        task = host.call("task.get", id=record["orch_id"])["task"]
        host.call("lease.list")
        if task != reviewed:
            raise TaskError("ORCH task changed since review; inspect it again.")
        if action == "done" and task.get("status") in ("done", "merged"):
            return {"task": task}
        if task.get("gate") and action == "done":
            host.validate_terminal(record)
            if str(task.get("assignee")) != str(record.get("pane")):
                raise TaskError("Claim the task for the verified worker before running its gate; active-workspace fallback is disabled.")
            if task.get("worktree") and Path(task["worktree"]).resolve() != Path(record["target"]["path"]).resolve():
                raise TaskError("Native gate worktree differs from the reviewed handover.")
        key = "orch-write:" + record["orch_id"]
        if store.preference(key, {}).get("pending"):
            raise TaskError("Previous ORCH write outcome is uncertain. Inspect native ORCH before any retry.")
        if store.preference(key, {}).get("gate_running") and task.get("status") == "running":
            raise TaskError("The previously requested quality gate is still running. Do not run it again.")
        store.set_preference(key, {"pending": True, "action": action, "at": now()})
        result = host.call("task." + action, id=record["orch_id"])
        store.set_preference(key, {"pending": False, "gate_running": result.get("gate_running", False), "action": action, "at": now()})
        return result


def session_key():
    home = str(Path((os.environ.get("LUVUS_HOME") or Path.home() / ".luvus")).resolve())
    session = os.environ.get("LUVUS_SESSION", "default")
    # The inherited socket is authoritative. Default home callers use the same canonical identity.
    socket = os.environ.get("LUVUS_SOCKET_PATH", "")
    if socket and os.name != "nt":
        socket = str(Path(socket).resolve())
    if socket == str(Path(home) / "luvus.sock"):
        socket = ""
    identity = [socket, home, session]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]


def register_console(store, host):
    pane = os.environ.get("LUVUS_PANE_ID")
    if not pane:
        return
    inventory = host.call("terminal.backend.inventory")
    terminal = next((t for t in inventory["terminals"] if str(t["pane_id"]) == pane), None)
    if terminal:
        from .tab_titles import remember
        remember(host.call, pane, "☑ Tasks")
        store.set_preference("console:" + session_key(), {"pane": pane, "terminal": terminal["terminal_id"],
                                                        "generation": inventory["server_generation"],
                                                        "inbox": session_key() + ":terminal:" + terminal["terminal_id"]})


def open_console(store, host, action="open", ticket="", context=None):
    if action == "open" and not ticket:
        # Existing running consoles already understand this navigation payload.
        context = context or {}
        source = "pane" if context.get("invocation_source") in ("menu:pane", "menu:agent") else "workspace"
        ticket = "dashboard:" + json.dumps({"section": "issues", "repo": context.get(source, {}).get("cwd", "")})
    session = session_key()
    with store.lock("console-" + session):
        inventory = host.call("terminal.backend.inventory")
        saved = store.preference("console:" + session, {})
        terminal = next((t for t in inventory["terminals"] if t["terminal_id"] == saved.get("terminal") and
                         str(t["pane_id"]) == str(saved.get("pane"))), None)
        if terminal and inventory["server_generation"] == saved.get("generation"):
            host.call("pane.focus", pane=terminal["pane_id"])
            from .tab_titles import remember
            remember(host.call, terminal["pane_id"], "☑ Tasks")
            store.put("inbox", uuid.uuid4().hex, {"session": saved.get("inbox", session), "action": action, "ticket": ticket, "context": context or {}})
            return
        if saved.get("pending") and saved.get("generation") == inventory["server_generation"]:
            raise TaskError("Console opening outcome is uncertain. Inspect Luvus before opening another tab.")
        store.set_preference("console:" + session, {"pending": True, "generation": inventory["server_generation"]})
        result = host.call("module.pane.open", module=MODULE_ID,
                           entrypoint="tasks-windows" if os.name == "nt" else "tasks", placement="tab")
        from .tab_titles import remember
        remember(host.call, result["pane"], "☑ Tasks")
        inventory = host.call("terminal.backend.inventory")
        terminal = next((t for t in inventory["terminals"] if str(t["pane_id"]) == str(result["pane"])), None)
        if terminal:
            store.set_preference("console:" + session, {"pane": result["pane"], "terminal": terminal["terminal_id"],
                                                       "generation": inventory["server_generation"],
                                                       "inbox": session + ":terminal:" + terminal["terminal_id"]})
        destination = store.preference("console:" + session, {}).get("inbox", session)
        store.put("inbox", uuid.uuid4().hex, {"session": destination, "action": action, "ticket": ticket, "context": context or {}})


def browser_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise TaskError("Only HTTPS URLs without embedded credentials can be opened.")
    return value


def repository_identity(path):
    if not path:
        return ""
    try:
        return str(Path(git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve())
    except TaskError:
        return ""


def ticket_matches(config, ticket, path):
    identity = repository_identity(path)
    if not identity:
        return False
    if ticket.get("provider") == "GitHub":
        try:
            validate_issue_repository(ticket, path)
            return True
        except (TaskError, ValueError):
            return False
    c = next((c for c in config["connections"] if c["id"] == ticket["connection"]), {})
    mapped = c.get("repositories", {}).get(ticket["project"], "")
    if mapped:
        return repository_identity(mapped) == identity
    try:
        remote = git(path, "remote", "get-url", "origin").lower().rstrip("/").removesuffix(".git")
    except TaskError:
        return False
    return c.get("provider") == "github" and any(remote.endswith(p + ticket["project"].lower()) for p in ("github.com/", "github.com:"))


def needs_attention(record, agents, pending=()):
    agent = matching_agent(agents, record)
    return bool(any(ticket_key(t) in pending for t in handover_tickets(record)) or record.get("error") or record.get("orch_pending") or
                record["stage"].endswith("pending") or (agent and agent.get("status") in ("blocked", "waiting")) or
                any(f["state"] == "pending" for f in record.get("followups", [])))


def dashboard(store, tasks, agents, scope, errors=(), attention_items=None):
    """A bounded issue index with cached branch summaries, not a second task console."""
    from .attention import project
    from .productivity import snoozed
    from . import pr_links
    config = store.config()
    attention_items = [i for i in (attention_items if attention_items is not None else project(store, agents)) if not snoozed(store, i)]
    attention_ids = {i['handover'] for i in attention_items}
    urgent_ids = {i['handover'] for i in attention_items if i.get('state') == 'active'}
    repo = scope.get('repo', '') if scope.get('mode') != 'all' else ''
    all_repos = scope.get('mode') == 'all'
    suffix = '-windows' if os.name == 'nt' else ''
    def payload(**data):
        return 'dashboard:' + json.dumps(data, ensure_ascii=False)
    def row(text, value='', action='open', **extra):
        return {'text': clean(text), 'action': action + suffix, 'value': value, **extra}
    def menu(title, value='', action='open'):
        return {'title': title, 'action': action + suffix, 'value': value}
    def short(text, width=44):
        text = ' '.join(str(text).split())
        return text if len(text) <= width else text[:width - 1] + '…'
    rows = [row('Browse issues', payload(section='issues', repo=repo), menu=[menu('Refresh issues', action='refresh'), menu('Configuration', payload(config=True)), menu('All repositories', 'all', 'scope'), menu('Use current workspace', 'current', 'scope')])]
    if attention_items:
        rows.append(row(f'{len(attention_items)} need attention', payload(section='attention-all'), dot='blocked'))
    if errors:
        rows.append(row('Refresh issue · cached results retained', payload(config=True), dot='blocked'))
    history = [r for r in store.records('handovers') if not r.get('archived_at')]
    if not all_repos:
        identity = repository_identity(repo)
        history = [r for r in history if identity and repository_identity((r.get('target') or r.get('inputs') or {}).get('repo', '')) == identity]
        matches = {}
        for t in tasks:
            group = (t['connection'], t['project'])
            if group not in matches:
                matches[group] = bool(identity and ticket_matches(config, t, repo))
        tasks = [t for t in tasks if matches[(t['connection'], t['project'])]]
    live = {r['id']: matching_agent(agents, r) for r in history}
    grouped = {}
    # A shared handover appears once. Every member remains accessible in its menu and Issues.
    represented = {ticket_key(t) for r in history for t in handover_tickets(r)}
    for r in history:
        target = r.get('target') or r.get('inputs') or {}
        heading = r['ticket']['project']
        entry_key = ('group', tuple(sorted(ticket_key(t) for t in handover_tickets(r))))
        entries = grouped.setdefault(heading, {})
        entry = entries.setdefault(entry_key, {'ticket': r['ticket'], 'records': [], 'title': handover_label(r), 'priority': 2})
        entry['records'].append(r)
        entry['priority'] = min(entry['priority'], 0 if r['id'] in attention_ids else 1 if live[r['id']] else 2)
    for t in tasks:
        if ticket_key(t) not in represented and t['status'].casefold() not in ('closed', 'done', 'resolved', 'completed', 'removed'):
            grouped.setdefault(t['project'], {})[('issue', ticket_key(t))] = {'ticket': t, 'records': [], 'title': t['title'], 'priority': 3}
    for current_work, title in ((True, 'Current work · PR / CI'), (False, 'Open issues')):
        section = {heading: {k: e for k, e in entries.items() if bool(e['records']) == current_work} for heading, entries in grouped.items()}
        section = {heading: entries for heading, entries in section.items() if entries}
        if not section:
            continue
        rows.append({'text': title})
        for heading, entries in sorted(section.items()):
            rows.append({'text': '── ' + short(heading, 36) + f' · {len(entries)}'})
            ordered = sorted(entries.values(), key=lambda e: (e['priority'], e['ticket']['key']))
            for entry in ordered[:5]:
                t, records = entry['ticket'], entry['records']
                label = '#' + t['key'].rsplit('#', 1)[-1] if t.get('provider') == 'GitHub' else t['key']
                actions = [menu('Open issue', ticket_key(t)), menu('Open issue in browser', ticket_key(t), 'browser')]
                records = sorted(records, key=lambda r: (r['id'] not in urgent_ids, r['id'] not in attention_ids, not bool(live[r['id']])))
                for r in records[:3]:
                    actions += [menu('View handover · ' + handover_label(r), payload(record=r['id'])), menu('Start / Continue', payload(record=r['id'], action='task-start')), menu('Linked PRs', payload(record=r['id'], action='work-linked-prs'))]
                    if len(handover_tickets(r)) > 1:
                        actions += [menu('Issue ' + member['key'], ticket_key(member)) for member in handover_tickets(r)[1:5]]
                if len(records) > 3:
                    actions.append(menu(f'Browse all {len(records)} handovers', payload(section='handovers', repo=repo)))
                rows.append(row(short(label + ' ' + entry['title']), ticket_key(t), dot='blocked' if entry['priority'] == 0 else 'working' if entry['priority'] == 1 else 'idle', menu=actions))
                branches = set()
                for r in records:
                    target = r.get('target') or r.get('inputs') or {}
                    branch = target.get('branch') or 'Branch not chosen'
                    if branch in branches:
                        continue
                    if len(branches) >= 3:
                        rows.append(row('  More branches / handovers', payload(section='handovers', repo=repo)))
                        break
                    branches.add(branch)
                    state = 'Needs attention' if r['id'] in attention_ids else 'Working' if live[r['id']] else 'Draft' if r['stage'] == 'draft' else 'Saved'
                    rows.append(row('  ' + short(branch, 30) + ' · ' + state, payload(record=r['id'])))
                    if (r.get('target') or {}).get('path') or pr_links.items(store, r):
                        rows.append(row('  ' + short(pr_links.summary(store, r)), payload(record=r['id'], action='work-linked-prs')))
                if not records:
                    rows.append(row('  ' + short(t['status']), ticket_key(t)))
                rows.append({'text': ''})
            if len(ordered) > 5:
                rows.append(row(f'Browse all {len(ordered)} · ' + short(heading, 25), payload(section='handovers' if current_work else 'issues', project=heading, repo=repo)))
    if not grouped:
        rows.append(row('No linked work · choose repository', payload(config=True)))
    rows.append(row('Handovers', payload(section='handovers', repo=repo)))
    return rows, sum(bool(a) for a in live.values()), len(attention_items)


def suggested_connections(config, repo):
    try:
        remote = git(repo, "remote", "get-url", "origin").removesuffix(".git").lower()
    except TaskError:
        return []
    result = []
    for c in config["connections"]:
        if c["provider"] == "github" and any(remote.endswith(prefix + r.lower()) for r in github_repositories(c) for prefix in ("github.com/", "github.com:")):
            result.append(c["id"])
        elif c["provider"] == "azure" and ("dev.azure.com/" + c["organization"] + "/" + c["project"] + "/_git/").lower() in remote:
            result.append(c["id"])
    return result
