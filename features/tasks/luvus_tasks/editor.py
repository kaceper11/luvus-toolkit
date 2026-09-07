"""Persistent handover editor; launch and tracker writes remain reviewed operations."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from pathlib import Path

from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Collapsible, DataTable, Input, Select, Static, TabbedContent, TabPane, TextArea

from . import operations as ops
from .core import handover_tickets, handover_label, has_ticket, TaskError
from .handover import attach, base_branches, branch_name, default_base, file_context, git, save_record, validate_issue_repository
from .providers import lookup_candidates, provider


class HandoverEditor(Vertical):
    DEFAULT_CSS = """
    HandoverEditor { height: 1fr; }
    HandoverEditor #editor-summary { height: auto; max-height: 4; padding: 0 1; background: $surface; }
    HandoverEditor #editor-state { height: auto; max-height: 3; color: $text-muted; }
    HandoverEditor #editor-sections { height: 1fr; }
    HandoverEditor TabPane { padding: 0 1; overflow-y: auto; }
    HandoverEditor .editor-buttons { height: auto; layout: grid; grid-size: 3; grid-columns: 1fr 1fr 1fr; grid-rows: 3; }
    HandoverEditor .editor-buttons Button { min-width: 0; width: 1fr; }
    HandoverEditor #context-table { height: 8; }
    HandoverEditor #context-body { height: 10; }
    HandoverEditor #opening-prompt { height: 12; }
    HandoverEditor #prompt-notes { height: 5; }
    HandoverEditor #base-fields { height: auto; }
    HandoverEditor #target-feedback { height: auto; min-height: 3; }
    HandoverEditor #editor-footer { height: 3; }
    .narrow HandoverEditor .editor-buttons { grid-size: 2; grid-columns: 1fr 1fr; }
    """

    def __init__(self, record, workspaces, agents):
        super().__init__(id="handover-editor-" + record["id"])
        self.record = copy.deepcopy(record)
        self.workspaces, self.agents = workspaces, agents
        self.ready = False
        self.synchronizing = False
        self.revision = 0
        self.saved_revision = -1
        self.valid_revision = -1
        self.context_index = None
        self.operation_busy = False
        self.save_timer = None
        self.save_failed = False
        self.validation_signature = None

    def compose(self):
        from .console import Preview
        r, v = self.record, self.record["inputs"]
        yield Static(handover_label(r), id="editor-summary", markup=False)
        yield Button("Add / remove issues", id="editor-issues")
        yield Static("Saved locally · nothing starts before confirmation", id="editor-state", markup=False)
        with TabbedContent(id="editor-sections"):
            with TabPane("Target", id="editor-target"):
                yield Static(r.get("repository_source", "Saved draft"), id="repository-source", markup=False)
                with Collapsible(title="Choose a Luvus workspace", collapsed=bool(v["repo"])):
                    yield Input(placeholder="Search by name, branch, or path", id="workspace-search")
                    yield Select(self.workspace_options(), prompt="Choose workspace (optional)", id="workspace-choice")
                yield Static("Repository path · local Git checkout, not a tracker URL")
                yield Input(v["repo"], id="target-repo")
                yield Select([("New isolated worktree", True), ("Existing branch / worktree", False)], value=bool(v["new"]), allow_blank=False, id="target-new")
                yield Static("Feature branch")
                yield Input(v["branch"], id="target-branch")
                with Collapsible(title="Browse existing branches / base", collapsed=True):
                    yield Input(placeholder="Search available branches", id="branch-search")
                    yield Select([], prompt="Choose branch / base", id="branch-choice")
                with Vertical(id="base-fields"):
                    yield Static("Base branch or commit · pinned before launch")
                    yield Input(v["base"], id="target-base")
                yield Static("Agent")
                yield Select([(label, kind) for label, kind in self.agents.items()], value=r["agent"] if r["agent"] in self.agents.values() else Select.NULL, prompt="Choose an available agent", id="target-agent")
                with Collapsible(title="Advanced", collapsed=True):
                    yield Static("Worktree parent folder · blank uses module storage")
                    yield Input(v.get("worktree_parent", ""), id="target-parent")
                yield Static("Checking target…", id="target-feedback", markup=False)
            with TabPane("Context", id="editor-context"):
                with Vertical(classes="editor-buttons"):
                    for title, ident in [("Add text", "text"), ("Repository file", "file"), ("File / image", "attachment"), ("Related issue", "related"), ("Comment", "comment"), ("Context packs", "packs")]:
                        yield Button(title, id="context-" + ident)
                yield Static("Repository files require a valid target. Text and attachments can be added now.", markup=False)
                yield DataTable(id="context-table", cursor_type="row")
                with Vertical(classes="editor-buttons"):
                    for title, ident in [("Remove", "remove"), ("Move up", "up"), ("Move down", "down")]:
                        yield Button(title, id="context-" + ident)
                yield Input(placeholder="Selected context label", id="context-label")
                yield TextArea("", id="context-body")
            with TabPane("Prompt", id="editor-prompt"):
                yield Static("Opening instructions, not a guaranteed agent system message. Context is reference data.")
                presets = list(self.app.store.config()["presets"])
                if r["preset"] not in presets:
                    presets.append(r["preset"])
                yield Select([(p, p) for p in presets], value=r["preset"], allow_blank=False, id="prompt-preset")
                yield Static("Extra instructions for this handover")
                yield TextArea(r.get("notes", ""), id="prompt-notes")
                yield Static("", id="prompt-state", markup=False)
                with Vertical(classes="editor-buttons"):
                    yield Button("Regenerate", id="prompt-regenerate")
                    yield Button("Keep edited prompt", id="prompt-keep")
                    yield Button("Copy prompt", id="prompt-copy")
                yield Static("Exact opening prompt")
                yield TextArea(r.get("prompt", ""), id="opening-prompt")
        with Horizontal(id="editor-footer"):
            yield Button("Review & launch", id="editor-review", variant="primary")
            yield Button("Save", id="editor-save")
            yield Button("Back to handovers", id="editor-close")

    def workspace_options(self, query=""):
        return [(w["label"], w["cwd"]) for w in self.workspaces if query.casefold() in w["label"].casefold()]

    def on_mount(self):
        self.query_one("#context-table", DataTable).add_columns("Context", "Inclusion", "State")
        self.paint_context()
        self.query_one("#base-fields").display = self.record["inputs"]["new"]
        self.ready = True
        self.refresh_prompt()
        self.changed()
        self.load_branches()

    def state(self, text):
        self.query_one("#editor-state", Static).update(text)

    def refresh_prompt(self, regenerate=False):
        if self.record.get("approved"):
            return
        ops.update_prompt(self.app.store, self.record, regenerate)
        widget = self.query_one("#opening-prompt", TextArea)
        if widget.text != self.record["prompt"]:
            widget.load_text(self.record["prompt"])
        self.query_one("#prompt-state", Static).update("Inputs changed: regenerate or keep the edited prompt." if self.record.get("prompt_outdated") else
                                                       ("Custom prompt preserved" if self.record["prompt_mode"] == "custom" else "Generated from current instructions and context"))

    def changed(self):
        if not self.ready:
            return
        self.revision += 1
        target = json.dumps([self.record["inputs"], self.record["context"], handover_tickets(self.record)], sort_keys=True)
        needs_validation = target != self.validation_signature
        if needs_validation:
            self.validation_signature = target
            self.valid_revision = -1
        elif self.valid_revision >= 0:
            self.valid_revision = self.revision
        self.state("Saving…")
        if self.save_timer:
            self.save_timer.stop()
        self.save_timer = self.set_timer(0.3, self.flush)
        if needs_validation:
            self.validate_target()

    def flush(self):
        if self.save_timer:
            self.save_timer.stop()
        try:
            if self.record.get("approved"):
                return True
            save_record(self.app.store, self.record)
            self.saved_revision = self.revision
            self.save_failed = False
            self.app.save_warning()
            self.state("Saved locally · " + self.record.get("stage", "draft"))
            return True
        except (OSError, sqlite3.Error) as exc:
            self.save_failed = True
            self.app.save_warning()
            self.state("Save failed; edits retained, launch blocked: " + str(exc))
            return False

    @work(group="editor-validation", exclusive=True, exit_on_error=False)
    async def validate_target(self):
        await asyncio.sleep(0.2)
        if self.operation_busy or self.record.get("approved"):
            return
        revision, signature, snapshot = self.revision, self.validation_signature, copy.deepcopy(self.record)
        try:
            await self.app.io(lambda s: ops.validate_group_repository(s.config(), snapshot, snapshot["inputs"]["repo"]))
            # Prepare uses a worker-owned store; never save stale validation over a newer draft.
            plan = await self.app.io(lambda s: ops.target_plan(**{
                "repo": snapshot["inputs"]["repo"], "branch": snapshot["inputs"]["branch"], "base": snapshot["inputs"]["base"],
                "new": snapshot["inputs"]["new"], "root": s.root, "worktree_parent": snapshot["inputs"].get("worktree_parent", "")}))
            feedback = f"{plan['branch']}\n{plan['path']}\nPinned commit: {plan['commit']}"
            def references(s):
                items = []
                for item in snapshot["context"]:
                    try:
                        if item.get("relative"):
                            item = file_context(plan, item["relative"], item["mode"] == "snapshot", item.get("start"), item.get("end"))
                        elif item["mode"] == "reference" and snapshot.get("target") != plan:
                            raise TaskError("Legacy reference: remove and add it again for this target.")
                    except (TaskError, OSError) as exc:
                        item = {**item, "error": str(exc)}
                    items.append(item)
                return items
            items = await self.app.io(references)
            if signature != self.validation_signature or not self.is_mounted or self.operation_busy or self.record.get("approved"):
                return
            self.record["target"] = plan
            self.query_one("#editor-summary", Static).update(handover_label(self.record) + '\n' + ', '.join(t['key'] for t in handover_tickets(self.record)))
            if items != self.record["context"]:
                self.record["context"] = items
                self.refresh_prompt()
                self.paint_context()
            self.record["prepared_inputs"] = dict(snapshot["inputs"])
            self.valid_revision = self.revision
            self.flush()
        except (TaskError, OSError, ValueError) as exc:
            feedback = str(exc)
        if self.is_mounted and signature == self.validation_signature:
            self.query_one("#target-feedback", Static).update(feedback)
            self.query_one("#context-file", Button).disabled = self.valid_revision != self.revision


    @work(group="editor-branches", exclusive=True, exit_on_error=False)
    async def load_branches(self):
        repo = self.record["inputs"]["repo"]
        new = self.record["inputs"]["new"]
        default = ""
        try:
            if new and not self.record["inputs"].get("base") and "base" not in self.record.get("manual_fields", []):
                default = await self.app.io(lambda s: default_base(repo, {**s.config().get("branch_defaults", {}), **s.config().get("repositories", {}).get(repo, {})}.get("base", "")))
            branches = await self.app.io(lambda s: base_branches(repo) if new else git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines())
        except TaskError:
            branches = []
        if self.is_mounted and (repo, new) == (self.record["inputs"]["repo"], self.record["inputs"]["new"]):
            if default and not self.record["inputs"].get("base") and "base" not in self.record.get("manual_fields", []):
                self.record["inputs"]["base"] = default
                self.query_one("#target-base", Input).value = default
                self.changed()
            search = self.query_one("#branch-search", Input).value.casefold()
            self.query_one("#branch-choice", Select).set_options([(b, b) for b in branches if search in b.casefold()])

    @on(Input.Changed)
    @on(TextArea.Changed)
    @on(Select.Changed)
    def input_changed(self, event):
        event.stop()
        if not self.ready or self.synchronizing or self.record.get("approved"):
            return
        widget = event.control
        ident = widget.id
        value = widget.text if isinstance(widget, TextArea) else widget.value
        if value is Select.NULL:
            return
        if ident == "workspace-search":
            self.query_one("#workspace-choice", Select).set_options(self.workspace_options(value))
            return
        if ident == "branch-search":
            self.load_branches()
            return
        if ident == "workspace-choice":
            self.query_one("#target-repo", Input).value = value
            return
        if ident == "branch-choice":
            self.query_one("#target-base" if self.record["inputs"]["new"] else "#target-branch", Input).value = value
            return
        fields = {"target-repo": "repo", "target-branch": "branch", "target-base": "base", "target-new": "new", "target-parent": "worktree_parent"}
        if ident in fields:
            key = fields[ident]
            if self.record["inputs"][key] == value:
                return
            self.record["inputs"][key] = value
            self.record.setdefault("manual_fields", [])
            if key not in self.record["manual_fields"]:
                self.record["manual_fields"].append(key)
            if key == "repo":
                self.record["repository_source"] = "User-selected checkout"
                self.query_one("#repository-source", Static).update("User-selected checkout")
                self.apply_defaults()
            if key in ("repo", "new"):
                self.query_one("#base-fields").display = self.record["inputs"]["new"]
                self.load_branches()
        elif ident == "target-agent":
            if self.record["agent"] == value:
                return
            self.record["agent"] = value
            if "agent" not in self.record.setdefault("manual_fields", []):
                self.record["manual_fields"].append("agent")
        elif ident in ("prompt-preset", "prompt-notes"):
            if self.record.get("preset" if ident == "prompt-preset" else "notes", "") == value:
                return
            self.record["preset" if ident == "prompt-preset" else "notes"] = value
            if ident == "prompt-preset" and "preset" not in self.record.setdefault("manual_fields", []):
                self.record["manual_fields"].append("preset")
        elif ident == "opening-prompt":
            if value == self.record.get("prompt"):
                return
            self.record.update(prompt=value, prompt_mode="custom")
        elif ident in ("context-label", "context-body") and self.context_index is not None:
            item = self.record["context"][self.context_index]
            field = "label" if ident == "context-label" else "text"
            if field == "text" and item["mode"] != "text":
                return
            if item.get(field) == value:
                return
            item[field] = value
        else:
            return
        self.refresh_prompt()
        self.changed()

    def apply_defaults(self):
        config = self.app.store.config()
        defaults = {**config.get("branch_defaults", {}), **config["repositories"].get(self.record["inputs"]["repo"], {})}
        for key, ident, value in [("branch", "target-branch", branch_name(defaults.get("branch_pattern", "feature/{number}-{slug}"), self.record["ticket"])),
                                  ("base", "target-base", defaults.get("base", "")), ("worktree_parent", "target-parent", defaults.get("worktree_parent", ""))]:
            if key not in self.record.get("manual_fields", []):
                self.record["inputs"][key] = value
                self.query_one("#" + ident, Input).value = value
        if "agent" not in self.record.get("manual_fields", []) and defaults.get("agent") in self.agents.values():
            self.record["agent"] = defaults["agent"]
            self.query_one("#target-agent", Select).value = defaults["agent"]
        if "preset" not in self.record.get("manual_fields", []) and defaults.get("preset") in config["presets"]:
            self.record["preset"] = defaults["preset"]
            self.query_one("#prompt-preset", Select).value = defaults["preset"]

    def paint_context(self):
        table = self.query_one("#context-table", DataTable)
        table.clear()
        for i, item in enumerate(self.record["context"]):
            from rich.text import Text
            table.add_row(Text(item["label"]), item["mode"], "Needs repair" if item.get("error") else "Included", key=str(i))
        if self.context_index is not None and self.context_index < len(self.record["context"]):
            table.move_cursor(row=self.context_index)
            self.show_context()
        else:
            self.context_index = None

    @on(DataTable.RowHighlighted, "#context-table")
    def context_selected(self, event):
        event.stop()
        self.context_index = int(event.row_key.value)
        self.show_context()

    def show_context(self):
        if self.context_index is None or self.context_index >= len(self.record["context"]):
            return
        item = self.record["context"][self.context_index]
        # Ignore queued programmatic editor events by comparing values in the handler.
        self.query_one("#context-label", Input).value = item["label"]
        body = self.query_one("#context-body", TextArea)
        body.read_only = item["mode"] != "text"
        body.load_text(item.get("text", item.get("target", "")) + ("\n" + item["error"] if item.get("error") else ""))

    @on(Button.Pressed)
    def button(self, event):
        event.stop()
        if not self.operation_busy:
            self.action(event.button.id)

    @work(group="editor-action", exit_on_error=False)
    async def action(self, ident):
        self.operation_busy = True
        token = self.app.begin_action({"editor": self, "record": self.record,
            "target": self.record["ticket"]["project"] + " · " + self.record["ticket"]["key"] + " · " + self.record["ticket"]["title"]})
        try:
            if ident == "editor-issues":
                updated = await self.app.group_issues(self.record)
                if updated:
                    self.record = updated
                    self.query_one('#editor-summary', Static).update(handover_label(updated))
                    self.refresh_prompt()
                    self.changed()
                return
            if ident == "editor-save":
                self.flush()
                return
            if ident == "editor-close":
                self.flush()
                self.app.show_tab("handovers")
                return
            if ident == "editor-review":
                if not self.flush():
                    return
                self.disabled = True
                try:
                    await self.app.review_launch(self.record)
                finally:
                    self.disabled = bool(self.record.get("approved"))
                self.paint_context()
                self.refresh_prompt()
                self.app.refresh_linked_prs()
                return
            if ident == "prompt-copy":
                self.app.copy_to_clipboard(self.query_one("#opening-prompt", TextArea).text)
                return
            if ident == "prompt-regenerate":
                if self.record["prompt_mode"] == "custom" and not await self.app.confirm("Replace edited prompt?", "This replaces your per-handover edits with current instructions and context."):
                    return
                self.refresh_prompt(True)
            elif ident == "prompt-keep":
                self.record["generated_prompt"] = ""  # reset comparison to current generated inputs below
                saved = self.record["prompt"]
                self.refresh_prompt(True)
                self.record.update(prompt=saved, prompt_mode="custom", prompt_outdated=False)
                self.refresh_prompt()
            elif ident.startswith("context-"):
                await self.context_action(ident.removeprefix("context-"))
                self.paint_context()
                self.refresh_prompt()
            self.changed()
        except (TaskError, ValueError, OSError) as exc:
            self.state(str(exc))
        finally:
            self.app.end_action(token)
            self.operation_busy = False

    async def context_action(self, action):
        if action == 'packs':
            from .productivity_ui import pack_menu
            return await pack_menu(self.app, self)
        from .console import Picker
        r = self.record
        item = None
        if action == "text":
            item = {"label": "Notes", "mode": "text", "text": ""}
        elif action in ("remove", "up", "down"):
            index = self.context_index
            if index is None:
                raise TaskError("Select a context item first.")
            if action == "remove":
                r["context"].pop(index)
                self.context_index = min(index, len(r["context"]) - 1) if r["context"] else None
            else:
                target = index + (-1 if action == "up" else 1)
                if 0 <= target < len(r["context"]):
                    r["context"].insert(target, r["context"].pop(index))
                    self.context_index = target
        elif action in ("file", "attachment"):
            if action == "file" and self.valid_revision != self.revision:
                raise TaskError("Choose a valid target before adding repository files.")
            plan = r.get("target") or {}
            root = plan.get("path") if plan.get("existing") else plan.get("repo")
            path = await self.app.push_screen_wait(Picker(root or str(Path.home())))
            if path:
                if action == "attachment":
                    item = await self.app.io(lambda s: attach(s, r["id"], path))
                else:
                    relative = str(Path(path).relative_to(root)) if Path(path).is_absolute() else path
                    value = await self.app.form("Repository reference", [("snapshot", "Include text snapshot", False, "bool"), ("lines", "Optional line range, e.g. 10-25", "", "input")], message=relative)
                    if value:
                        parts = value["lines"].split("-") if value["lines"] else []
                        start, end = (int(parts[0]), int(parts[-1])) if parts else (None, None)
                        item = await self.app.io(lambda s: file_context(plan, relative, value["snapshot"], start, end))
        elif action in ("related", "comment"):
            ticket = r["ticket"]
            p = provider(ops.connection(self.app.store, ticket))
            items = await self.app.io(lambda s: p.related(ticket) if action == "related" else p.comments(ticket))
            index = await self.app.choice("Related issues" if action == "related" else "Comments", [(x["label"], str(i)) for i, x in enumerate(items)])
            if index is not None:
                source = items[int(index)]
                if action == "comment":
                    item = {**source, "mode": "snapshot", "source": ticket["url"]}
                else:
                    if source.get("url"):
                        candidates = lookup_candidates(self.app.store.config(), source["url"])
                        selection = await self.app.choice("Connection", [(c[0]["id"], str(i)) for i, c in enumerate(candidates)])
                        if selection is None:
                            return
                        c, key = candidates[int(selection)]
                        related = await self.app.io(lambda s: provider(c).get(key))
                    else:
                        related = await self.app.io(lambda s: p.get(source["id"]))
                    item = {"label": related["key"], "mode": "snapshot", "source": related["url"], "text": related["url"] + "\n" + related["title"] + "\n" + related.get("description", "") + "\n" + related.get("acceptance", "")}
        if item is not None:
            r["context"].append(item)
            self.context_index = len(r["context"]) - 1
