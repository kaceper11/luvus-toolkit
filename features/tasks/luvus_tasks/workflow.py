"""Task evidence and cross-module contracts. No second command runner."""
from __future__ import annotations

from contextlib import closing
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .core import handover_tickets, handover_label, has_ticket, TaskError, atomic_json, clean, now, private_dir
from .handover import git, save_record, target_plan


def fingerprint(path):
    """Include untracked contents; status text alone cannot prove validation freshness."""
    try:
        path = Path(path).resolve()
        head = git(path, "rev-parse", "HEAD")
        names = git(path, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
        digest = hashlib.sha256(head.encode())
        total = 0
        for name in sorted(set(filter(None, names))):
            p = Path(path) / name
            if p.parent.resolve() != p.parent.absolute():
                return {"state": "unknown", "reason": "Symlinked parent path cannot be fingerprinted safely"}
            before = p.lstat() if p.exists() or p.is_symlink() else None
            if p.is_symlink():
                raw = os.readlink(p).encode()
            elif not p.exists():
                raw = b"<deleted>"
            elif not p.is_file():
                return {"head": head, "state": "unknown", "reason": "Submodule/directory content not fingerprinted"}
            else:
                total += p.stat().st_size
                # ponytail: bound local hashing to 64 MiB; larger checkouts need a producer snapshot.
                if total > 64 * 1024 * 1024:
                    return {"head": head, "state": "unknown", "reason": "Checkout exceeds 64 MiB fingerprint budget"}
                raw = p.read_bytes()
            after = p.lstat() if p.exists() or p.is_symlink() else None
            signature = lambda stat: None if stat is None else (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_mode)
            if signature(before) != signature(after):
                return {"state": "unknown", "reason": "File changed while observing checkout"}
            digest.update(name.encode() + b"\0" + str(before.st_mode if before else 0).encode() + b"\0" + hashlib.sha256(raw).digest())
        if git(path, "rev-parse", "HEAD") != head or git(path, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0") != names:
            return {"state": "unknown", "reason": "Checkout changed while observing files"}
        return {"head": head, "state": "observed", "digest": digest.hexdigest()}
    except (TaskError, OSError) as exc:
        return {"state": "unknown", "reason": str(exc)}


def freshness(evidence, path, current=None):
    if evidence.get("stale"):
        return "stale"
    prior = evidence.get("checkout", {})
    current = fingerprint(path) if current is None else current
    if prior.get("state") != "observed" or current.get("state") != "observed":
        return "unknown"
    return "current" if prior == current else "stale"


def evidence_text(record, evidence):
    data = summary(record)
    lines = ["Objective: " + str(data.get("objective") or record["ticket"]["title"])]
    for key in ("decisions", "remaining", "paths"):
        if data.get(key):
            lines.append(key.title() + ": " + str(data[key]))
    if not data.get("decisions") and not data.get("remaining"):
        lines.append("No decisions or remaining work recorded yet. Use Summary to add them.")
    target = (record.get("target") or {})
    if target.get("path"):
        lines.append("Checkout: " + target["path"])
    pr = next((e for e in evidence if e["kind"] == "pr"), None)
    if pr:
        lines += ["\nPR / CI (saved observation)", pr_status(pr), "Checked: " + pr.get("observed_at", "unknown")]
        if pr.get("error"):
            lines.append("Use Workflows → Select PR connection to resolve an ambiguous link, or Refresh to retry a source failure.")
        if pr.get("snapshot", {}).get("url"):
            lines.append(pr["snapshot"]["url"])
    items = [e for e in evidence if e["kind"] not in ("pr", "launch", "summary")]
    lines.append("\nEvidence · " + str(len(items)) + " entries")
    if not items:
        lines.append("No artifacts, validation or review results yet. Add artifact or open Workflows to get started.")
    for item in items[:10]:
        state = artifact_state(item) if item.get("path") else item["state"]
        lines.append("• " + item["title"] + " · " + state + (" · saved freshness: " + item.get("freshness", "unknown") if item.get("checkout") else ""))
    if len(items) > 10:
        lines.append(f"Showing 10 of {len(items)}. Open Evidence for all entries.")
    lines.append("\nEvidence opens full details, files and run actions. Timeline shows lifecycle changes. Workflows contains PR/CI and validation actions.")
    return "\n".join(lines)


def artifact(store, record, source, title=""):
    path = Path(source).expanduser().resolve(strict=True)
    if not path.is_file():
        raise TaskError("Choose a regular artifact file.")
    stat = path.stat()
    data = {"kind": "artifact", "title": title or path.name, "path": str(path), "state": "available", "source": "user-selected output",
            "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream", "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "created_at": now(), "creation_source": "association time; file creation unknown"}
    return store.observe("artifact:" + uuid.uuid4().hex, record["id"], data)


def artifact_state(item):
    try:
        p = Path(item["path"])
        stat = p.stat()
        if not p.is_file():
            return "missing"
        return "available" if (stat.st_size, stat.st_mtime_ns) == (item.get("size"), item.get("mtime_ns")) else "changed"
    except OSError:
        return "missing"


def open_artifact(item, reveal=False):
    if artifact_state(item) == "missing":
        raise TaskError("Artifact is missing. Its reference has been preserved.")
    path = str(Path(item["path"]).resolve())
    if sys.platform == "darwin":
        argv = ["open", "-R", path] if reveal else ["open", path]
    elif os.name == "nt":
        if reveal:
            argv = ["explorer.exe", "/select,", path]
        else:
            os.startfile(path)
            return
    else:
        argv = ["xdg-open", str(Path(path).parent) if reveal else path]
    subprocess.run(argv, check=True, timeout=15, capture_output=True)


def summary(record):
    return record.get("summary") or {"objective": record["ticket"]["title"], "decisions": "", "remaining": "",
              "paths": "\n".join(x.get("relative", x.get("target", "")) for x in record.get("context", [])),
              "source_session": record.get("session", "unknown"), "source_revision": (record.get("target") or {}).get("commit", "unknown")}


def bridge(store, owner, method, **params):
    argv = store.config().get("integrations", {}).get(owner)
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv) or not Path(argv[0]).is_absolute():
        raise TaskError(owner + " integration unavailable. Configure its version-1 JSON bridge; no substitute runner is used.")
    if owner == "project-commands":
        return commands_bridge(argv, method, params)
    request = {"version": 1, "request_id": uuid.uuid4().hex, "operation": method, **params}
    return exchange(argv, request, owner)


def exchange(argv, request, owner):
    try:
        result = subprocess.run(argv, input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=30)
        if len(result.stdout.encode()) > 1_048_576 or result.returncode:
            raise ValueError("Bridge failed")
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or data.get("version") != 1 or "result" not in data:
            raise ValueError("Unsupported bridge response")
        if "request_id" in request and (data.get("request_id") != request["request_id"] or data.get("cwd") != request.get("cwd")):
            raise ValueError("Bridge response identity mismatch")
        return data["result"]
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise TaskError(owner + " bridge unavailable or response uncertain. Inspect before retrying a submitted action.") from exc


def commands_bridge(argv, method, params):
    """Adapt Project Commands' public version-1 API; never import its internals."""
    root = params["worktree"]
    def call(action, **fields):
        return exchange(argv, {"version": 1, "action": action, "root": root, **fields}, "project-commands")
    if method == "describe":
        return call("describe")
    if method in ("commands", "run"):
        # config requires an existing root even when the requested worktree isn't prepared yet.
        configured = exchange(argv, {"version": 1, "action": "config", "root": root if Path(root).is_dir() else params["repository"]}, "project-commands")
        definitions = configured.get("projects", {}).get(root, {}).get("commands", [])
        if method == "commands":
            return [{"id": d["id"], "title": d["name"], "kind": "setup" if d.get("category") == "setup" else "validation",
                     "producer_definition": d} for d in definitions if d.get("kind", "command") == "command"]
        approved = params.get("definition")
        current = next((d for d in definitions if d["id"] == params["command_id"]), None)
        if current is None or approved != current:
            raise TaskError("Configure/review this exact checkout's command in Project Commands before running it. Its definition is missing or changed.")
        session = call("session")
        if params.get("session") and session != params["session"]:
            raise TaskError("Reviewed workflow session changed; command was not started.")
        if params.get("deadline") is not None:
            import time
            if time.time() >= params["deadline"]:
                raise TaskError("Workflow deadline reached before command dispatch.")
        result = call("run", command=params["command_id"], request_id=params["request_id"], reviewed=approved, session=session)
    elif method in ("result", "evidence"):
        result = call("evidence" if method == "evidence" else "status", request_id=params["request_id"], **({"run": params["run_id"]} if params.get("run_id") else {}))
        if isinstance(result, list):
            result = next((r for r in result if r.get("request") == params["request_id"]), None)
            if result is None:
                raise TaskError("Submitted run is not in the producer's recent results. Inspect Project Commands; no run was retried.")
    else:
        raise TaskError("Unsupported Project Commands contract operation.")
    return {"request_id": result["request"], "run_id": result["id"], "worktree": result["root"],
            "state": {"starting": "pending", "cancelling": "running"}.get(result["state"], result["state"]),
            "freshness": result.get("freshness", "unknown"), "source": result}


def commands(store, record):
    result = bridge(store, "project-commands", "commands", repository=record["target"]["repo"], worktree=record["target"]["path"])
    if not isinstance(result, list) or any(not isinstance(x, dict) or not x.get("id") or x.get("kind") not in ("setup", "validation") for x in result):
        raise TaskError("Invalid Project Commands discovery response.")
    return result


def command_run(store, record, command, retry=False):
    scope = record["id"] + ":" + command["id"]
    with store.lock("command-" + hashlib.sha256(scope.encode()).hexdigest()[:20]):
        prior = next((e for e in store.evidence(record["id"]) if e.get("command", {}).get("id") == command["id"]), None)
        if prior and not retry:
            return command_status(store, record, prior)
        if prior and prior["state"] not in ("passed", "failed", "cancelled", "interrupted"):
            raise TaskError("Run outcome is uncertain or still active; inspect it before starting another.")
        request_id = uuid.uuid4().hex
        key = "command:" + request_id
        checkout = fingerprint(record["target"]["path"])
        pending = store.observe(key, record["id"], {"kind": command["kind"], "title": command.get("title", command["id"]),
                "state": "pending", "request_id": request_id, "command": command, "checkout": checkout, "worktree": record["target"]["path"]})
        result = bridge(store, "project-commands", "run", request_id=request_id, command_id=command["id"],
                        repository=record["target"]["repo"], worktree=record["target"]["path"], definition=command.get("producer_definition"), approved=True)
        return accept_run(store, record, pending, result)


def accept_run(store, record, item, result):
    if result.get("request_id") != item["request_id"] or Path(result.get("worktree", "")).resolve() != Path(record["target"]["path"]).resolve() or not result.get("run_id"):
        raise TaskError("Project Commands result belongs to another request or checkout. Submission remains uncertain.")
    state = result.get("state", "unknown")
    if state not in ("running", "pending", "passed", "failed", "cancelled", "interrupted", "unknown"):
        state = "unknown"
    data = {**item, "state": state, "run_id": result["run_id"], "producer": result, "observed_at": now()}
    if state in ("passed", "failed"):
        data["freshness"] = freshness(item, record["target"]["path"])
        if result.get("freshness") != "current":
            data["freshness"] = result.get("freshness", "unknown")
    return store.observe(item["id"], record["id"], data)


def command_status(store, record, item):
    result = bridge(store, "project-commands", "result", request_id=item["request_id"], run_id=item.get("run_id"), worktree=record["target"]["path"])
    return accept_run(store, record, item, result)


def ensure_setup(store, record):
    item = command_run(store, record, record["setup"])
    if item["state"] != "passed" or item.get("freshness") != "current":
        raise TaskError("Selected setup is " + item["state"] + ". Refresh/reconcile its run in Evidence, then retry this handover; no agent started.")


def refresh_pr(store, record, discover=False):
    from .forge import client, choices, preferred
    record = copy.deepcopy(record)
    ident = "pr:" + record["id"]
    prior = next((e for e in store.evidence(record["id"]) if e["id"] == ident), {})
    def selection():
        saved = next((r for r in store.records("handovers") if r["id"] == record["id"]), {})
        return {k: saved.get(k) for k in ("target", "forge", "pr_id")}
    before = selection()
    try:
        if not record.get("forge"):
            link = preferred(store, record)
            if not link:
                raise TaskError("PR connection needs repair. Open Connection health or Select PR connection to choose the account for this repository.")
            record["forge"] = link
        p = client(store, record)
        if discover:
            from .pr_links import refresh as discover_links
            discover_links(store, record, p, record["forge"])
            saved = next((r for r in store.records("handovers") if r["id"] == record["id"]), {})
            if not saved or saved.get("target") != record.get("target") or saved.get("forge", record["forge"]) != record["forge"]:
                raise TaskError("Handover PR target changed during discovery. Refresh again.")
            if saved:
                record.update(saved)
                before = selection()
        if not record.get("pr_id"):
            from .pr_links import key as pr_key
            candidates = [v for v in p.list(record["target"]["branch"]) if pr_key(record["forge"], v["id"]) not in record.get("ignored_prs", [])]
            if len(candidates) > 1 and not discover:
                raise TaskError("Multiple PRs match this branch. Select the intended PR in Workflows.")
            if len(candidates) == 1:
                record["pr_id"] = candidates[0]["id"]
        data = p.snapshot(record["pr_id"]) if record.get("pr_id") else p.snapshot(branch=record["target"]["branch"])
        if data.get("errors"):
            raise TaskError("PR/CI read is incomplete; prior evidence retained: " + "; ".join(data["errors"]))
        if data["head_branch"] != record["target"]["branch"]:
            raise TaskError("Selected PR no longer matches the recorded branch.")
        checkout = fingerprint(record["target"]["path"])
        stale = data.get("state") != "unpublished" and (not data.get("head") or data["head"] != checkout.get("head"))
        if selection() != before:
            raise TaskError("Handover PR target changed during refresh. Old response discarded.")
        return store.observe(ident, record["id"], {"kind": "pr", "title": ("PR #" + data["id"] if data["id"] else "Branch CI · no open PR") + " · " + data["title"],
                   "state": data["state"], "snapshot": data, "forge": record["forge"], "checkout": checkout, "stale": stale, "error": "", "observed_at": now()})
    except TaskError as exc:
        if selection() != before:
            raise
        store.observe(ident, record["id"], {**prior, "kind": "pr", "title": prior.get("title", "PR unavailable"),
                      "state": "unknown", "stale": True, "error": str(exc), "observed_at": now()})
        raise


def poll_prs(store, limit=4):
    """Bounded read-only discovery, shared by event hooks and the console timer."""
    import time
    groups = {}
    config_hash = hashlib.sha256(json.dumps(store.config().get("connections", []), sort_keys=True).encode()).hexdigest()
    for record in store.records("handovers"):
        if record.get("archived_at"):
            continue
        target = record.get("target") or {}
        if not target.get("path") or not target.get("branch") or (not Path(target["path"]).is_dir() and not record.get("forge")):
            continue
        scope = json.dumps([str(Path(target["path"]).resolve()), target["branch"], record.get("forge"), record.get("pr_id"), [t.get("url", "") for t in handover_tickets(record)], record.get("ignored_prs", []), config_hash], sort_keys=True)
        key = "pr-poll:" + hashlib.sha256(scope.encode()).hexdigest()
        groups.setdefault(key, []).append(record)
    due = sorted(groups, key=lambda key: store.preference(key, {}).get("next", 0))
    refreshed = 0
    try:
        with store.lock("pr-poll"):
            for key in due:
                if refreshed >= limit:
                    break
                if store.preference(key, {}).get("next", 0) > time.time():
                    continue
                # Reserve before network calls, so concurrent hooks cannot duplicate reads.
                store.set_preference(key, {"next": time.time() + 300})
                refreshed += 1
                record = groups[key][0]
                try:
                    if not Path(record['target']['path']).is_dir():
                        from .pr_links import refresh as discover_links
                        discover_links(store, record)
                        store.set_preference(key, {"next": time.time() + 60})
                        store.set_preference('pr-discovery-error:' + record['id'], '')
                        continue
                    item = refresh_pr(store, record, discover=True)
                    store.set_preference('pr-discovery-error:' + record['id'], '')
                    store.set_preference(key, {"next": time.time() + 60})
                except TaskError as exc:
                    store.set_preference('pr-discovery-error:' + record['id'], str(exc))
                    item = next((e for e in store.evidence(record["id"]) if e["id"] == "pr:" + record["id"]), None)
                    if item is None:
                        continue  # Target changed/deleted during discovery; discard the old read.
                for other in groups[key][1:]:
                    store.observe("pr:" + other["id"], other["id"], item)
    except TaskError as exc:
        if "Operation already active" not in str(exc):
            raise
    return refreshed


def pr_status(item, path=None):
    """Compact saved source status; no provider calls during sidebar rendering."""
    from .attention import age
    if not item:
        return "PR/CI not checked yet"
    p = item.get("snapshot", {})
    if item.get("error"):
        return "PR/CI unavailable · " + item["error"]
    prefix = "PR #" + p["id"] + " · " + p["state"] if p.get("id") else "No open PR · branch CI"
    results = ci_results(p)
    def state(revision):
        states = [r["state"] for r in results if revision and r.get("revision") == revision]
        return "failed" if "failed" in states else "running" if "running" in states else "passed" if states and all(s == "passed" for s in states) else "unknown / no checks"
    ci = "head CI " + state(p.get("head"))
    if p.get("merge"):
        ci += " · merge CI " + state(p["merge"])
    if any(not r.get("revision") or r.get("revision") not in (p.get("head"), p.get("merge")) for r in results):
        ci += " · prior/unknown-revision results"
    stale = item.get("stale") or age(item.get("observed_at")) > 120 or path is not None and freshness(item, path) != "current"
    reviews = [f for f in p.get("feedback", []) if f.get("state") not in ("resolved", "DISMISSED") and f.get("text")]
    return prefix + " · " + ci + " · " + str(len(reviews)) + " feedback" + (" · stale" if stale else "")


def ci_results(snapshot):
    latest = {}
    for run in sorted(snapshot.get("runs", []), key=lambda x: int(x["id"]), reverse=True):
        latest.setdefault((run.get("group", run["id"]), run.get("revision")), run)
    return snapshot.get("checks", []) + list(latest.values())


def alternatives(store, record, specs):
    """Prepare two durable drafts only; each uses the normal reviewed launch flow."""
    if len(specs) != 2:
        raise TaskError("Choose exactly two approaches.")
    base = git(record["target"]["path"], "rev-parse", "HEAD")
    children = []
    for spec in specs:
        if not spec["prompt"].strip():
            raise TaskError("Both approaches require instructions.")
        ident = str(uuid.uuid4())
        branch = "compare/" + ident[:12]
        target = target_plan(record["target"]["repo"], branch, base, True, store.root)
        child = {"id": ident, "name": "task-" + ident[:16], "ticket": record["ticket"], "tickets": copy.deepcopy(handover_tickets(record)), "title": record.get("title", ""), "created": now(),
                 "stage": "draft", "context": copy.deepcopy(record.get("context", [])), "notes": "", "agent": spec["agent"],
                 "preset": "Custom", "prompt": spec["prompt"], "prompt_mode": "custom", "target": target,
                 "inputs": {"repo": target["repo"], "branch": branch, "base": base, "new": True, "worktree_parent": ""},
                 "comparison_parent": record["id"]}
        child["prepared_inputs"] = dict(child["inputs"])
        children.append(child)
    if children[0]["target"]["path"] == children[1]["target"]["path"]:
        raise TaskError("Alternative checkouts must be separate.")
    for child in children:
        save_record(store, child)
    store.observe("comparison:" + uuid.uuid4().hex, record["id"], {"kind": "comparison", "title": "Two approaches", "state": "draft",
                  "base": base, "children": [x["id"] for x in children]})
    return children


def save_bundle(store, title, records):
    if not title.strip() or not 1 <= len(records) <= 32 or len({str(Path(r["target"]["path"]).resolve()) for r in records}) != len(records):
        raise TaskError("Name the bundle and select 1–32 distinct checkout paths.")
    ident = "bundle:" + uuid.uuid4().hex
    members = [{"handover": r["id"], "repository": r["target"]["repo"], "worktree": r["target"]["path"], "branch": r["target"]["branch"]} for r in records]
    store.set_preference(ident, {"id": ident, "title": title, "members": members, "outcomes": {}})
    return ident


def bundle_snapshot(bundle):
    members = [{"cwd": os.path.normcase(str(Path(m["worktree"]).resolve())),
                "branch": {"kind": "branch", "value": m["branch"]}} for m in bundle["members"]]
    snapshot = {"id": bundle["id"], "name": bundle["title"], "members": members}
    snapshot["revision"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return snapshot


def bundle_api(root):
    """Read-only Launcher contract, with an explicit Tasks state directory."""
    import sqlite3
    request = {}
    try:
        raw = sys.stdin.read(1_048_577)
        if len(raw.encode()) > 1_048_576:
            raise TaskError("Request exceeds 1 MiB.")
        request = json.loads(raw)
        if not isinstance(request, dict):
            request = {}
            raise TaskError("Request must be an object.")
        if request.get("version") != 1 or not request.get("request_id") or not Path(request.get("cwd", "")).is_absolute():
            raise TaskError("Version, request ID and absolute cwd required.")
        cwd = os.path.normcase(str(Path(request["cwd"]).resolve()))
        with closing(sqlite3.connect((Path(root).resolve() / "tasks.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            bundles = [bundle_snapshot(json.loads(row[0])) for row in db.execute("SELECT data FROM preferences WHERE key LIKE 'bundle:%'")]
        bundles = [b for b in bundles if any(m["cwd"] == cwd for m in b["members"])]
        if request.get("operation") == "bundles.list":
            result = bundles
        elif request.get("operation") == "bundles.get":
            result = next((b for b in bundles if b["id"] == request.get("id")), None)
            if result is None:
                raise TaskError("Bundle is missing or outside this checkout.")
        else:
            raise TaskError("Only read-only bundle operations are supported.")
        response = {"version": 1, "request_id": request["request_id"], "cwd": cwd, "result": result}
        encoded = json.dumps(response)
        if len(encoded.encode()) > 1_048_576:
            raise TaskError("Response exceeds 1 MiB.")
        print(encoded)
        return 0
    except (TaskError, ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"version": 1, "request_id": request.get("request_id"), "error": str(exc)}))
        return 1


def open_bundle(store, ident):
    bundle = store.preference(ident)
    if not bundle:
        raise TaskError("Bundle no longer exists.")
    snapshot = bundle_snapshot(bundle)
    cwd = next((m["cwd"] for m in snapshot["members"] if Path(m["cwd"]).is_dir()), None)
    if not cwd:
        raise TaskError("All bundle checkouts are unavailable; no workspace opened.")
    with store.lock("bundle-open"):
        # Launcher revalidates this snapshot through bundle_api and owns workspace reuse.
        results = bridge(store, "cli-launcher", "bundles.open", cwd=cwd, bundle=snapshot, layouts=False, confirm=True)
        expected = {m["cwd"] for m in snapshot["members"]}
        if not isinstance(results, list) or len(results) != len(expected) or {r.get("cwd") for r in results} != expected or any(r.get("state") not in ("succeeded", "failed") for r in results):
            raise TaskError("Launcher returned unmatched member outcomes. Inspect Launcher before retrying.")
        bundle["outcomes"] = {r["cwd"]: r for r in results}
        store.set_preference(ident, bundle)
    return bundle


def scheduling(host):
    capabilities = host.capabilities()
    methods = set(capabilities.get("methods", [])) | {x["method"] for x in capabilities.get("method_contracts", [])}
    return "Native automation is available. Configure a concrete schedule in Luvus; none is enabled by Tasks." if "automation.create" in methods else "Native automation is unavailable on this server. No schedule, upgrade, or custom scheduler was enabled."


def overlaps(tasks):
    """Conservative declared-scope comparison, not a claim about actual edits."""
    import fnmatch
    import itertools
    result = []
    for a, b in itertools.combinations(tasks, 2):
        if a.get("status") in ("done", "merged") or b.get("status") in ("done", "merged"):
            continue
        matches = []
        for left, right in itertools.product(a.get("paths", []), b.get("paths", [])):
            l, r = left.replace("\\", "/").rstrip("/"), right.replace("\\", "/").rstrip("/")
            if l == r or fnmatch.fnmatchcase(l, r) or fnmatch.fnmatchcase(r, l) or l.startswith(r + "/") or r.startswith(l + "/"):
                matches.append({"left": left, "right": right, "kind": "declared overlap"})
            elif any(c in l + r for c in "*?["):
                lp, rp = l.split("*")[0].split("?")[0].split("[")[0], r.split("*")[0].split("?")[0].split("[")[0]
                if lp.startswith(rp) or rp.startswith(lp):
                    matches.append({"left": left, "right": right, "kind": "possible glob overlap"})
        result.append({"tasks": [a["id"], b["id"]], "state": "overlap warning" if matches else "no overlap found in declared scopes",
                       "matches": matches, "scope": "Native lease namespace; undeclared paths and actual changes are not covered"})
    return result
