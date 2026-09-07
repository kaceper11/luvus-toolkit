from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import string
import subprocess
import sys
import uuid
from urllib.parse import urlsplit
from pathlib import Path

from .core import handover_tickets, handover_label, has_ticket, TaskError, clean, now, private_dir, ticket_key

AGENTS = {"Codex": "codex", "Claude Code": "claude", "Copilot CLI": "copilot", "OpenCode": "opencode", "Muse Code": "muse"}


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *map(str, args)], capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise TaskError(clean(result.stderr or "Git command failed."))
    return result.stdout.rstrip("\n")


def repository(path):
    if not str(path).strip():
        raise TaskError("Choose a workspace or enter the local Git checkout path first.")
    return str(Path(git(path, "rev-parse", "--show-toplevel")).resolve())


def validate_issue_repository(ticket, path):
    """GitHub handovers may only operate in the issue's own repository."""
    if ticket.get("provider") != "GitHub":
        return
    expected = ticket["project"].casefold()
    try:
        remote = git(path, "remote", "get-url", "origin").strip()
    except TaskError:
        remote = ""
    # Normalize ordinary HTTPS, ssh://, and Git's scp-style SSH remotes.
    if "://" not in remote and re.match(r"(?:[^/@:]+@)?github\.com:", remote, re.I):
        remote = "ssh://" + re.sub(r"github\.com:", "github.com/", remote, count=1, flags=re.I)
    parsed = urlsplit(remote)
    actual = parsed.path.strip("/").removesuffix(".git").casefold()
    if parsed.hostname != "github.com" or actual != expected:
        raise TaskError(f"This issue belongs to {ticket['project']}. Choose a checkout whose origin points to github.com/{ticket['project']}; the selected repository does not match. No handover was launched.")


def worktrees(repo):
    raw = git(repo, "worktree", "list", "--porcelain", "-z")
    result, entry = [], {}
    for field in raw.split("\0"):
        if field.startswith("worktree "):
            if entry:
                result.append(entry)
            entry = {"path": field[9:]}
        elif field.startswith("branch "):
            entry["branch"] = field[7:].removeprefix("refs/heads/")
    if entry:
        result.append(entry)
    return result


def branch_name(pattern, ticket):
    allowed = {"key": re.sub(r"[^a-z0-9-]+", "-", ticket["key"].lower()).strip("-"),
               "type": "feature", "number": re.split(r"[#-]", ticket["key"])[-1],
               "repo": re.sub(r"[^a-z0-9-]+", "-", ticket.get("project", "repo").split("/")[-1].lower()),
               "slug": re.sub(r"[^a-z0-9]+", "-", ticket["title"].lower()).strip("-")[:60] or "task"}
    try:
        if any(field is not None and (field not in allowed or spec or conversion) for _, field, spec, conversion in string.Formatter().parse(pattern)):
            raise ValueError("Unsupported branch placeholder")
        result = pattern.format(**allowed)
    except (ValueError, KeyError, IndexError) as exc:
        raise TaskError("Branch pattern supports {type}, {key}, {number}, {repo}, and {slug}.") from exc
    return result


def base_branches(repo):
    return [b for b in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/", "refs/remotes/").splitlines()
            if not b.endswith("/HEAD")]


def default_base(repo, configured=""):
    if not repo:
        return ""
    try:
        branches = base_branches(repo)
    except TaskError:
        return ""
    if configured in branches:
        return configured
    try:
        remote = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        if remote in branches:
            return remote
    except TaskError:
        pass
    # A missing origin/HEAD is common in local checkouts. Do not guess between main and master.
    defaults = [name for name in ("main", "master") if name in branches or "origin/" + name in branches]
    if len(defaults) == 1:
        name = defaults[0]
        return "origin/" + name if "origin/" + name in branches else name
    return ""


def target_plan(repo, branch, base, new, root, worktree_parent=""):
    repo = repository(repo)
    if not branch or not branch.strip():
        raise TaskError("Choose a task branch before starting.")
    if new and (not base or not base.strip()):
        raise TaskError("Choose a base branch in the draft's Target section before starting. No worktree was created.")
    git(repo, "check-ref-format", "--branch", branch)
    local = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines()
    if new and branch in local:
        raise TaskError("Branch already exists; select existing branch instead.")
    if not new and branch not in local:
        raise TaskError("Select an existing local branch (fetch remote branches yourself first).")
    revision = base if new else branch
    try:
        commit = git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    except TaskError as exc:
        choices = base_branches(repo)
        hint = "Choose a valid base: " + ", ".join(choices[:12]) if choices else "This checkout has no available commits. Choose the task's existing repository."
        raise TaskError(f"Cannot resolve '{revision}' in {repo}. {hint} No worktree was created.") from exc
    existing = next((w["path"] for w in worktrees(repo) if w.get("branch") == branch), None)
    digest = hashlib.sha256((repo + "\0" + branch).encode()).hexdigest()[:16]
    parent = Path(worktree_parent).expanduser() if worktree_parent else Path(root) / "worktrees"
    if not parent.is_absolute():
        raise TaskError("Worktree parent must be an absolute folder path.")
    path = existing or str(parent / (Path(repo).name + "-" + digest))
    if not existing and Path(path).exists():
        raise TaskError("Planned worktree path already exists but is not registered with Git.")
    return {"repo": repo, "branch": branch, "base": base, "commit": commit,
            "new": new, "existing": bool(existing), "path": path}


def ensure_target(plan):
    if not plan["new"] and git(plan["repo"], "rev-parse", "--verify", "--end-of-options", plan["branch"] + "^{commit}") != plan["commit"]:
        raise TaskError("Existing branch moved since review. Review the target again before launching.")
    existing = next((w for w in worktrees(plan["repo"]) if w.get("branch") == plan["branch"]), None)
    if existing:
        if Path(existing["path"]).resolve() != Path(plan["path"]).resolve():
            raise TaskError("Branch checkout changed since review; review a new handover.")
        if git(existing["path"], "rev-parse", "HEAD") != plan["commit"]:
            raise TaskError("Target commit changed since review. Review a new handover.")
        return
    private_dir(Path(plan["path"]).parent)
    args = ["worktree", "add"]
    if plan["new"]:
        args += ["-b", plan["branch"], plan["path"], plan["commit"]]
    else:
        args += [plan["path"], plan["branch"]]
    git(plan["repo"], *args)


def file_context(plan, relative, snapshot=False, start=None, end=None):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise TaskError("Repository references must be relative paths without '..'.")
    target = Path(plan["path"]) / rel
    if plan["existing"]:
        if not target.resolve().is_relative_to(Path(plan["path"]).resolve()):
            raise TaskError("Reference escapes the checkout through a symlink; attach explicitly instead.")
        if not target.is_file():
            raise TaskError("File is absent from the target checkout.")
        raw = target.read_bytes()
    else:
        result = subprocess.run(["git", "-C", plan["repo"], "show", plan["commit"] + ":" + rel.as_posix()], capture_output=True)
        if result.returncode:
            raise TaskError("File is absent from the selected base/branch commit.")
        raw = result.stdout
    item = {"label": rel.as_posix(), "mode": "snapshot" if snapshot else "reference", "target": str(target), "size": len(raw),
            "relative": rel.as_posix(), "start": start, "end": end}
    if start is not None:
        if start < 1 or (end is not None and end < start):
            raise TaskError("Invalid line range.")
        item["target"] += f":{start}" + (f"-{end}" if end else "")
    if snapshot or start is not None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TaskError("Binary/non-UTF-8 file: use reference-only without line ranges, or attach it.") from exc
        if "\0" in text:
            raise TaskError("Binary file: use reference-only or attach it.")
        lines = text.splitlines(keepends=True)
        if start and (start > len(lines) or (end and end > len(lines))):
            raise TaskError("Line range exceeds the file length.")
        if snapshot:
            item["text"] = str(target) + "\n" + clean("".join(lines[(start or 1)-1:end]))
    return item


def attach(store, ident, source):
    ident = str(uuid.UUID(ident))
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise TaskError("Attachment must be a readable regular file.")
    if clean(str(source)) != str(source) or "\n" in str(source) or "\r" in str(source):
        raise TaskError("Attachment path contains unsupported terminal control characters.")
    folder = private_dir(store.root / "handovers" / ident / "attachments")
    target = folder / (uuid.uuid4().hex[:8] + "-" + source.name)
    with source.open("rb") as inp, target.open("xb") as out:
        shutil.copyfileobj(inp, out)
    if os.name != "nt":
        target.chmod(0o600)
    kind = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return {"label": source.name, "source": str(source), "target": str(target), "mode": "attachment",
            "size": target.stat().st_size, "mime": kind, "image": kind.startswith("image/")}


class Luvus:
    def __init__(self):
        if os.environ.get("LUVUS_ENV") == "1" and not os.environ.get("LUVUS_BIN_PATH"):
            raise TaskError("Managed Luvus pane is missing LUVUS_BIN_PATH.")
        self.binary = os.environ.get("LUVUS_BIN_PATH") or shutil.which("luvus")
        if not self.binary:
            raise TaskError("Luvus is not installed or is missing from PATH.")

    def call(self, method, **params):
        from toolkit_core.transport import request as toolkit_request, response as toolkit_response
        params, toolkit_owner = toolkit_request('tasks', method, params)
        request = json.dumps({"id": uuid.uuid4().hex, "method": method, "params": params}) + "\n"
        if len(request.encode()) > 1_048_576:
            raise TaskError("Request exceeds Luvus's 1 MiB frame limit. Reduce included snapshots; nothing was submitted.")
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("LUVUS_TASKS_TOKEN_"):
                env.pop(key)
        try:
            r = subprocess.run([self.binary, "uhp", "proxy"], input=request, capture_output=True,
                               encoding="utf-8", errors="replace", env=env, timeout=65)
            response = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise TaskError("Luvus response unavailable. A submitted operation may have executed; inspect before retrying.") from exc
        if "error" in response:
            error = response["error"]
            raise TaskError(clean(f"Luvus {error['code']}: {error['message']}"))
        return toolkit_response(toolkit_owner, method, response["result"])

    def capabilities(self):
        result = self.call("uhp.capabilities")
        methods = {m["method"] for m in result["method_contracts"]}
        required = {"agent.start", "agent.prompt", "agent.list", "terminal.backend.create", "module.pane.open", "ui.dock.push"}
        if required - methods:
            raise TaskError("Luvus is missing: " + ", ".join(sorted(required - methods)))
        return result

    def available_agents(self):
        names = self.call("server.agent_manifests")["agents"]
        return {label: kind for label, kind in AGENTS.items() if kind in names and shutil.which(kind)}

    def agents(self):
        agents = self.call("agent.list")["agents"]
        inventory = self.call("terminal.backend.inventory")
        terminals = {str(t["pane_id"]): t for t in inventory["terminals"]}
        result = []
        for agent in agents:
            terminal = terminals.get(str(agent["pane"]))
            if terminal and agent.get("cwd") and Path(terminal["cwd"]).resolve() == Path(agent["cwd"]).resolve():
                result.append({**agent, "terminal_id": terminal["terminal_id"], "generation": inventory["server_generation"]})
            else:
                result.append({**agent, "terminal_id": None, "generation": None})
        return result

    def validate_terminal(self, record):
        inventory = self.call("terminal.backend.inventory")
        if inventory["server_generation"] != record["generation"]:
            raise TaskError("Luvus restarted since launch. Resume the recorded session or start fresh.")
        terminal = next((t for t in inventory["terminals"] if t["terminal_id"] == record["terminal_id"] and
                         str(t["pane_id"]) == str(record["pane"])), None)
        if not terminal or Path(terminal["cwd"]).resolve() != Path(record["target"]["path"]).resolve():
            raise TaskError("Recorded terminal no longer matches the reviewed checkout. Reconcile before retrying.")
        if record["target"].get("branch") and git(record["target"]["path"], "branch", "--show-current") != record["target"]["branch"]:
            raise TaskError("Recorded checkout changed branch. No instructions were sent; reconcile the handover first.")


def save_record(store, record):
    record["updated"] = now()
    store.put("handovers", record["id"], record, ticket_key(record["ticket"]))
    store.observe("launch:" + record["id"], record["id"], {"kind": "launch", "title": "Handover",
                  "state": record.get("stage", "draft"), "error": record.get("error", "")})


def launch(store, luvus, record, dirty_ok=False):
    from .bounded import lock as workflow_lock
    with workflow_lock(store), store.lock("launch"):
        from .bounded import reservation
        reservation(store, record)
        current = next((r for r in store.records("handovers") if r["id"] == record["id"]), record)
        record.update(current)
        if record.get("stage") == "delivered":
            return record
        if not record.get("prompt", "").strip() or len(record["prompt"]) > 262_144 or len(json.dumps(record["prompt"]).encode()) > 1_000_000:
            raise TaskError("Prompt exceeds Luvus's limit or is empty. Edit the draft and use file references for large context; nothing was launched.")
        if record.get("stage") in ("prompt_pending", "terminal_pending"):
            raise TaskError("Previous operation has an uncertain outcome. Inspect Luvus and reconcile it in history; automatic retry is disabled.")
        plan = record["target"]
        from .operations import validate_group_repository
        validate_group_repository(store.config(), record, plan["repo"])
        agents = luvus.agents()
        own = matching_agent(agents, record)
        if any(a.get("cwd") and Path(a["cwd"]).resolve() == Path(plan["path"]).resolve() and a != own for a in agents):
            raise TaskError("Another agent uses the target checkout. Use a reviewed follow-up or choose another branch.")
        ensure_target(plan)
        if git(plan["path"], "status", "--porcelain") and not dirty_ok:
            raise TaskError("Target checkout has changes. Review them and explicitly acknowledge before launch.")
        if git(plan["path"], "branch", "--show-current") != plan["branch"]:
            raise TaskError("Target checkout changed branch since review.")
        if record.get("setup") and not record.get("pane"):
            from .workflow import ensure_setup
            ensure_setup(store, record)
        if not record.get("pane"):
            record["stage"] = "terminal_pending"
            save_record(store, record)
            launcher = str(Path(__file__).resolve().parent.parent / "launcher.py")
            r = luvus.call("terminal.backend.create", cwd=plan["path"], placement={"kind": "workspace"},
                           focus=True, label=record["name"], command=[sys.executable, launcher, "shell"])
            from .tab_titles import remember
            remember(luvus.call, r["pane_id"], record["agent"].title() + " · " + record["ticket"]["key"] + " · " + record["ticket"]["title"])
            record.update(pane=r["pane_id"], terminal_id=r["terminal_id"], generation=r["server_generation"], stage="terminal_ready")
            save_record(store, record)
        luvus.validate_terminal(record)
        existing = live_agent(luvus, record)
        if not existing:
            if record.get("stage") == "agent_pending":
                raise TaskError("Agent startup outcome is uncertain. Inspect the recorded pane; do not launch a second process.")
            record["stage"] = "agent_pending"
            save_record(store, record)
            result = luvus.call("agent.start", name=record["name"], kind=record["agent"], pane=record["pane"], timeout_s=30)
            if not result.get("ready"):
                raise TaskError("Agent has not become ready. Retry after checking its pane; the existing launch will be reused.")
        record["stage"] = "agent_ready"
        from .checkout import guard, identity
        record.setdefault("checkout_identity", identity(guard(record)))
        record.setdefault("orch_enabled", bool(store.config().get("orchestration")))
        save_record(store, record)
        from .orch import coordinate
        try:
            coordinate(store, luvus, record)
        except TaskError as exc:
            record["orch_error"] = str(exc)
            save_record(store, record)
            raise
        guard(record)
        luvus.validate_terminal(record)
        record["stage"] = "prompt_pending"
        save_record(store, record)
        luvus.call("agent.prompt", target=record["name"], text=record["prompt"])
        record["stage"] = "delivered"
        save_record(store, record)
        try:
            agent = live_agent(luvus, record)
            if agent and agent.get("session"):
                record["session"] = agent["session"]
                save_record(store, record)
        except TaskError:
            pass  # Prompt delivery is already durable; session discovery is optional.
        return record


def matching_agent(agents, record):
    matches = [a for a in agents if record.get("terminal_id") and record.get("generation") and
               a.get("terminal_id") == record["terminal_id"] and a.get("generation") == record["generation"] and
               str(a.get("pane")) == str(record.get("pane")) and a.get("name") == record.get("name") and
               a.get("agent") == record.get("agent") and a.get("cwd") and
               Path(a["cwd"]).resolve() == Path(record.get("target", {}).get("path", "")).resolve()]
    return matches[0] if len(matches) == 1 else None


def live_agent(luvus, record):
    return matching_agent(luvus.agents(), record)


def resume(store, luvus, record):
    agent = live_agent(luvus, record)
    if agent:
        if agent.get("session"):
            record["session"] = agent["session"]
            save_record(store, record)
        luvus.call("pane.focus", pane=agent["pane"])
        return
    session = record.get("session")
    if session:
        candidates = [a for a in luvus.agents() if a.get("terminal_id") and a.get("generation") and a.get("session") == session and a.get("agent") == record["agent"] and
                      Path(a.get("cwd", "")).resolve() == Path(record["target"]["path"]).resolve()]
        if len(candidates) > 1:
            raise TaskError("More than one live agent matches the recorded session; resolve the ambiguity in Luvus.")
        if candidates:
            a = candidates[0]
            luvus.call("agent.name", pane=a["pane"], name=record["name"])
            record.update(pane=a["pane"], terminal_id=a["terminal_id"], generation=a["generation"])
            save_record(store, record)
            luvus.call("pane.focus", pane=a["pane"])
            return
        if record.get("resume_pending"):
            raise TaskError("Resume was requested but its agent is not yet identified. Inspect Luvus before requesting another restore.")
        matches = [s for s in luvus.call("agent.sessions")["sessions"] if s["session_id"] == session and
                   s["agent"] == record["agent"] and Path(s["cwd"]).resolve() == Path(record["target"]["path"]).resolve()]
        if len(matches) == 1:
            record["resume_pending"] = True
            save_record(store, record)
            luvus.call("agent.resume", session_id=session)
            agents = [a for a in luvus.agents() if a.get("terminal_id") and a.get("generation") and a.get("session") == session and a.get("agent") == record["agent"] and
                      Path(a.get("cwd", "")).resolve() == Path(record["target"]["path"]).resolve()]
            if len(agents) == 1:
                a = agents[0]
                luvus.call("agent.name", pane=a["pane"], name=record["name"])
                record.update(pane=a["pane"], terminal_id=a["terminal_id"], generation=a["generation"])
                record["resume_pending"] = False
                save_record(store, record)
            return
    raise TaskError("Recorded conversation is unavailable. Start fresh in the existing branch; conversation continuity cannot be guaranteed.")
