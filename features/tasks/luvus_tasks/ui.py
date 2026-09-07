from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from . import MODULE_ID
from .core import Store, TaskError, clean, compose, default_filters, now, save_token, ticket_key
from .handover import (Luvus, attach, branch_name, default_base, file_context, git, launch, live_agent,
                       repository, resume, save_record, target_plan)
from .providers import ProviderError, lookup_candidates, provider


def say(value=""):
    print(clean(value))


def ask(label, default=""):
    value = input(clean(label) + (f" [{clean(default)}]" if default else "") + ": ").strip()
    return value or default


def confirm(label):
    return ask(label + " (type yes to confirm)").lower() == "yes"


def choose(label, values, display=str):
    say("\n" + label)
    for i, value in enumerate(values, 1):
        say(f"{i}. {display(value)}")
    if not values:
        raise TaskError("No choices available.")
    raw = ask("Number (blank cancels)")
    if not raw:
        raise TaskError("Cancelled.")
    if not raw.isdigit() or not 1 <= int(raw) <= len(values):
        raise TaskError("Choose one of the displayed numbers.")
    return values[int(raw) - 1]


def multiline(label, default=""):
    say(label)
    if default:
        say(default)
    say("Enter replacement text; a line containing only '.' finishes. Empty keeps current; '/clear' clears.")
    lines = []
    while True:
        line = input()
        if line == ".":
            break
        lines.append(line)
    text = "\n".join(lines)
    return "" if text == "/clear" else clean(text or default)


class App:
    def __init__(self, store=None, luvus=None):
        self.store = store or Store()
        self.luvus = luvus
        self.tasks = []

    def host(self):
        if self.luvus is None:
            self.luvus = Luvus()
        return self.luvus

    def connection(self, task):
        return next(c for c in self.store.config()["connections"] if c["id"] == task["connection"])

    def configure(self):
        config = self.store.config()
        action = choose("Configuration", ["Add/edit connection", "Global instructions", "Prompt preset", "Repository defaults", "Check Luvus"])
        if action == "Check Luvus":
            self.host().capabilities()
            say("Available agents: " + ", ".join(self.host().available_agents()))
            say("Generic image delivery: local paths with explicit acknowledgement.")
            return
        if action == "Global instructions":
            config["instructions"] = multiline("Global instructions", config["instructions"])
        elif action == "Prompt preset":
            name = choose("Preset", list(config["presets"]))
            config["presets"][name] = multiline("Preset instructions", config["presets"][name])
        elif action == "Repository defaults":
            path = repository(ask("Local repository path", os.environ.get("LUVUS_WORKSPACE_CWD", str(Path.cwd()))))
            previous = config["repositories"].get(path, {})
            value = dict(previous)
            value["agent"] = choose("Preferred agent", ["codex", "claude", "copilot", "opencode", "muse"])
            value["base"] = ask("Default base branch", previous.get("base", "main"))
            pattern = ask("Branch pattern ({key}, {slug})", previous.get("branch_pattern", "{key}-{slug}"))
            branch_name(pattern, {"key": "TEST-1", "title": "Example"})
            value["branch_pattern"] = pattern
            value["preset"] = choose("Default preset", list(config["presets"]))
            value["validation"] = multiline("Validation instructions", previous.get("validation", ""))
            if confirm("Override global instructions for this repository?"):
                value["instructions"] = multiline("Repository instructions", previous.get("instructions", config["instructions"]))
            else:
                value.pop("instructions", None)
            config["repositories"][path] = value
        else:
            ident = ask("Connection name (letters, digits, underscores)")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", ident):
                raise TaskError("Invalid connection name.")
            old = next((c for c in config["connections"] if c["id"] == ident), {})
            kind = old.get("provider") or choose("Provider", ["jira", "azure", "github"])
            c = {**old, "id": ident, "provider": kind, "token_env": "LUVUS_TASKS_TOKEN_" + ident.upper()}
            if kind == "jira":
                c["url"] = ask("Jira site URL", old.get("url", "https://your-site.atlassian.net")).rstrip("/")
                c["email"] = ask("Atlassian email", old.get("email", ""))
                c["cloud_id"] = ask("Cloud ID for scoped API token (blank for classic token)", old.get("cloud_id", ""))
                c["acceptance_field"] = ask("Acceptance criteria custom field ID (optional)", old.get("acceptance_field", ""))
            elif kind == "azure":
                c["organization"] = ask("Azure organization name", old.get("organization", ""))
                c["project"] = ask("Azure project", old.get("project", ""))
                c["acceptance_field"] = ask("Acceptance field", old.get("acceptance_field", "Microsoft.VSTS.Common.AcceptanceCriteria"))
            else:
                c["repository"] = ask("GitHub owner/repository", old.get("repository", ""))
                from .providers import github_repositories
                github_repositories(c)
                c.pop("token_env", None)
                say("Uses gh's existing github.com login. Run gh auth login --hostname github.com if needed.")
            filters = old.get("filters", default_filters(kind))
            while True:
                operation = choose("Saved filters", ["Done", "Add", "Edit", "Remove"])
                if operation == "Done":
                    break
                selected = None if operation == "Add" else choose("Filter", filters, lambda f: f["name"])
                if operation == "Remove":
                    filters.remove(selected)
                else:
                    updated = {"name": ask("Filter name", selected["name"] if selected else ""),
                               "query": multiline({"jira": "JQL", "azure": "Flat WIQL", "github": "GitHub search qualifiers (no repo: needed)"}[kind], selected["query"] if selected else "")}
                    if selected:
                        filters[filters.index(selected)] = updated
                    else:
                        filters.append(updated)
            c["filters"] = filters
            method = "gh login" if kind == "github" else choose("Credentials", ["Operating system credential store", "Environment variable", "Keep existing"])
            if method == "Operating system credential store":
                import getpass
                save_token(c, getpass.getpass("Token (hidden): "))
            elif method == "Environment variable":
                say("Set " + c["token_env"] + " before running the module. Do not add tracker tokens to your agent shell configuration.")
            config["connections"] = [x for x in config["connections"] if x["id"] != ident] + [c]
        self.store.save_config(config)
        say("Configuration saved.")

    def refresh(self, network=True):
        from .operations import refresh
        self.tasks, errors = refresh(self.store, network, factory=provider)
        for error in errors:
            say(error)
        return self.tasks

    def dock(self, network=False, check_pr=False):
        from . import operations as ops
        if check_pr:
            from .workflow import poll_prs
            poll_prs(self.store)
        self.tasks, errors = ops.refresh(self.store, network)
        key = "dock:" + ops.session_key()
        scope = self.store.preference(key + ":scope", None)
        if scope is None:
            scope = {"mode": "workspace", "repo": os.environ.get("LUVUS_WORKSPACE_CWD", "")}
            self.store.set_preference(key + ":scope", scope)
        if not network:
            errors += self.store.preference("issue-refresh-errors", [])
        try:
            agents = self.host().agents()
            available = True
        except TaskError as exc:
            agents = []
            available = False
            errors.append(str(exc))
        from .attention import project, notifications
        orch = []
        if self.store.config().get("orchestration"):
            try:
                orch = ops.orch_snapshot(self.host())["tasks"]
            except TaskError as exc:
                errors.append(str(exc))
        items = project(self.store, agents, orch, available)
        rows, active, attention = ops.dashboard(self.store, self.tasks, agents, scope, errors, items)
        notifications(self.store, self.host(), items)
        inventory = self.host().call("terminal.backend.inventory")
        projection = {"rows": rows, "generation": inventory["server_generation"]}
        if self.store.preference(key + ":paint", {}) != projection:
            self.host().call("ui.dock.push", id="luvus-tasks", title="Tasks", placement="sidebar.right", rows=rows)
            suffix = "-windows" if os.name == "nt" else ""
            self.host().call("ui.bar.push", id="task-status", owner=MODULE_ID, content=[
                {"type": "text", "text": f"Tasks: {active} active · {attention} attention", "action": "open" + suffix}])
            self.store.set_preference(key + ":paint", projection)

    def lookup(self):
        value = ask("Ticket URL or ID")
        candidates = lookup_candidates(self.store.config(), value)
        c, identifier = candidates[0] if len(candidates) == 1 else choose("Matching connection", candidates, lambda x: x[0]["id"])
        return provider(c).get(identifier)

    def status(self, task):
        p = provider(self.connection(task))
        task = p.get(task["id"])
        action = choose("Available status actions", p.actions(task), lambda a: a["name"])
        fields = {}
        for key, info in action["fields"].items():
            if not info.get("required"):
                continue
            name = info.get("name", key)
            choices = info.get("allowedValues", [])
            kind = info.get("schema", {}).get("type")
            if choices:
                item = choose(name, choices, lambda x: str(x.get("name", x.get("value", x.get("id")))) if isinstance(x, dict) else str(x))
                fields[key] = {"id": item["id"]} if isinstance(item, dict) and "id" in item else item
                if kind == "array":
                    fields[key] = [fields[key]]
            elif kind in ("string", "number", "integer", "boolean"):
                value = ask(name)
                if not value:
                    raise TaskError(name + " is required.")
                if kind == "boolean" and value.lower() not in ("true", "false"):
                    raise TaskError("Boolean fields accept true or false.")
                fields[key] = float(value) if kind == "number" else int(value) if kind == "integer" else value.lower() == "true" if kind == "boolean" else value
            else:
                raise TaskError(f"Required field {name} needs unsupported input. Complete this transition at {task['url']}.")
        say(json.dumps(fields, ensure_ascii=False, indent=2))
        if confirm(f"Change {task['key']} at {task['url']} from {task['status']} using {action['name']}?"):
            say("New status: " + p.transition(task, action, fields)["status"])
            self.refresh()

    def context(self, task, record):
        items = record["context"]
        while True:
            say("\nContext inventory")
            for i, item in enumerate(items, 1):
                say(f"{i}. {item['label']} | {item['mode']} | {item.get('size', len(item.get('text', '').encode()))} bytes | {item.get('source', item.get('target', ''))}")
            action = choose("Context", ["Done", "Add text", "Reference repository file", "Attach local file/image", "Related ticket", "Ticket comment", "Remove", "Reorder", "Preview"])
            if action == "Done":
                return
            if action == "Add text":
                items.append({"label": ask("Label", "Notes"), "mode": "text", "text": multiline("Additional context")})
            elif action == "Reference repository file":
                relative = ask("Path relative to selected repository")
                mode = choose("Inclusion", ["Reference only", "Include text snapshot"])
                raw = ask("Optional line range, e.g. 10-25")
                start = end = None
                if raw:
                    parts = raw.split("-")
                    start, end = int(parts[0]), int(parts[-1])
                items.append(file_context(record["target"], relative, mode == "Include text snapshot", start, end))
            elif action == "Attach local file/image":
                items.append(attach(self.store, record["id"], ask("Local file path (no quotes needed)")))
            elif action in ("Related ticket", "Ticket comment"):
                p = provider(self.connection(task))
                if action == "Related ticket":
                    related = choose("Related ticket", p.related(task), lambda x: x["label"])
                    if related.get("url"):
                        candidates = lookup_candidates(self.store.config(), related["url"])
                        c, ident = candidates[0] if len(candidates) == 1 else choose("Connection for related ticket", candidates, lambda x: x[0]["id"])
                        t = provider(c).get(ident)
                    else:
                        t = p.get(related["id"])
                    items.append({"label": t["key"], "mode": "snapshot", "source": t["url"],
                                  "text": t["url"] + "\n" + t["title"] + "\n" + t["description"] + "\n" + t["acceptance"]})
                else:
                    comment = choose("Comments", p.comments(task), lambda x: x["label"] + " " + x["text"])
                    items.append({**comment, "mode": "snapshot", "source": task["url"]})
            else:
                item = choose("Context item", items, lambda x: x["label"])
                if action == "Remove":
                    items.remove(item)
                elif action == "Reorder":
                    position = int(ask("New position, starting at 1"))
                    if not 1 <= position <= len(items):
                        raise TaskError("Invalid position.")
                    items.remove(item)
                    items.insert(position - 1, item)
                else:
                    say(item.get("text", item.get("target", "")))

    def handover(self, task):
        config = self.store.config()
        c = self.connection(task)
        task = provider(c).get(task["id"])
        previous = self.store.records("handovers", ticket_key(task))
        if previous:
            action = choose("Previous handover exists", ["Resume previous", "Start fresh"])
            if action == "Resume previous":
                return self.history(task)
        repo = repository(ask("Local Git checkout path", c.get("repositories", {}).get(task["project"], "")))
        defaults = {**config.get("branch_defaults", {}), **config["repositories"].get(repo, {})}
        self.host().capabilities()
        agents = self.host().available_agents()
        if not agents:
            raise TaskError("No supported agent executables are available to this module.")
        labels = sorted(agents, key=lambda label: agents[label] != defaults.get("agent", "codex"))
        agent = agents[choose("Agent", labels)]
        mode = choose("Target", ["New branch and worktree", "Existing feature branch"])
        new = mode.startswith("New")
        if new:
            branch = ask("New branch", branch_name(defaults.get("branch_pattern", "feature/{number}-{slug}"), task))
            base = ask("Base branch or commit", default_base(repo, defaults.get("base", "")))
        else:
            branch = choose("Local branch", git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines())
            base = branch
        plan = target_plan(repo, branch, base, new, self.store.root, defaults.get("worktree_parent", ""))
        preset = choose("Prompt preset", sorted(config["presets"], key=lambda x: x != defaults.get("preset", "Implement")))
        ident = str(uuid.uuid4())
        record = {"id": ident, "name": "task-" + ident[:16], "created": now(), "ticket": task, "target": plan,
                  "agent": agent, "preset": preset, "stage": "draft", "context": []}
        save_record(self.store, record)
        try:
            self.context(task, record)
        finally:
            save_record(self.store, record)
        notes = multiline("Optional handover notes")
        record["notes"] = notes
        record["prompt"] = multiline("Complete prompt preview", compose(config, defaults, preset, task, notes, record["context"]))
        if not record["prompt"].strip():
            raise TaskError("Opening prompt must not be empty.")
        save_record(self.store, record)
        if any(i.get("image") for i in record["context"]):
            say("Images are referenced by local path. Luvus's generic prompt API does not deliver image payloads; agent image reading is not guaranteed.")
            if not confirm("Accept local-path image fallback?"):
                raise TaskError("Launch cancelled. Edit context or choose another handover.")
        say(f"Repository: {repo}\nTarget: {plan['path']}\nBranch: {branch}\nBase commit: {plan['commit']}\nAgent: {agent}\nPreset: {preset}")
        dirty = plan["existing"] and bool(git(plan["path"], "status", "--porcelain"))
        if dirty:
            say(git(plan["path"], "status", "--short"))
            if not confirm("Allow agent to work in this dirty checkout, preserving existing changes?"):
                return
        if config.get("orchestration"):
            say("ORCH: create/link and claim this worker before delivery; no path scopes declared.")
        if not confirm("Launch this reviewed handover?"):
            return
        record["approved"] = True
        record["orch_enabled"] = bool(config.get("orchestration"))
        save_record(self.store, record)
        launch(self.store, self.host(), record, dirty_ok=dirty)
        say("Prompt delivered to " + record["name"])
        if confirm("Remember this repository for this connection/project?"):
            c.setdefault("repositories", {})[task["project"]] = repo
            for index, old in enumerate(config["connections"]):
                if old["id"] == c["id"]:
                    config["connections"][index] = c
            self.store.save_config(config)

    def history(self, task):
        record = choose("Handovers", self.store.records("handovers", ticket_key(task)),
                        lambda r: f"{r['created']} {r['agent']} {r['target']['branch']} [{r['stage']}]")
        action = choose("History action", ["Resume", "Edit draft", "Completion review", "Reviewed follow-up", "Retry launch", "Reconcile uncertain operation", "Remove history"])
        if action == "Resume":
            resume(self.store, self.host(), record)
        elif action == "Edit draft":
            if record.get("approved") or record.get("pane"):
                raise TaskError("This handover was already approved. Use a reviewed follow-up or start fresh.")
            config = self.store.config()
            defaults = config["repositories"].get(record["target"]["repo"], {})
            try:
                self.context(record["ticket"], record)
                record["notes"] = multiline("Handover notes", record.get("notes", ""))
                generated = compose(config, defaults, record["preset"], record["ticket"], record["notes"], record["context"])
                say("Rebuilt prompt from current configuration and selected context. Previous per-launch edits:\n" + record.get("prompt", ""))
                record["prompt"] = multiline("Complete prompt preview", generated)
            finally:
                save_record(self.store, record)
            say(json.dumps(record["target"], indent=2))
            if any(i.get("image") for i in record["context"]) and not confirm("Images will be local-path references, not native image payloads. Accept fallback?"):
                return
            dirty = Path(record["target"]["path"]).exists() and bool(git(record["target"]["path"], "status", "--porcelain"))
            if dirty:
                say(git(record["target"]["path"], "status", "--short"))
                if not confirm("Acknowledge existing checkout changes?"):
                    return
            say("ORCH coordination: " + ("enabled" if self.store.config().get("orchestration") else "disabled"))
            if confirm("Launch this reviewed draft?"):
                self.host().capabilities()
                record["approved"] = True
                record["orch_enabled"] = bool(self.store.config().get("orchestration"))
                save_record(self.store, record)
                launch(self.store, self.host(), record, dirty_ok=dirty)
        elif action == "Completion review":
            say("Original acceptance criteria:\n" + (record["ticket"].get("acceptance") or "Not supplied"))
            say("Original prompt and context:\n" + record.get("prompt", "Draft not completed"))
            path = record["target"]["path"]
            say("Observed Git status:\n" + git(path, "status", "--short"))
            say("Committed changes since base:\n" + git(path, "diff", "--stat", record["target"]["commit"], "HEAD"))
            say("Uncommitted changes:\n" + git(path, "diff", "--stat", "HEAD"))
            agent = live_agent(self.host(), record)
            if agent:
                if agent.get("session"):
                    record["session"] = agent["session"]
                    save_record(self.store, record)
                say("Agent output (reported, not independently verified):")
                say(json.dumps(self.host().call("agent.read", target=agent["pane"], lines=120), ensure_ascii=False, indent=2))
            else:
                say("Agent summary/checks unavailable.")
            say("The module has not run validation commands or verified acceptance criteria.")
        elif action == "Reviewed follow-up":
            fresh = provider(self.connection(task)).get(task["id"])
            text = multiline("Follow-up (fresh ticket material supplied for review)", fresh["title"] + "\n" + fresh["description"] + "\n" + fresh["acceptance"])
            agent = live_agent(self.host(), record)
            if not agent:
                raise TaskError("Resume and bind the exact live conversation before sending a follow-up.")
            if confirm("Send this follow-up to " + record["name"] + "?"):
                from .operations import followup
                followup(self.store, self.host(), record, text)
        elif action == "Retry launch":
            if not record.get("approved"):
                raise TaskError("This draft was never approved. Start a new reviewed handover.")
            dirty = Path(record["target"]["path"]).exists() and bool(git(record["target"]["path"], "status", "--porcelain"))
            if dirty and not confirm("Acknowledge existing checkout changes before retry?"):
                return
            launch(self.store, self.host(), record, dirty_ok=dirty)
        elif action == "Reconcile uncertain operation":
            say(json.dumps(record, ensure_ascii=False, indent=2))
            if record["stage"] == "prompt_pending":
                if confirm("I inspected the exact agent and confirm the prompt was delivered"):
                    record["stage"] = "delivered"
                elif confirm("I inspected the exact agent and confirm the prompt was NOT delivered; allow a retry"):
                    record["stage"] = "agent_ready"
            elif record["stage"] == "terminal_pending":
                say("Inspect Luvus for a terminal labelled " + record["name"])
                if confirm("I verified the terminal was NOT created; allow a retry"):
                    record["stage"] = "draft"
            elif record["stage"] == "agent_pending":
                if confirm("I inspected the recorded pane and verified NO agent process started; allow a retry"):
                    record["stage"] = "terminal_ready"
            save_record(self.store, record)
        else:
            if confirm("Delete this handover's saved prompt and attachment copies? Original files and worktree are preserved"):
                self.store.delete_handover(record)
                say("Removed module history and attachment copies; this cannot be undone.")

    def writeback(self, task):
        p = provider(self.connection(task))
        text = multiline("Progress/completion comment")
        link = ask("Optional branch/PR HTTPS URL")
        if link:
            url = urlsplit(link)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise TaskError("Link must be an HTTPS URL without embedded credentials.")
            text += "\n" + link
        if not text.strip():
            raise TaskError("Comment must not be empty.")
        key = ticket_key(task)
        digest = hashlib.sha256((key + "\0" + text).encode()).hexdigest()
        with self.store.lock("writeback"):
            prior = next((r for r in self.store.records("writes", key) if r["id"] == digest), None)
            marker = "[luvus-tasks:" + digest[:20] + "]"
            if prior:
                found = any(marker in c["text"] for c in p.comments(task))
                if found or prior["state"] == "published":
                    say("This comment was already published.")
                    return
                if not confirm("Previous outcome is uncertain and comment is not visible. I checked the ticket and want to retry"):
                    return
            body = text + "\n\n" + marker
            say("Destination: " + task["url"] + "\nExact comment:\n" + body)
            if not confirm("Publish this comment?"):
                return
            record = {"id": digest, "state": "pending", "text": body, "at": now()}
            self.store.put("writes", digest, record, key)
            try:
                p.comment(task, body)
            except ProviderError as exc:
                if not exc.ambiguous:
                    record["state"] = "rejected"
                    self.store.put("writes", digest, record, key)
                raise
            if not any(marker in c["text"] for c in p.comments(task)):
                raise TaskError("Comment submitted but readback is not yet visible. Do not publish again; reconcile on retry.")
            record["state"] = "published"
            self.store.put("writes", digest, record, key)
            say("Published and verified.")

    def detail(self, task):
        while True:
            try:
                task = provider(self.connection(task)).get(task["id"])
                say(f"\n{task['key']}: {task['title']}\nTicket status: {task['status']}\n{task['url']}\n{task['description']}\nAcceptance criteria:\n{task['acceptance'] or 'Not supplied'}")
                action = choose("Task", ["Back", "Change status", "Handover", "History / resume / review", "Publish reviewed comment/link"])
                if action == "Back":
                    return
                {"Change status": self.status, "Handover": self.handover, "History / resume / review": self.history,
                 "Publish reviewed comment/link": self.writeback}[action](task)
            except (TaskError, ValueError, OSError) as exc:
                say("Error: " + str(exc))
                return

    def run(self):
        self.refresh()
        while True:
            try:
                action = choose("Luvus Tasks", ["Browse tasks", "Open ticket URL/ID", "Refresh", "Configure", "History", "Quit"])
                if action == "Quit":
                    return
                if action == "Configure":
                    self.configure()
                elif action == "Refresh":
                    self.refresh()
                    try:
                        self.dock()
                    except TaskError as exc:
                        say(str(exc))
                elif action == "Open ticket URL/ID":
                    self.detail(self.lookup())
                elif action == "History":
                    record = choose("Recorded tasks", self.store.records("handovers"), lambda r: r["ticket"]["key"] + " " + r["created"])
                    self.history(record["ticket"])
                else:
                    pending = self.store.records("inbox")
                    selected = {r["ticket"] for r in pending}
                    values = sorted(self.tasks, key=lambda t: ticket_key(t) not in selected)
                    task = choose("Tasks (sidebar selection first)", values, lambda t: f"{t['connection']} {t['key']} [{t['status']}] {t['title']} | refreshed {t.get('refreshed')}" + (" [cached]" if t.get("stale") else ""))
                    with self.store.db:
                        self.store.db.execute("DELETE FROM inbox")
                    self.detail(task)
            except (TaskError, ValueError, OSError) as exc:
                say("Error: " + str(exc))


def main():
    parser = argparse.ArgumentParser(description="Jira, Azure DevOps, and GitHub Issues for Luvus")
    parser.add_argument("command", nargs="?", choices=["ui", "open", "dock", "watch", "refresh", "scope", "configure", "doctor", "legacy",
                        "hub", "handover", "status", "browser", "actions", "selection", "copy-selection", "capture", "linked", "follow-up", "workspace-handover", "attention", "find-work", "orch-open"], default="ui")
    args = parser.parse_args()
    try:
        app = App()
        if args.command == "watch":
            from .polling import watch
            return watch(app)
        elif args.command == "orch-open":
            from .operations import open_console
            open_console(app.store, app.host(), "orch-open", os.environ.get("LUVUS_MODULE_ROW_VALUE", ""))
        elif args.command == "hub":
            from .module_hub import open_hub
            open_hub(app, json.loads(os.environ.get("LUVUS_MODULE_CONTEXT_JSON", "{}")))
        elif args.command in ("open", "handover", "status", "browser", "actions", "selection", "copy-selection", "capture", "linked", "follow-up", "workspace-handover", "attention", "find-work"):
            from .operations import open_console
            value = os.environ.get("LUVUS_MODULE_ROW_VALUE", "")
            context = json.loads(os.environ.get("LUVUS_MODULE_CONTEXT_JSON", "{}"))
            if args.command in ("copy-selection", "capture", "selection", "linked", "follow-up", "workspace-handover"):
                value = ""
            open_console(app.store, app.host(), "status-change" if args.command == "status" else args.command, value, context)
        elif args.command == "scope":
            from .operations import session_key
            mode = os.environ.get("LUVUS_MODULE_ROW_VALUE", "current")
            context = json.loads(os.environ.get("LUVUS_MODULE_CONTEXT_JSON", "{}"))
            repo = context.get("workspace", {}).get("cwd", os.environ.get("LUVUS_WORKSPACE_CWD", ""))
            app.store.set_preference("dock:" + session_key() + ":scope", {"mode": "all" if mode == "all" else "workspace", "repo": repo})
            app.dock(False)
        elif args.command in ("dock", "refresh"):
            app.dock(args.command == "refresh", check_pr=True)
        elif args.command in ("ui", "configure"):
            from .console import Cockpit
            app.store.db.close()
            Cockpit().run()
        elif args.command == "doctor":
            app.host().capabilities()
            say("Luvus capabilities: OK\nAvailable agents: " + ", ".join(app.host().available_agents()))
            say("Image transport: local-path fallback. Live tracker writes and agent launches are not tested by doctor.")
        elif args.command == "legacy":
            app.run()
    except (TaskError, ValueError, OSError) as exc:
        say("Error: " + str(exc))
        return 1
    except (EOFError, KeyboardInterrupt):
        say("\nClosed task pane.")
    return 0
