"""Reviewed Tasks console actions for evidence and repository workflows."""
import asyncio
import json
from pathlib import Path
import uuid
import webbrowser

from textual.widgets import TabbedContent, Select, Input

from . import attention, forge, workflow
from .core import handover_tickets, handover_label, has_ticket, TaskError, now, atomic_json, ticket_key
from .handover import AGENTS, save_record, git



async def repair_connection(app, record, force=False):
    link = await app.io(lambda s: forge.preferred(s, record))
    if link and not force:
        record['forge'] = link
        return True
    links = await app.io(lambda s: forge.choices(s, record))
    if not links:
        value = await app.form('Connection setup needed', message='No authorized connection matches this origin. Choose or repair its repository scope.', submit='Open connection setup')
        if value is not None:
            await app.configure('connections')
        links = await app.io(lambda s: forge.choices(s, record))
        if not links:
            return False
    connections = {c['id']: c for c in app.store.config()['connections']}
    link = await select(app, 'Choose PR connection · matches this repository origin', links,
        lambda x: x['connection'] + ' · ' + connections[x['connection']].get('account', 'configured credentials') + ' · ' + x['kind'] + ' · ' + x['repository'])
    if not link:
        return False
    value = await app.form('Save PR connection', [('remember', 'Remember for this repository', True, 'bool')],
                           message=link['connection'] + ' → ' + link['repository'], submit='Save and continue')
    if value is None:
        return False
    if link not in await app.io(lambda s: forge.choices(s, record)):
        raise TaskError('Connection scope changed during repair. Choose again.')
    current_connection = next(c for c in app.store.config()['connections'] if c['id'] == link['connection'])
    if current_connection.get('account', '') != connections[link['connection']].get('account', ''):
        raise TaskError('Connection account changed during repair. Choose again.')
    if value['remember']:
        await app.io(lambda s: forge.remember(s, record, link))
    record['forge'] = link
    record['forge_account'] = connections[link['connection']].get('account', '')
    record.pop('pr_id', None)
    save_record(app.store, record)
    return True


async def show(app, title, data):
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    await app.form(title, message=text, submit="Close")


async def select(app, title, items, label=lambda x: x.get("title", x["id"]), load_more=None):
    if load_more:
        async def load_options():
            note = await load_more()
            return [(label(x), str(i)) for i, x in enumerate(items)], note
        options = [(label(x), str(i)) for i, x in enumerate(items)] or [("Searching native results…", "")]
        result = await app.form(title, [("choice", "Choose", options[0][1], options)], load_options=load_options)
        return items[int(result["choice"])] if result and result.get("choice") else None
    if not items:
        await show(app, title, "No matching items.")
        return None
    index = await app.choice(title, [(label(x), str(i)) for i, x in enumerate(items)])
    return items[int(index)] if index is not None else None


async def attention_action(app, action):
    if action == "attention-refresh":
        available = True
        try:
            app.agents = await app.io(lambda s: app.host.agents())
        except TaskError:
            available = False
        app.attention_items = await app.io(lambda s: attention.project(s, app.agents if available else [], app.orch, available))
        app.paint_attention()
        return
    if action == "attention-notify":
        value = await app.form("Optional attention notifications", [("enabled", "Notify for new CI, validation and review conditions", app.store.preference("attention-notifications", False), "bool")],
                               message="Native agent notifications and AI Usage keep their existing delivery owners.", submit="Save")
        if value:
            app.store.set_preference("attention-notifications", value["enabled"])
        return
    selected = next((x for x in app.attention_items if x["id"] == app.selected_attention), None)
    if not selected:
        raise TaskError("Select an attention item.")
    if action == "attention-ack":
        if await app.confirm("Mark this specific evidence reviewed", selected["title"] + "\nThis never approves agent input or changes its source."):
            attention.acknowledge(app.store, selected)
            await attention_action(app, "attention-refresh")
    elif action == "attention-open":
        if selected["target"].get("workflow"):
            from . import bounded_ui
            return await bounded_ui.manage(app, run_id=selected["target"]["workflow"])
        rid = await app.io(lambda s: attention.navigate(app.host, selected))
        if rid:
            app.selected_handover = rid
            app.show_tab("work")
            app.paint_work()
        elif not selected["target"].get("agent"):
            raise TaskError("Source no longer has a navigation target.")


async def search(app):
    from . import operations as ops
    value = await app.form("Find work", [
        ("query", "Issue, task, repository, branch or agent", "", "input"),
        ("archived", "Include archived handovers", False, "bool"),
        ("scope", "Native search", "navigate", [("Workspaces, tabs and agents", "navigate"),
                                                   ("Include file paths and output (slower)", "all")])])
    if not value or not value["query"].strip():
        return
    query = value["query"].strip()
    q = query.casefold()
    records = [r for r in app.store.records("handovers") if value.get("archived") or not r.get("archived_at")]
    def qualified(ticket):
        return ticket["connection"] + " · " + ticket["project"] + " · " + ticket["key"]
    def identity(ticket):
        return ticket_key(ticket), ticket["project"]
    rows = []
    for record in records:
        target = record.get("target") or record.get("inputs", {})
        label = ", ".join(qualified(t) for t in handover_tickets(record)) + " · " + handover_label(record)
        detail = " · ".join(str(target.get(k, "")) for k in ("path", "repo", "branch"))
        if q in (label + detail + str(record.get("name", "")) + str(record.get("agent", ""))).casefold():
            rows.append({"id": record["id"], "title": label + " · " + detail, "record": record["id"]})
    cached, _ = await app.io(lambda store: ops.refresh(store, False))
    tickets = {identity(t): t for t in [*cached, *app.tasks]}
    recorded = {identity(t) for r in records for t in handover_tickets(r)}
    rows += [{"id": ticket_key(t), "title": qualified(t) + " · " + t["title"], "ticket": t}
             for key, t in tickets.items() if key not in recorded and q in (qualified(t) + " " + t["title"]).casefold()]
    by_id = {r["id"]: r for r in records}
    for evidence in app.store.evidence():
        if evidence.get("path") and evidence["handover"] in by_id and q in (evidence["title"] + " " + evidence["path"]).casefold():
            record = by_id[evidence["handover"]]
            rows.append({"id": evidence["id"], "title": qualified(record["ticket"]) + " · " + evidence["title"] + " · " + evidence["path"], "record": record["id"]})
    async def load_native():
        notice = ""
        try:
            native = await app.io(lambda s: app.host.call("search.query", query=query, scope=value.get("scope", "navigate"), limit=100))
            inventory = await app.io(lambda s: app.host.call("terminal.backend.inventory"))
            if inventory.get("truncated"):
                raise TaskError("Terminal inventory is incomplete.")
            for result in native.get("matches", native.get("results", [])):
                terminal = next((t for t in inventory["terminals"] if str(t["pane_id"]) == str(result.get("target", {}).get("pane"))), None)
                rows.append({"id": result.get("id", "native"), "title": result.get("label", result.get("title", "Native result")) + " · " + result.get("detail", ""),
                             "native": result, "generation": inventory["server_generation"], "terminal": terminal})
            if native.get("partial"):
                notice = " · partial native results; refine your query"
        except (TaskError, ValueError, OSError, KeyError) as exc:
            notice = " · native search unavailable (stored matches only)"
            pass
        return notice or "Native results loaded"
    item = await select(app, "Find work · repository-qualified results · local matches ready", rows, load_more=load_native)
    if not item:
        return
    if item.get("record") or item.get("ticket"):
        app.dashboard_scope = ""
        app.scope_ticket_keys = app.scope_history_ids = None
        app.query_one("#history-filter", Select).value = "all"
        if item.get("record"):
            app.history = app.store.records("handovers")
            if not any(r["id"] == item["record"] for r in app.history):
                raise TaskError("Handover was removed. Search again.")
            app.query_one("#history-filter", Select).value = "archived" if next(r for r in app.history if r["id"] == item["record"]).get("archived_at") else "all"
            app.selected_handover = item["record"]
            app.show_tab("work")
            app.paint_work()
        else:
            ticket = item["ticket"]
            app.tasks = list(tickets.values())
            app.selected_issue = ticket_key(ticket)
            app.query_one("#connection", Select).value = "all"
            app.query_one("#repository-filter", Select).value = "all"
            app.query_one("#view", Select).value = "all"
            app.query_one("#search", Input).value = ""
            app.show_tab("issues")
            app.paint()
    elif item.get("native"):
        inventory = await app.io(lambda s: app.host.call("terminal.backend.inventory"))
        target = item["native"]["target"]
        if inventory.get("truncated") or inventory["server_generation"] != item["generation"] or target.get("pane") is not None and (not item.get("terminal") or not any(
                all(t.get(k) == item["terminal"].get(k) for k in ("pane_id", "terminal_id", "cwd")) for t in inventory["terminals"])):
            raise TaskError("Search target changed. Search again; no navigation occurred.")
        await app.io(lambda s: app.host.call("search.activate", kind=item["native"]["kind"], target=target))


async def action(app, action, record):
    if action == 'work-linked-prs':
        from . import pr_links
        record = next(r for r in app.store.records('handovers') if r['id'] == record['id'])
        items = pr_links.items(app.store, record)
        options = [(pr_links.short_status(pr, item) + ' · ' + ', '.join(pr.get('sources', [])), 'open:' + str(i)) for i, (pr, item) in enumerate(items)]
        options += [('Refresh automatic discovery', 'refresh'), ('Link existing PR URL', 'link'), ('Ignore / unlink a PR', 'ignore'), ('Restore ignored PR', 'restore')]
        chosen = await app.choice('Linked PRs · ' + handover_label(record), options)
        if not chosen:
            return
        if chosen.startswith('open:'):
            from .operations import browser_url
            pr, item = items[int(chosen[5:])]
            url = pr.get('url') or item.get('snapshot', {}).get('url')
            if not url:
                raise TaskError('No saved PR URL yet. Refresh discovery first.')
            await asyncio.to_thread(webbrowser.open, browser_url(url))
        elif chosen == 'refresh':
            await app.io(lambda s: pr_links.refresh(s, record))
        elif chosen == 'link':
            value = await app.form('Link PR', [('url', 'PR URL', '', 'input')], message='Local association only; no tracker or PR edits.', submit='Link')
            if value:
                await app.io(lambda s: pr_links.link_url(s, record, value['url']))
        elif chosen == 'ignore':
            links = record.get('linked_prs', [])
            k = await app.choice('Ignore locally (PR stays unchanged)', [(p.get('url') or p['id'], p['key']) for p in links if p['key'] not in record.get('ignored_prs', [])])
            if k:
                current = next(r for r in app.store.records('handovers') if r['id'] == record['id'])
                current['ignored_prs'] = list(dict.fromkeys(current.get('ignored_prs', []) + [k]))
                if current.get('pr_id') and current.get('forge') and pr_links.key(current['forge'], current['pr_id']) == k:
                    current.pop('pr_id', None)
                save_record(app.store, current)
        elif chosen == 'restore':
            k = await app.choice('Restore ignored PR', [(p.get('url') or p['id'], p['key']) for p in record.get('linked_prs', []) if p['key'] in record.get('ignored_prs', [])])
            if k:
                current = next(r for r in app.store.records('handovers') if r['id'] == record['id'])
                current['ignored_prs'] = [x for x in current.get('ignored_prs', []) if x != k]
                save_record(app.store, current)
        app.paint_work()
    elif action == "work-summary":
        data = workflow.summary(record)
        value = await app.form("Handover summary", [(k, k.replace("_", " ").title(), v, "text" if k in ("objective", "decisions", "remaining", "paths") else "input") for k, v in data.items()], submit="Save summary")
        if value:
            record["summary"] = value
            save_record(app.store, record)
            app.store.observe("summary:" + record["id"], record["id"], {"kind": "summary", "title": "Handover summary updated", "state": "saved", "summary": value})
    elif action == "work-timeline":
        offset = 0
        while True:
            events = app.store.timeline(record["id"], offset, 100)
            result = await app.form("Timeline · newest first", message=json.dumps(events, indent=2, ensure_ascii=False), submit="Older entries" if len(events) == 100 else "Close")
            if not result or len(events) < 100:
                break
            offset += 100
    elif action == "work-artifact":
        value = await app.form("Associate generated artifact", [("path", "Local file path", "", "input"), ("title", "Label (optional)", "", "input")], message="Reference only; no copy or upload. Removing the reference preserves the original.", submit="Add reference")
        if value:
            await app.io(lambda s: workflow.artifact(s, record, value["path"], value["title"]))
    elif action == "work-evidence":
        items = app.store.evidence(record["id"])
        items = [x for x in items if x["kind"] not in ("attention", "launch")]
        item = await select(app, "Evidence", items, lambda x: x["title"] + " · " + x["state"])
        if not item:
            return
        options = [("Details", "details")]
        if item.get('kind') == 'review' and item['state'] == 'prepared' and not item.get('orch_pending'):
            options.append(('Start this prepared review', 'review-start'))
        if item.get('kind') == 'review' and item['state'] in ('pending', 'unknown'):
            options.append(('Reconcile stopped review before retry', 'review-reconcile'))
        if item.get("orch_pending"):
            options += [("Link ORCH task after inspecting uncertain creation", "orch-link")]
        if item.get("orch_completion_pending"):
            options += [("Inspect ORCH completion outcome", "orch-inspect")]
        if item.get("path"):
            options += [("Open file", "open"), ("Reveal file", "reveal")]
            options += [("Prepare reviewed follow-up", "feedback")]
        if item["kind"] == "artifact":
            options += [("Remove reference", "remove")]
        if item["kind"] in ("validation", "setup"):
            options += [("Refresh run", "refresh"), ("Retry command (reviewed)", "retry")]
        operation = await app.choice("Evidence action", options)
        if operation == 'review-start':
            from .review import start
            if await app.confirm('Start this frozen read-only review', json.dumps(item, indent=2)):
                await app.io(lambda s: start(s, app.host, item))
        elif operation == 'review-reconcile':
            inventory = await app.io(lambda s: app.host.call('terminal.backend.inventory'))
            if inventory.get('truncated') or any(Path(t.get('cwd', '')).resolve() == Path(item['folder']).resolve() for t in inventory['terminals']):
                raise TaskError('The review terminal still exists. Inspect it and close it only after its process has stopped, then reconcile again.')
            if item.get('orch_id'):
                task = await app.io(lambda s: app.host.call('task.get', id=item['orch_id'])['task'])
                if task.get('assignee') is not None or task.get('status') in ('done', 'merged'):
                    raise TaskError('Inspect the native ORCH task and release its stopped worker before retrying. Completed tasks cannot be rerun.')
            if await app.confirm('Allow retry of this frozen review', 'I verified that no reviewer or quality gate is running. This resets the local run only; no prompt is sent now.'):
                item.update(state='prepared', error='')
                app.store.observe(item['id'], item['handover'], item)
        elif operation == 'orch-link':
            tasks = await app.io(lambda s: app.host.call('task.list')['tasks'])
            task = await select(app, 'Link the review task already created in ORCH', tasks, lambda t: t['id'] + ' · ' + t['title'])
            if task and await app.confirm('Link existing task', task['id'] + ' → ' + item['title'] + '\nI verified this is the task created for this review.'):
                item.update(orch_id=task['id'], orch_pending=False)
                app.store.observe(item['id'], item['handover'], item)
        elif operation == 'orch-inspect':
            task = await app.io(lambda s: app.host.call('task.get', id=item['orch_id'])['task'])
            await show(app, 'Native ORCH outcome', task)
            if task.get('status') in ('done', 'merged', 'blocked'):
                item['orch_completion_pending'] = False
                app.store.observe(item['id'], item['handover'], item)
        elif operation == "details":
            data = dict(item)
            if item.get("path"):
                data["file_state"] = workflow.artifact_state(item)
            if item.get("checkout"):
                data["current_freshness"] = await app.io(lambda s: workflow.freshness(item, record["target"]["path"]))
            await show(app, "Evidence details", data)
        elif operation in ("open", "reveal"):
            await asyncio.to_thread(workflow.open_artifact, item, operation == "reveal")
        elif operation == "remove" and await app.confirm("Remove artifact reference", item["path"] + "\nThe original file is preserved."):
            with app.store.db:
                app.store.db.execute("DELETE FROM evidence WHERE id=? AND handover=?", (item["id"], record["id"]))
        elif operation == "refresh":
            await app.io(lambda s: workflow.command_status(s, record, item))
        elif operation == "retry" and await app.confirm("Retry the selected command", json.dumps(item["command"]) + "\nCheckout: " + record["target"]["path"]):
            await app.io(lambda s: workflow.command_run(s, record, item["command"], retry=True))
        elif operation == "feedback":
            from .operations import followup
            text = json.dumps({"source": item.get("url", item["path"]), "file": item["path"], "revision": item.get("revision", item.get("head")),
                               "title": item["title"], "omitted_bytes": item.get("omitted_bytes", 0)}, indent=2)
            value = await app.form("Review evidence follow-up", [("text", "Exact message (local file reference)", text, "text")])
            if value and await app.confirm("Send once to " + record["name"], value["text"]):
                await app.io(lambda s: followup(s, app.host, record, value["text"]))
    elif action == "work-pr-link":
        if await repair_connection(app, record, force=True):
            p = await app.io(lambda s: forge.client(s, record))
            candidates = await app.io(lambda s: p.list(record["target"]["branch"]))
            pr = await select(app, "Select PR (or create one separately)", candidates)
            if pr:
                await app.io(lambda s: forge.reconcile(s, record, pr["id"]))
                record["pr_id"] = pr["id"]
                save_record(app.store, record)
                await app.io(lambda s: workflow.refresh_pr(s, record))
    elif action == "work-pr":
        if not await repair_connection(app, record):
            return
        await app.io(lambda s: workflow.refresh_pr(s, record))
    elif action == "work-pr-create":
        if not await repair_connection(app, record):
            return
        value = await app.form("Prepare PR publication", [("title", "Title", record["ticket"]["title"], "input"),
                 ("body", "Body", record["ticket"]["url"], "text"), ("base", "Published base branch", record["target"]["base"].removeprefix("origin/"), "input"),
                 ("draft", "Create as draft", True, "bool")], message="This does not push the branch. Publish it using Git Sidebar first.")
        if value:
            plan = await app.io(lambda s: forge.publication_plan(s, record, **value))
            if await app.confirm("Publish exactly this PR", json.dumps(plan, ensure_ascii=False, indent=2)):
                result = await app.io(lambda s: forge.publish(s, record, plan))
                record["pr_id"] = result["id"]
                save_record(app.store, record)
                await app.io(lambda s: workflow.refresh_pr(s, record))
    elif action == "work-pr-browser":
        from .operations import browser_url
        from .pr_links import items
        saved = items(app.store, record)
        if len(saved) != 1:
            return await globals()['action'](app, 'work-linked-prs', record)
        pr, item = saved[0]
        await asyncio.to_thread(webbrowser.open, browser_url(pr.get('url') or item.get('snapshot', {}).get('url', '')))
    elif action in ("work-pr-feedback", "work-pr-comments", "work-pr-failures"):
        from .operations import followup
        cached = False
        try:
            p = await app.io(lambda s: workflow.refresh_pr(s, record))
        except TaskError:
            p = next((e for e in app.store.evidence(record["id"]) if e["kind"] == "pr" and e.get("snapshot")), None)
            if p is None:
                raise
            cached = True
            app.status("Provider unavailable · inspecting saved feedback")
        snapshot = p["snapshot"]
        comments = [x for x in snapshot["feedback"] if x.get("text") and x.get("state") not in ("resolved", "DISMISSED")]
        checks = workflow.ci_results(snapshot)
        items = comments if action == "work-pr-comments" else [x for x in checks if x["state"] == "failed"] if action == "work-pr-failures" else comments + checks
        item = await select(app, "PR comments" if action == "work-pr-comments" else "Failing checks / builds" if action == "work-pr-failures" else "Select feedback or CI result", items, lambda x: x.get("title", x.get("text", x["id"]))[:140] + " · revision " + (x.get("revision") or "unknown"))
        if item:
            text = "Investigate and address the selected " + ("PR feedback" if action == "work-pr-comments" else "CI/build failure" if action == "work-pr-failures" else "feedback") + ". Verify whether it still applies to this checkout before changing code.\n\n"
            text += json.dumps({"source": snapshot["url"], "repository": snapshot["repository"], "head": snapshot["head"], "feedback": item}, ensure_ascii=False, indent=2)
            if cached:
                return await show(app, "Saved feedback · provider unavailable", text + "\n\nThis is a cached observation. Refresh successfully before preparing an agent follow-up.")
            from .handover import live_agent
            if not await app.io(lambda s: live_agent(app.host, record)):
                return await show(app, "Selected feedback · no linked agent available", text + "\n\nOpen or resume the recorded agent from Handovers before sending feedback.")
            value = await app.form("Review selected feedback", [("text", "Exact follow-up", text, "text")], message="Recipient: " + record["name"])
            if value and await app.confirm("Send once to the recorded conversation", record["name"] + "\n" + value["text"]):
                await app.io(lambda s: followup(s, app.host, record, value["text"]))
    elif action in ("work-logs", "work-failing-logs"):
        evidence = await app.io(lambda s: workflow.refresh_pr(s, record))
        p = await app.io(lambda s: forge.client(s, {**record, "forge": evidence["forge"]}))
        snapshot = evidence["snapshot"]
        runs = [x for x in workflow.ci_results(snapshot) if x in snapshot["runs"]]
        run = await select(app, "Choose failing CI run" if action == "work-failing-logs" else "Choose CI run", [x for x in runs if x["state"] == "failed"] if action == "work-failing-logs" else runs)
        if not run:
            return
        jobs = await app.io(lambda s: p.jobs(run["id"]))
        job = await select(app, "Choose job / log", [x for x in jobs if x["state"] == "failed"] if action == "work-failing-logs" else jobs)
        if job:
            data = await app.io(lambda s: p.log(run["id"], job["id"]))
            folder = app.store.root / "handovers" / record["id"] / "logs"
            from .core import private_dir
            private_dir(folder)
            file = folder / (uuid.uuid4().hex + ".txt")
            file.write_text(data["text"])
            item = await app.io(lambda s: workflow.artifact(s, record, file, job["title"] + " output"))
            app.store.observe(item["id"], record["id"], {**item, "url": job.get("url", run["url"]), "revision": run["revision"], "omitted_bytes": data["omitted_bytes"], "omission_is_minimum": data.get("omission_is_minimum", False)})
            await show(app, "Job output", data["text"][:16000] + f"\n\nPreview shows up to 16,000 characters. Saved excerpt: {file}\nDownload omitted bytes: {'at least ' if data.get('omission_is_minimum') else ''}{data['omitted_bytes']}\nFull provider output: {job.get('url', run['url'])}")
    elif action == "work-validation":
        items = await app.io(lambda s: workflow.commands(s, record))
        command = await select(app, "Run configured validation", [x for x in items if x["kind"] == "validation"])
        if command and await app.confirm("Run validation in this checkout", record["target"]["path"] + "\n" + json.dumps(command, indent=2)):
            await app.io(lambda s: workflow.command_run(s, record, command))
    elif action == "work-review":
        from . import review
        await app.io(lambda s: review.capability())
        value = await app.form("Prepare independent review", [("base", "Diff base commit", record["target"]["commit"], "input")], message="Frozen working-tree diff and validation evidence. A separate enforced read-only Codex run; no prompt-only fallback.")
        if value:
            item = await app.io(lambda s: review.prepare(s, record, value["base"]))
            if await app.confirm("Start independent read-only review", json.dumps(item, indent=2)):
                await app.io(lambda s: review.start(s, app.host, item))
    elif action == "work-compare":
        specs = []
        available = await app.io(lambda s: app.host.available_agents())
        for label in ("A", "B"):
            value = await app.form("Approach " + label, [("agent", "Agent", record["agent"], [(k, v) for k, v in available.items()]),
                        ("prompt", "Reviewed starting instructions", record.get("prompt", ""), "text")])
            if not value:
                return
            specs.append(value)
        if await app.confirm("Prepare two isolated drafts", "Same current HEAD, two different worktrees. Each draft still requires its own launch review."):
            await app.io(lambda s: workflow.alternatives(s, record, specs))
    elif action == "work-comparison":
        groups = [x for x in app.store.evidence(record["id"]) if x["kind"] == "comparison"]
        group = await select(app, "Comparison", groups)
        if group:
            data = []
            for ident in group["children"]:
                child = next((r for r in app.store.records("handovers") if r["id"] == ident), None)
                if not child:
                    data.append({"id": ident, "state": "history missing"})
                    continue
                try:
                    diff = await app.io(lambda s: git(child["target"]["path"], "diff", "--no-ext-diff", "--stat", group["base"]))
                except TaskError:
                    diff = "Checkout not yet prepared or unavailable"
                data.append({"id": ident, "target": child["target"], "state": child["stage"], "diff": diff, "validation": [x for x in app.store.evidence(ident) if x["kind"] == "validation"]})
            await show(app, "Comparison · no automatic winner", data)
    elif action == "work-overlap":
        from .operations import orch_snapshot
        state = await app.io(lambda s: orch_snapshot(app.host))
        await show(app, "Declared ownership and leases", {"linked_task": record.get("orch_id"), **state, "warnings": workflow.overlaps(state["tasks"]),
            "meaning": "Native declared scopes/leases only. They do not prevent external edits and are not evidence of a Git conflict."})


# Keep recursive UI dispatch independent of the local action name.
action_dispatch = action


async def global_action(app, action):
    if action == "work-search":
        return await search(app)
    if action == "work-scheduling":
        return await show(app, "Scheduling compatibility", await app.io(lambda s: workflow.scheduling(app.host)))
    if action == "work-integrations":
        owner = await app.choice("Integration bridge", [("Project Commands", "project-commands"), ("CLI Launcher", "cli-launcher")])
        if not owner:
            return
        config = app.store.config()
        value = await app.form("Version-1 JSON bridge · " + owner, [("argv", "Command argv JSON (empty array disables)", json.dumps(config.get("integrations", {}).get(owner, [])), "text")],
                               message="Use the owning module's documented JSON bridge. This configures a bridge only; it does not run setup or open workspaces.", submit="Save")
        if value:
            argv = json.loads(value["argv"])
            if not isinstance(argv, list) or any(not isinstance(x, str) or not x for x in argv) or argv and not Path(argv[0]).is_absolute():
                raise TaskError("Use an argv array with an absolute executable path; no shell command string.")
            config.setdefault("integrations", {})[owner] = argv
            app.store.save_config(config)
    if action == "work-bundles":
        bundles = [json.loads(x[0]) for x in app.store.db.execute("SELECT data FROM preferences WHERE key LIKE 'bundle:%'")]
        selected = await app.choice("Repository bundles", [("Create bundle", "new")] + [(x["title"], x["id"]) for x in bundles])
        if selected == "new":
            records = [r for r in app.store.records("handovers") if r.get("target")]
            value = await app.form("Create bundle", [("title", "Name", "", "input"), ("ids", "Handover IDs, one per line", "", "text")],
                                   message="\n".join(r["id"] + " · " + r["ticket"]["key"] + " · " + r["target"]["path"] for r in records), submit="Save")
            if value:
                ids = value["ids"].splitlines()
                chosen = [r for r in records if r["id"] in ids]
                if len(chosen) != len(set(ids)):
                    raise TaskError("Select only listed handover IDs.")
                workflow.save_bundle(app.store, value["title"], chosen)
        elif selected:
            bundle = app.store.preference(selected)
            if await app.confirm("Open bundle members through CLI Launcher", json.dumps(bundle, indent=2) + "\nOnly missing/failed members are retried; branches are not switched."):
                await show(app, "Bundle outcomes", await app.io(lambda s: workflow.open_bundle(s, selected)))
