"""One attention projection for the console, dock and optional notifications."""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .core import handover_tickets, handover_label, has_ticket, TaskError, now, ticket_key
from .handover import matching_agent
from .workflow import fingerprint, freshness


def age(at):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def project(store, agents, orch=(), host_available=True):
    from .productivity import pending as pending_record
    history = store.records("handovers")
    pending = {r["ticket"] for r in store.db.execute("SELECT ticket,data FROM writes") if json.loads(r["data"])["state"] == "pending"}
    items, linked, observed = [], set(), set()
    fingerprints, checkouts = {}, {}
    def add(key, record, reason, source, state="active", target=None, at=None, revision=None):
        observed.add(key)
        item = {"kind": "attention", "title": reason, "state": state, "source": source, "revision": revision,
                "target": target or {"record": record["id"]}, "task": record.get("ticket", {}).get("key", "Unlinked agent"),
                "worktree": (record.get("target") or {}).get("path", ""), "task_title": record.get("ticket", {}).get("title", "")}
        previous = next((x for x in store.evidence(record.get("id", "")) if x["id"] == key), {})
        item["since"] = at or (previous.get("since") if previous.get("state") != "cleared" and previous.get("source") == source else None) or now()
        epoch = previous.get("epoch", 0) + int(previous.get("state") == "cleared" or bool(previous) and previous.get("source") != source)
        item["epoch"] = epoch
        item["signature"] = hashlib.sha256(json.dumps({k: v for k, v in item.items() if k != "since"}, sort_keys=True).encode()).hexdigest()
        stored = store.observe(key, record.get("id", ""), item)
        # Acknowledgement applies to this source revision, never arbitrary later feedback.
        if store.preference("ack:" + key) != item["signature"] or source in ("agent-input", "uncertainty", "validation", "ci", "workflow"):
            items.append(stored)

    for r in history:
        rid = r["id"]
        a = matching_agent(agents, r)
        path = (r.get('target') or {}).get('path')
        if path and not r.get('archived_at'):
            from .checkout import snapshot
            if path not in checkouts:
                checkouts[path] = snapshot(path)
                store.set_preference('checkout:' + path, checkouts[path])
            observed_checkout = checkouts[path]
            if observed_checkout.get('state') != 'observed' or observed_checkout.get('branch', '').removeprefix('refs/heads/') != r['target']['branch']:
                add('attention:checkout:' + rid, r, 'Checkout unavailable or branch changed', 'uncertainty', revision=observed_checkout.get('branch'))
        if r.get('archived_at'):
            newer = [e for e in store.evidence(rid) if e.get('observed_at', '') > r['archived_at'] and e['kind'] in ('pr', 'validation', 'setup', 'review')]
            unresolved = any(e.get('error') or e['state'] in ('failed', 'pending', 'unknown', 'interrupted') or e['kind'] == 'pr' and (e.get('snapshot', {}).get('feedback') or any(c.get('state') == 'failed' for c in e.get('snapshot', {}).get('checks', []))) for e in newer)
            resurrect = unresolved or pending_record(r) or (a and a.get('status') not in ('done', 'idle')) or any(ticket_key(t) in pending for t in handover_tickets(r))
            if not resurrect:
                if a:
                    linked.add((a.get('generation'), a.get('terminal_id')))
                continue
            # A lifecycle update must make previously archived work visible again.
            r.pop('archived_at', None)
            from .handover import save_record
            save_record(store, r)
        if a:
            linked.add((a.get("generation"), a.get("terminal_id")))
        if any(ticket_key(t) in pending for t in handover_tickets(r)) or r.get("error") or r.get("orch_pending") or r["stage"].endswith("pending") or any(f["state"] == "pending" for f in r.get("followups", [])):
            add("attention:uncertain:" + rid, r, r.get("error") or "An operation needs reconciliation", "uncertainty")
        if a and a.get("status") in ("blocked", "waiting", "done"):
            done = a["status"] == "done"
            add("attention:agent:" + rid, r, "Finished work awaits review" if done else "Agent needs input; open to inspect", "agent-done" if done else "agent-input",
                target={"record": rid, "agent": {k: a.get(k) for k in ("pane", "terminal_id", "generation", "cwd", "name", "agent")}})
        elif r.get("pane") and not a:
            add("attention:agent:" + rid, r, "Recorded agent is unavailable; inspect or resume", "agent-unavailable", "unknown")
        for t in orch:
            if t["id"] == r.get("orch_id") and t["status"] in ("review", "blocked"):
                add("attention:orch:" + rid, r, "Native task: " + t["status"], "orch", revision=t.get("updated_at", t["status"]))
        command_ids = set()
        evidence = store.evidence(rid)
        path = (r.get('target') or {}).get('path')
        if path and any(e['kind'] in ('setup', 'validation', 'pr', 'review') for e in evidence):
            if path not in fingerprints:
                fingerprints[path] = fingerprint(path)
            checkout = fingerprints[path]
        else:
            checkout = {'state': 'unknown'}
        for e in evidence:
            if e["kind"] in ("setup", "validation"):
                command = e.get("command", {}).get("id", e["id"])
                if command in command_ids:
                    continue
                command_ids.add(command)
            if e["kind"] in ("setup", "validation") and e["state"] in ("failed", "unknown", "pending", "interrupted"):
                fresh = freshness(e, (r.get("target") or {}).get("path", ""), checkout)
                if e.get("freshness") != "current" and fresh == "current":
                    fresh = e.get("freshness", "unknown")
                add("attention:" + e["id"], r, e["title"] + ": " + e["state"] + " · " + fresh, "validation",
                    "unknown" if fresh != "current" else "active", at=e["observed_at"], revision=e.get("run_id"))
            if e['kind'] == 'workflow' and e['state'] in ('running', 'paused', 'completed'):
                detail = (e.get('phase', '') + ' · attempt ' + str(e.get('attempt', 0) + 1) + ' · ' + str(max(0, int((e.get('deadline', time.time()) - time.time()) / 60))) + ' min left') if e['state'] == 'running' else e.get('reason', '')
                add('attention:' + e['id'], r, e['title'] + ': ' + e['state'] + ' · ' + detail,
                    'workflow' if e['state'] == 'running' else 'uncertainty' if e['state'] == 'paused' else 'review', revision=e.get('run'), target={'record': r['id'], 'workflow': e.get('run')})
            if e["kind"] == "review" and e["state"] in ("passed", "failed", "unknown"):
                fresh = freshness(e, (r.get("target") or {}).get("path", ""), checkout)
                title = "Reviewer finished; inspect findings" if e["state"] == "passed" else "Reviewer execution: " + e["state"]
                add("attention:" + e["id"], r, title + " · " + fresh, "review", "active" if fresh == "current" else "stale", at=e["observed_at"], revision=e.get("checkout"))
            if e["kind"] != "pr":
                continue
            p = e.get("snapshot", {})
            if p.get("state") in ("closed", "completed", "abandoned") and not e.get("error"):
                continue
            stale = freshness(e, (r.get("target") or {}).get("path", ""), checkout) != "current" or age(e.get("observed_at")) > 120
            if e.get("error") or p.get("errors"):
                add("attention:pr-read:" + rid, r, "PR/CI source is incomplete or unavailable", "provider", "unknown", at=e["observed_at"])
            latest_runs = {}
            for run in sorted(p.get("runs", []), key=lambda x: int(x["id"]), reverse=True):
                latest_runs.setdefault((run.get("group", run["id"]), run.get("revision")), run)
            for check in p.get("checks", []) + list(latest_runs.values()):
                if check["state"] != "failed":
                    continue
                current = not stale and check.get("revision") and check["revision"] == p.get("head")
                add("attention:ci:" + rid + ":" + check["id"], r, check["title"] + (": failed at current head" if current else ": prior/unknown-revision failure"), "ci",
                    "active" if current else "stale", target={"record": rid, "url": check.get("url")}, at=e["observed_at"], revision=check.get("revision"))
            feedback = p.get("feedback", [])
            latest_reviews = {}
            for f in feedback:
                if f["id"].startswith("reviews:"):
                    latest_reviews[f.get("author")] = f["id"]
            for f in feedback:
                if f["id"].startswith("reviews:") and latest_reviews.get(f.get("author")) != f["id"]:
                    continue
                if f.get("state") in ("resolved", "DISMISSED") or not f.get("text"):
                    continue
                add("attention:feedback:" + rid + ":" + f["id"], r, "PR feedback: " + f["text"][:100], "pr-feedback", "stale" if stale else "active",
                    target={"record": rid, "url": f.get("url")}, at=f.get("at") or e["observed_at"], revision=[p.get("head"), f.get("state"), f["text"]])

    for a in agents:
        if (a.get("generation"), a.get("terminal_id")) in linked or not a.get("generation") or not a.get("terminal_id") or a.get("status") not in ("blocked", "waiting", "done"):
            continue
        identity = a["generation"] + ":" + a["terminal_id"]
        done = a["status"] == "done"
        add("attention:native:" + identity, {"target": {"path": a.get("cwd", "")}},
            (a.get("name") or a.get("agent", "Agent")) + (" finished; review required" if done else " needs input; inspect request"),
            "agent-done" if done else "agent-input", target={"agent": {k: a.get(k) for k in ("pane", "terminal_id", "generation", "cwd", "name", "agent")}})

    for e in store.evidence():
        if e.get("kind") != "attention" or e["id"] in observed or e["state"] == "cleared":
            continue
        if not host_available and e.get("source", "").startswith("agent"):
            old = store.observe(e["id"], e["handover"], {**e, "state": "unknown", "title": "Agent source unavailable · " + e["title"].removeprefix("Agent source unavailable · ")})
            items.append(old)
        else:
            store.observe(e["id"], e["handover"], {**e, "state": "cleared", "observed_at": now()})
    return items


def acknowledge(store, item):
    if item["source"] in ("agent-input", "uncertainty", "validation", "ci", "workflow"):
        raise TaskError("This condition clears only at its source. Opening or acknowledging it cannot approve or resolve it.")
    store.set_preference("ack:" + item["id"], item["signature"])


def navigate(host, item):
    target = item["target"]
    agent = target.get("agent")
    if agent:
        candidates = host.agents()
        match = next((a for a in candidates if all(a.get(k) == agent.get(k) for k in ("generation", "terminal_id", "pane", "cwd", "agent", "name"))), None)
        if not match:
            raise TaskError("Navigation target changed. Refresh attention; no pane was focused.")
        host.call("pane.focus", pane=match["pane"])
    return target.get("record")


def notifications(store, host, items):
    if not store.preference("attention-notifications", False):
        return
    # Native agent/usage notifications retain their own delivery owner.
    from .productivity import snoozed
    for e in items:
        if snoozed(store, e):
            continue
        if e["source"] not in ("ci", "pr-feedback", "validation", "review") or e["state"] != "active":
            continue
        key = "notified:" + e["id"]
        if store.preference(key) == e["signature"]:
            continue
        # Reserve before delivery: an uncertain reply must not cause duplicate notifications.
        store.set_preference(key, e["signature"])
        host.call("ui.notification.push", text=e["task"] + " · " + e["title"], level="warning", dedupe_key=e["id"])
