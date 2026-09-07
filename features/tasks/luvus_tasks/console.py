"""Mouse and keyboard task cockpit. Provider/Git work runs outside the UI thread."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
import webbrowser
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, Collapsible, DataTable, DirectoryTree, Footer, Header,
                             Input, Label, Select, SelectionList, Static, TabbedContent, TabPane, TextArea)

from .core import handover_tickets, handover_label, has_ticket, DEFAULT_INSTRUCTIONS, Store, TaskError, clean, compose, default_filters, delete_token, now, save_token, ticket_key
from .handover import (AGENTS, Luvus, attach, base_branches, default_base, branch_name, file_context, git, launch,
                       live_agent, matching_agent, repository, resume, save_record, target_plan, validate_issue_repository)
from .providers import GitHub, github_repositories, lookup_candidates, provider
from . import operations as ops
from .editor import HandoverEditor
from . import attention, workflow, workflow_ui, productivity, productivity_ui


class Preview(TextArea):
    """Selectable, plain text; shares the editor's native clipboard bindings."""
    BINDINGS = [("ctrl+a", "select_all", "Select all"), ("ctrl+shift+c", "copy", "Copy selection")]

    def __init__(self, text="", **kwargs):
        kwargs.pop("markup", None)
        super().__init__(clean(text), read_only=True, **kwargs)

    def update(self, text):
        value = clean(text)
        if value != self.text:
            self.load_text(value)


class IssueTable(DataTable):
    BINDINGS = [("space", "check_issue", "Select issue")]

    def action_check_issue(self):
        if self.row_count:
            self.app.selected_issue = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
            self.app.toggle_checked()

    async def _on_click(self, event):
        await super()._on_click(event)
        row = event.style.meta.get("row", -1)
        if event.button == 1 and event.style.meta.get("column") == 0 and row >= 0:
            self.app.selected_issue = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
            self.app.toggle_checked()


class Form(ModalScreen):
    """Small, accessible form shared by configuration and reviewed operations."""
    DEFAULT_CSS = """
    Form { align: center middle; background: $background 70%; }
    Form > Vertical { width: 90%; max-width: 110; height: 90%; border: round $accent; background: $surface; padding: 1 2; }
    Form .form-body { height: 1fr; }
    Form Label { margin-top: 1; }
    Form TextArea { height: 10; min-height: 5; }
    Form Input, Form Select { width: 100%; }
    Form .buttons { height: 3; margin-top: 1; }
    Form Button { margin-right: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title, fields=(), message="", submit="Continue", autosave=None, preview=None, exact_prompt=None, load_options=None):
        super().__init__()
        self.heading, self.fields, self.message = title, list(fields), message
        self.submit_label, self.autosave = submit, autosave
        self.initial = {f[0]: f[2] for f in self.fields}
        self.preview = preview
        self.exact_prompt = exact_prompt
        self.buffer_key = None
        self.load_options = load_options

    def on_mount(self):
        if self.load_options:
            self.load_choices()

    @work(group="form-options", exclusive=True, exit_on_error=False)
    async def load_choices(self):
        options, note = await self.load_options()
        if not self.is_mounted:
            return
        widget = self.query_one("#choice", Select)
        selected = widget.value
        widget.set_options(options or [("No matching results", "")])
        widget.value = selected if selected in [v for _, v in options] else (options[0][1] if options else "")
        self.query_one("#form-error", Static).update(note)
        self.query_one("#submit", Button).disabled = not options

    def stash(self):
        if self.buffer_key and self.is_mounted and self.fields and self.values() != self.initial:
            self.app.form_buffers[self.buffer_key] = {k: v for k, v in self.values().items()
                if next((f[3] for f in self.fields if f[0] == k), None) not in ("secret", "bool")}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.heading, markup=False)
            with VerticalScroll(classes="form-body"):
                if self.message:
                    yield Preview(self.message)
                if self.exact_prompt is not None:
                    with Collapsible(title="Exact opening prompt", collapsed=True):
                        yield Preview(self.exact_prompt)
                for key, label, value, kind in self.fields:
                    yield Label(label, markup=False)
                    if isinstance(kind, list):
                        yield Select([(clean(v[0]), v[1]) if isinstance(v, tuple) else (clean(v), v) for v in kind],
                                     value=value, allow_blank=False, id=key)
                    elif kind == "text":
                        yield TextArea(str(value), id=key)
                    elif kind == "bool":
                        yield Checkbox(label, value=bool(value), id=key)
                    else:
                        yield Input(str(value), password=kind == "secret", id=key)
                if self.preview:
                    yield Static("Checking target…", id="target-preview", markup=False)
            with Horizontal(classes="buttons"):
                yield Button(self.submit_label, id="submit", variant="primary")
                yield Button("Cancel", id="cancel")
            yield Static("", id="form-error", markup=False)

    def values(self):
        result = {}
        for key, _, _, kind in self.fields:
            widget = self.query_one("#" + key)
            result[key] = widget.text if kind == "text" else widget.value
        return result

    def changed(self):
        if self.autosave and self.is_mounted:
            try:
                self.autosave(self.values())
                self.save_failed = False
                self.query_one('#form-error', Static).update('Saved locally')
            except (TaskError, OSError, sqlite3.Error) as exc:
                self.save_failed = True
                self.query_one('#form-error', Static).update('Save failed; edits retained: ' + str(exc))
        if self.preview and self.is_mounted:
            self.run_worker(self.update_preview, group="target-preview", exclusive=True)

    async def update_preview(self):
        await asyncio.sleep(0.15)
        if not self.is_mounted:
            return
        values = self.values()
        try:
            result = await self.preview(values)
        except (TaskError, OSError, ValueError) as exc:
            result = str(exc)
        if self.is_mounted and self.values() == values:
            self.query_one("#target-preview", Static).update(clean(result))

    on_input_changed = lambda self, event: self.changed()
    on_text_area_changed = lambda self, event: self.changed()
    on_select_changed = lambda self, event: self.changed()
    on_checkbox_changed = lambda self, event: self.changed()

    @on(Button.Pressed)
    def button(self, event):
        event.stop()
        if event.button.id == "submit":
            if self.autosave:
                self.changed()
                if getattr(self, "save_failed", False):
                    return
            if self.load_options and not self.values().get("choice"):
                return
            if self.buffer_key:
                self.app.form_buffers.pop(self.buffer_key, None)
            self.dismiss(self.values())
        elif event.button.id == "discard":
            if self.buffer_key:
                self.app.form_buffers.pop(self.buffer_key, None)
            self.dismiss(None)
        else:
            self.action_cancel()

    def action_cancel(self):
        if getattr(self, "save_failed", False) or not self.autosave and self.values() != self.initial:
            self.query_one("#form-error", Static).update("Unsaved changes. Press Discard to close without saving.")
            if not self.query("#discard"):
                self.query_one(".buttons").mount(Button("Discard", id="discard", variant="warning"))
            return
        self.dismiss(None)


class InlineForm(Vertical):
    """Use the same controls in the stable Configuration tab, not a modal."""
    compose = Form.compose
    values = Form.values
    changed = Form.changed
    on_input_changed = Form.on_input_changed
    on_text_area_changed = Form.on_text_area_changed
    on_select_changed = Form.on_select_changed
    on_checkbox_changed = Form.on_checkbox_changed
    action_cancel = Form.action_cancel

    def __init__(self, title, fields=(), message="", submit="Continue", autosave=None):
        super().__init__()
        self.heading, self.fields, self.message = title, list(fields), message
        self.submit_label, self.autosave = submit, autosave
        self.initial = {f[0]: f[2] for f in self.fields}
        self.result = asyncio.get_running_loop().create_future()
        self.preview = None
        self.exact_prompt = None
        self.buffer_key = None
        self.load_options = None

    def dismiss(self, value):
        if not self.result.done():
            self.result.set_result(value)

    @on(Button.Pressed)
    def button(self, event):
        Form.button(self, event)


class RepositorySelection(Vertical):
    def __init__(self, repos, selected):
        super().__init__()
        self.repos, self.selected = repos, set(selected)
        self.result = asyncio.get_running_loop().create_future()

    def compose(self):
        yield Label("Explicit repository access · new repositories are never selected automatically")
        yield Input(placeholder="Filter repositories", id="repo-search")
        yield SelectionList(*[(r, r, r in self.selected) for r in self.repos], id="repo-choices")
        yield Static("", id="repo-count")
        with Horizontal(classes="buttons"):
            for label, ident in [("Select visible", "select-visible"), ("Clear visible", "clear-visible"), ("Refresh", "refresh-repos"), ("Save selection", "save-repos"), ("Cancel", "cancel-repos")]:
                yield Button(label, id=ident)

    @on(SelectionList.SelectedChanged)
    def selection_changed(self):
        widget = self.query_one(SelectionList)
        visible = {widget.get_option_at_index(i).value for i in range(widget.option_count)}
        self.selected.difference_update(visible)
        self.selected.update(widget.selected)
        self.query_one("#repo-count", Static).update(f"{len(self.selected)} selected")

    @on(Input.Changed)
    def search(self, event):
        widget = self.query_one(SelectionList)
        widget.clear_options()
        widget.add_options([(r, r, r in self.selected) for r in self.repos if event.value.casefold() in r.casefold()])

    @on(Button.Pressed)
    def button(self, event):
        event.stop()
        ident = event.button.id
        widget = self.query_one(SelectionList)
        if ident == "select-visible":
            widget.select_all()
        elif ident == "clear-visible":
            widget.deselect_all()
        elif not self.result.done():
            self.result.set_result(None if ident == "cancel-repos" else (ident, sorted(self.selected)))


class IssueSelection(ModalScreen):
    DEFAULT_CSS = """
    IssueSelection { align: center middle; background: $background 70%; }
    IssueSelection > Vertical { width: 90%; height: 85%; border: round $accent; background: $surface; padding: 1; }
    IssueSelection SelectionList { height: 1fr; }
    IssueSelection Horizontal { height: 3; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, tickets, selected):
        super().__init__()
        self.tickets = {productivity.identity(t): t for t in tickets}
        self.selected = {productivity.identity(t) for t in selected}

    def options(self, query=""):
        return [(Text(t['key'] + ' · ' + t['title'] + ' · ' + t['connection']), key, key in self.selected)
                for key, t in self.tickets.items() if query.casefold() in (t['key'] + ' ' + t['title'] + ' ' + t['connection']).casefold()]

    def compose(self):
        with Vertical():
            yield Label("One handover · one repository · all selected issues")
            yield Input(placeholder="Search issues", id="group-search")
            yield SelectionList(*self.options(), id="group-choices")
            yield Static(f"{len(self.selected)} selected, including hidden matches", id="group-count")
            with Horizontal():
                yield Button("Select visible", id="group-select")
                yield Button("Clear visible", id="group-clear")
                yield Button("Continue", id="group-save", variant="primary")
                yield Button("Cancel", id="group-cancel")

    @on(Input.Changed, "#group-search")
    def search(self, event):
        widget = self.query_one(SelectionList)
        widget.clear_options()
        widget.add_options(self.options(event.value))

    @on(SelectionList.SelectedChanged)
    def selection(self):
        widget = self.query_one(SelectionList)
        visible = {widget.get_option_at_index(i).value for i in range(widget.option_count)}
        self.selected.difference_update(visible)
        self.selected.update(widget.selected)
        self.query_one('#group-count', Static).update(f"{len(self.selected)} selected, including hidden matches")

    @on(Button.Pressed)
    def button(self, event):
        event.stop()
        if event.button.id == 'group-select':
            self.query_one(SelectionList).select_all()
        elif event.button.id == 'group-clear':
            self.query_one(SelectionList).deselect_all()
        elif event.button.id == 'group-save':
            self.dismiss([t for k, t in self.tickets.items() if k in self.selected])
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class Picker(ModalScreen):
    DEFAULT_CSS = """
    Picker { align: center middle; background: $background 70%; }
    Picker > Vertical { width: 85%; height: 85%; border: round $accent; background: $surface; padding: 1; }
    Picker DirectoryTree { height: 1fr; }
    Picker Horizontal { height: 3; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, root):
        super().__init__()
        self.root = root

    def compose(self):
        with Vertical():
            yield Static("Choose a file; or paste an absolute path below", markup=False)
            yield DirectoryTree(self.root)
            yield Input(placeholder="File path", id="path")
            with Horizontal():
                yield Button("Choose", id="choose", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(DirectoryTree.FileSelected)
    def selected(self, event):
        self.query_one(Input).value = str(event.path)

    @on(Button.Pressed)
    def button(self, event):
        event.stop()
        self.dismiss(self.query_one(Input).value if event.button.id == "choose" else None)

    def action_cancel(self):
        self.dismiss(None)


class Cockpit(App):
    TITLE = "Luvus Tasks"
    CSS = """
    Screen { background: $background; }
    /* Select draws its border on its children, not on the outer control. */
    Input, TextArea, SelectCurrent, SelectOverlay, SelectionList, Checkbox { border: solid $border-blurred; }
    Input:focus, TextArea:focus, Select:focus > SelectCurrent,
    Select.-expanded > SelectCurrent, SelectOverlay:focus, SelectionList:focus, Checkbox:focus { border: solid $primary; }
    Input.-invalid, Input.-invalid:focus { border: solid $error; }
    TextArea { height: 10; min-height: 4; }
    Preview { height: 12; }
    InlineForm, InlineForm > Vertical, RepositorySelection { height: 1fr; }
    InlineForm .form-body { height: 1fr; }
    InlineForm Label { margin-top: 1; }
    .buttons { height: 3; }
    #config-editor { height: 1fr; }
    #config-navigation { height: 6; layout: grid; grid-size: 3; grid-columns: 1fr 1fr 1fr; grid-rows: 3; }
    #configuration #config-navigation Button { width: 1fr; min-width: 0; margin: 0; }
    #configuration .buttons Button { width: auto; min-width: 10; }
    #repo-choices { height: 1fr; }
    #summary { height: 2; padding: 0 1; color: $text-muted; }
    #errors { height: auto; max-height: 4; overflow-y: auto; color: $warning; padding: 0 1; }
    #tabs { height: 1fr; }
    #handover-container { height: 1fr; }
    #handover-empty { height: auto; padding: 1 2; }
    #handover-empty Static { height: auto; margin-bottom: 1; }
    #handover-empty-title { text-style: bold; }
    #handover-empty Button { width: 28; margin-bottom: 1; }
    .toolbar { height: auto; min-height: 3; layout: horizontal; }
    .toolbar Button { min-width: 10; margin-right: 1; }
    .toolbar Select { width: 1fr; }
    #search { width: 2fr; }
    #issue-body { height: 1fr; }
    #issues-table { width: 3fr; height: 1fr; }
    #issue-detail { width: 2fr; height: 1fr; border-left: solid $primary; padding: 0 1; }
    #issue-text { height: 1fr; }
    #handovers-table, #orch-table { height: 1fr; }
    #handover-text, #orch-text { height: 7; overflow-y: auto; padding: 0 1; }
    #configuration { padding: 1 2; }
    #configuration Button { width: 40; margin-bottom: 1; }
    #save-warning { height: auto; max-height: 3; color: $error; }
    #status { height: 2; padding: 0 1; background: $surface; }
    .narrow #issue-body { layout: vertical; }
    .narrow #issues-table { width: 100%; height: 1fr; }
    .narrow #issue-detail { width: 100%; height: 1fr; border-left: none; border-top: solid $primary; }
    .narrow .toolbar { overflow-x: auto; }
    """
    CSS += HandoverEditor.DEFAULT_CSS
    BINDINGS = [("ctrl+q", "quit", "Quit"), ("ctrl+r", "refresh", "Refresh"),
                ('s', "task_key('task-start')", 'Start / Continue'), ('f', "task_key('task-followup')", 'Follow-up'),
                ('e', "task_key('task-evidence')", 'Evidence'), ('a', "task_key('task-archive')", 'Archive'),
                (']', "task_key('task-next-attention')", 'Next attention'), ('f6', "task_key('task-review')", 'Review launch'),
                ("f2", "actions", "Actions"), ("ctrl+f", "search", "Search")]

    def __init__(self, root=None, host=None, network=True):
        super().__init__()
        self.store = Store(root)
        self.host = host or Luvus()
        self.network = network
        self.tasks, self.history, self.agents, self.orch = [], [], [], []
        self.selected_issue = self.selected_handover = self.selected_orch = None
        self.checked = set()
        self.busy = False
        self.host_busy = False
        self.filter_ready = False
        self.pending = []
        self.sort_column = 1
        self.sort_reverse = False
        self.pending_writes = set()
        self.editing_configuration = False
        self.dashboard_scope = ""
        self.scope_ticket_keys = self.scope_history_ids = None
        self.attention_items = []
        self.selected_attention = None
        self.expanded_attention = set()
        self.host_available = True
        self.editors = {}
        self.navigation_revision = 0
        self.form_buffers = {}
        self.action_contexts = []
        self.table_rows = {}
        self.visible_issue_ids = None
        self.action_context = __import__("contextvars").ContextVar("task_action_context", default=None)

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        for label, action in [('Start / Continue', 'task-start'), ('Review launch', 'task-review'), ('Prepare follow-up', 'task-followup'), ('Open evidence', 'task-evidence'), ('Archive / Restore', 'task-archive'), ('Next attention', 'task-next-attention'), ('Next phase', 'task-next-phase'), ('Up next', 'task-queue'), ('Add to Up next', 'task-enqueue'), ('Batch edit selected drafts', 'task-batch'), ('Capture task', 'task-capture'), ('Saved captures', 'task-captures'), ('Refresh current repository', 'refresh-repository')]:
            yield SystemCommand(label, 'Luvus Tasks', lambda action=action: self.dispatch(action))
        for title, action in [("Refresh issues", "refresh"), ("Look up issue", "lookup"), ("Prepare handover", "handover"),
                              ("Issue actions", "actions"), ("Handover actions", "history-actions"),
                              ("Configure connections", "connections"), ("Configure prompts", "prompts")]:
            yield SystemCommand(title, "Luvus Tasks", lambda action=action: self.dispatch(action))
        for title, action in [('Workflow recipes', 'workflow-recipes'), ('Workflow runs', 'workflow-runs'), ('Address PR feedback workflow', 'work-bounded')]:
            yield SystemCommand(title, 'Luvus Tasks', lambda action=action: self.dispatch(action))
        yield SystemCommand("Find work across tasks and native search", "Luvus Tasks", lambda: self.dispatch("work-search"))
        yield SystemCommand("Attention across all workspaces", "Luvus Tasks", lambda: self.dispatch("show-attention"))

    def compose(self):
        yield Header()
        yield Static("Task cockpit · Jira / Azure DevOps / GitHub", id="summary", markup=False)
        yield Static("", id="errors", markup=False)
        with TabbedContent(id="tabs"):
            with TabPane("Issues", id="issues"):
                with Horizontal(classes="toolbar"):
                    yield Input(placeholder="Search title, ID, status…", id="search")
                    yield Select([("All connections", "all")], value="all", allow_blank=False, id="connection")
                    yield Select([("All projects / repos", "all")], value="all", allow_blank=False, id="repository-filter")
                    yield Select([("All issues", "all"), ("Favorites", "favorites"), ("Recent", "recent"), ("Needs attention", "attention")], value="all", allow_blank=False, id="view")
                with Horizontal(id="issue-body"):
                    yield IssueTable(id="issues-table", cursor_type="row")
                    with VerticalScroll(id="issue-detail"):
                        yield Preview("Select an issue. Add a connection in Configuration to get started.", id="issue-text")
                with Horizontal(classes="toolbar"):
                    for label, ident in [("Start / Continue", "handover"), ("Select / deselect", "toggle"), ("Group selected", "group-issues"), ("Status", "status-change"), ("Browser", "browser"),
                                         ("⋯ Actions", "actions"), ("Copy", "copy"), ("Refresh", "refresh"), ("Refresh repo", "refresh-repository"), ("Up next", "task-queue"), ("Capture", "task-capture"), ("Captures", "task-captures"), ("Lookup", "lookup"), ("All repos", "clear-scope"), ("Clear selection", "clear-checked"), ("Reset filters", "reset-filters")]:
                        yield Button(label, id=ident)
            with TabPane("Handovers", id="handovers"):
                with Horizontal(classes="toolbar"):
                    yield Select([("All handovers", "all"), ("Needs attention", "attention"), ("Drafts", "draft"), ("Active", "active"), ("Archived", "archived")],
                                 value="all", allow_blank=False, id="history-filter")
                    yield Button("Open / Resume", id="resume")
                    yield Button("Edit draft", id="edit")
                    yield Button("More…", id="work-menu")
                    yield Button("Link / Open PRs", id="work-linked-prs")
                    yield Button("All repos", id="history-clear-scope")
                yield DataTable(id="handovers-table", cursor_type="row")
                yield Preview("Select a handover", id="handover-text")
            with TabPane("Draft editor", id="handover"):
                with Vertical(id="handover-container"):
                    with Vertical(id="handover-empty"):
                        yield Static("No handover draft is open", id="handover-empty-title")
                        yield Static("Prepare an agent's task here: choose its checkout, add context, and review the instructions before launch.")
                        yield Button("Choose an issue", id="handover-choose-issue", variant="primary")
                        yield Button("Browse saved drafts", id="handover-browse-drafts")
                        yield Static("For a new handover, select an issue and press Handover.\nFor a saved draft, select it in Handovers and press Edit draft.\n\nHandovers lists your previous work; Draft editor edits one draft at a time.")
            with TabPane("Attention", id="attention"):
                with Horizontal(classes="toolbar"):
                    yield Input(placeholder="Filter task, repository, worktree or reason", id="attention-search")
                    yield Button("Refresh", id="attention-refresh")
                    yield Button("Open", id="attention-open")
                    yield Button("Mark reviewed", id="attention-ack")
                    yield Button("Notifications", id="attention-notify")
                    yield Select([(label, ident) for label, ident in [('All sources', 'all'), ('Agent input', 'agent-input'), ('Finished work', 'agent-done'), ('PR feedback', 'pr-feedback'), ('CI', 'ci'), ('Validation', 'validation'), ('Review', 'review'), ('Uncertain', 'uncertainty')]], value='all', allow_blank=False, id='attention-source')
                    yield Select([('Current', 'current'), ('Snoozed', 'snoozed')], value='current', allow_blank=False, id='attention-visibility')
                    yield Button('Expand / Collapse', id='task-expand-attention')
                    yield Button('Snooze', id='task-snooze')
                    yield Button('Unsnooze', id='task-unsnooze')
                yield DataTable(id="attention-table", cursor_type="row")
                yield Preview("All workspaces in this Luvus session. Opening never approves input.", id="attention-detail")
            with TabPane("Configuration", id="configuration"):
                with Vertical(id="config-navigation"):
                    for label, ident in [("Connections / Test connection", "connections"), ("Saved filters", "filters"),
                                         ("Repository defaults & mappings", "repositories"), ("Prompts & instructions", "prompts"),
                                         ("Optional ORCH integration", "orch-settings"), ("Check Luvus", "doctor")]:
                        yield Button(label, id=ident)
                    yield Button("Repair invalid connection", id="repair-connection")
                    yield Button("Connection health", id="connection-health")
                    yield Button("Workflow recipes", id="workflow-recipes")
                    yield Button("Workflow runs", id="workflow-runs")
                    yield Button("Integration bridges", id="work-integrations")
                    yield Button("Scheduling compatibility", id="work-scheduling")
                yield Vertical(id="config-editor")
        yield Static("", id="save-warning", markup=False)
        yield Static("Ready", id="status", markup=False)
        yield Footer()

    async def on_mount(self):
        self.query_one("#issues-table", DataTable).add_columns("✓", "Issue", "Status", "Title", "Connection", "Repository")
        self.query_one("#handovers-table", DataTable).add_columns("Issues / group", "Branch", "PR / CI")
        self.query_one("#attention-table", DataTable).add_columns("Task / agent", "Reason", "Worktree", "Freshness", "Since")
        self.load_filters()
        self.set_class(self.size.width < 110, "narrow")
        self.set_interval(0.1, self.read_inbox)
        self.set_interval(15, self.update_host)
        self.set_interval(15, self.refresh_attention)
        self.set_interval(60, self.refresh_linked_prs)
        self.dispatch("initial")

    def begin_action(self, context):
        context.update(tab=self.query_one("#tabs", TabbedContent).active, cancelled=False)
        self.action_contexts.append(context)
        return self.action_context.set(context)

    def end_action(self, token):
        context = self.action_context.get()
        self.action_contexts = [c for c in self.action_contexts if c is not context]
        self.action_context.reset(token)

    def show_tab(self, ident):
        if ident in ("work", "orchestration"):
            ident = "handovers"
        context = self.action_context.get()
        if context is not None:
            if context.get("cancelled"):
                return
            context["tab"] = ident
        # Clear the old descendant focus before activation; it otherwise reactivates its hidden tab.
        self.set_focus(None)
        self.query_one("#tabs", TabbedContent).active = ident
        def focus_destination():
            tabs = self.query_one("#tabs", TabbedContent)
            if tabs.active != ident:
                return
            pane = self.query_one("#" + ident, TabPane)
            widgets = [w for w in pane.query("Input, Select, TextArea, Button, DataTable") if w.is_on_screen and not w.disabled]
            if widgets:
                widgets[0].focus()
        self.call_after_refresh(focus_destination)

    @on(TabbedContent.TabActivated, "#tabs")
    def tab_changed(self, event):
        if event.tabbed_content.id == "tabs":
            self.navigation_revision += 1
            for context in self.action_contexts:
                if event.pane.id != context["tab"]:
                    context["cancelled"] = True
            for editor in self.editors.values():
                if editor.ready:
                    editor.flush()

    def save_warning(self):
        failed = [e.record["ticket"]["key"] for e in self.editors.values() if e.save_failed]
        for warning in self.query("#save-warning"):
            warning.update("Unsaved drafts retained: " + ", ".join(failed) + ". Open the draft and retry Save." if failed else "")

    def on_resize(self, event):
        self.set_class(event.size.width < 110, "narrow")

    def load_filters(self):
        self.filter_ready = False
        config = self.store.config()
        self.query_one("#connection", Select).set_options([("All connections", "all")] + [(c["id"], c["id"]) for c in config["connections"]])
        self.query_one("#connection", Select).value = "all"
        self.project_filters()
        self.query_one("#view", Select).set_options([("All issues", "all"), ("Favorites", "favorites"), ("Recent", "recent"), ("Needs attention", "attention")] +
            [(c["id"] + " / " + f["name"], c["id"] + " / " + f["name"]) for c in config["connections"] for f in c.get("filters", [])])
        self.query_one("#view", Select).value = "all"
        self.filter_ready = True

    def project_filters(self, extra=None):
        projects = {t['project'] for t in self.tasks}
        for c in self.store.config()['connections']:
            projects.update(c.get('repositories', {}))
            if c['provider'] == 'github':
                projects.update(c.get('selected_repositories', [c.get('repository', '')]))
            elif c['provider'] == 'azure':
                projects.add(c.get('project', ''))
        projects.update(t['project'] for r in self.store.records('handovers') for t in handover_tickets(r))
        if extra:
            projects.add(extra)
        options = sorted(projects - {''})
        if options != getattr(self, 'project_options', None):
            control = self.query_one('#repository-filter', Select)
            selected = control.value
            with control.prevent(Select.Changed):
                control.set_options([('All projects / repos', 'all')] + [(p, p) for p in options])
                control.value = selected if selected in options else 'all'
            self.project_options = options

    async def io(self, fn):
        def run():
            store = Store(self.store.root)
            try:
                return fn(store)
            finally:
                store.db.close()
        return await asyncio.to_thread(run)

    async def form(self, *args, **kwargs):
        if self.editing_configuration:
            return await self.inline(InlineForm(*args, **kwargs))
        dialog = Form(*args, **kwargs)
        context = self.action_context.get()
        if context and context.get("cancelled"):
            return None
        if context and context.get("editor") and (self.active_editor() is not context["editor"] or self.query_one("#tabs", TabbedContent).active != "handover"):
            return None
        target = context.get("target", "") if context else ""
        dialog.buffer_key = (dialog.heading, target, tuple(f[0] for f in dialog.fields))
        saved = self.form_buffers.get(dialog.buffer_key, {})
        dialog.fields = [(k, label, saved.get(k, value), kind) for k, label, value, kind in dialog.fields]
        if target:
            dialog.message = target + ("\n\n" + dialog.message if dialog.message else "")
        return await self.push_screen_wait(dialog)

    async def inline(self, editor):
        self.show_tab("configuration")
        container = self.query_one("#config-editor", Vertical)
        await container.remove_children()
        await container.mount(editor)
        editor.query("Input, Select, TextArea, Button").first().focus()
        try:
            return await editor.result
        finally:
            await editor.remove()

    async def choice(self, title, options):
        if not options:
            raise TaskError("No choices available.")
        result = await self.form(title, [("choice", "Choose", options[0][1], options)])
        return result["choice"] if result else None

    async def confirm(self, title, message):
        return await self.form(title, message=message, submit="Confirm") is not None

    async def copy_data(self):
        preview = self.query_one("#handover-text" if self.query_one("#tabs", TabbedContent).active == "handovers" else "#issue-text", Preview)
        values = {"Selection": preview.selected_text, "Full details": preview.text}
        if self.query_one("#tabs", TabbedContent).active == "handovers":
            values["Handover prompt"] = self.record().get("prompt", "")
        else:
            ticket = self.issue()
            values.update({"Issue ID": ticket["key"], "Issue URL": ticket["url"],
                           "Issue row": "\t".join(ticket[k] for k in ("key", "status", "title", "project"))})
        selected = await self.choice("Copy plain text", [(k, k) for k, v in values.items() if v])
        if selected:
            self.copy_to_clipboard(clean(values[selected]))
            self.status("Copied · " + selected)

    def status(self, message):
        self.query_one("#status", Static).update(clean(message))

    def issue(self):
        context = self.action_context.get()
        if context and context.get("issue"):
            return copy.deepcopy(context["issue"])
        item = next((t for t in self.tasks if ticket_key(t) == self.selected_issue), None)
        if not item:
            raise TaskError("Select an issue first.")
        return copy.deepcopy(item)

    def record(self):
        context = self.action_context.get()
        if context and context.get("record"):
            return copy.deepcopy(context["record"])
        record = next((r for r in self.history if r["id"] == self.selected_handover), None)
        if not record:
            raise TaskError("Select a handover first.")
        return copy.deepcopy(record)

    def replace_rows(self, table, rows, selected):
        if self.table_rows.get(table.id) != rows:
            x, y = table.scroll_x, table.scroll_y
            with table.prevent(DataTable.RowHighlighted):
                table.clear()
                for key, cells in rows:
                    table.add_row(*cells, key=key)
                keys = [key for key, _ in rows]
                if selected in keys:
                    table.move_cursor(row=keys.index(selected), scroll=False)
            table.scroll_to(x=x, y=y, animate=False, force=True)
            self.table_rows[table.id] = rows
        keys = [key for key, _ in rows]
        if selected in keys and table.cursor_row != keys.index(selected):
            with table.prevent(DataTable.RowHighlighted):
                table.move_cursor(row=keys.index(selected))
        table.show_cursor = selected is not None

    def selection_buttons(self):
        for ident in ("handover", "toggle", "status-change", "browser", "actions", "copy"):
            self.query_one("Button#" + ident, Button).disabled = self.selected_issue is None
        for ident in ("resume", "edit", "work-menu", "work-linked-prs"):
            self.query_one("Button#" + ident, Button).disabled = self.selected_handover is None

    @work(group="attention-snapshot", exit_on_error=False)
    async def refresh_attention(self):
        if getattr(self, "attention_busy", False):
            return
        self.attention_busy = True
        try:
            agents, orch, available = copy.deepcopy(self.agents), copy.deepcopy(self.orch), self.host_available
            items = await self.io(lambda store: attention.project(store, agents, orch, available))
            if (agents, orch, available) == (self.agents, self.orch, self.host_available):
                self.attention_items = items
                self.paint(False)
            else:
                self.set_timer(0.1, self.refresh_attention)
        except (TaskError, OSError, ValueError) as exc:
            self.status(str(exc))
        finally:
            self.attention_busy = False

    @work(group="issue-refresh", exit_on_error=False)
    async def refresh_issues(self, scope=None, message="Refreshing issues…"):
        if getattr(self, "issues_refreshing", False):
            self.status("Issue refresh already running.")
            return
        self.issues_refreshing = True
        def display(tasks, errors):
            if self.store.config() != config:
                return
            self.tasks = tasks
            self.query_one("#errors", Static).update("\n".join(errors))
            self.paint()
        try:
            self.status(message)
            config = self.store.config()
            def fetch(store):
                def progress(tasks, errors):
                    if self.is_running:
                        try:
                            self.call_from_thread(display, tasks, errors)
                        except RuntimeError:
                            pass  # Console closed while a read was finishing; cached results remain.
                return ops.refresh(store, self.network, scope=scope, on_group=progress)
            tasks, errors = await self.io(fetch)
            display(tasks, errors)
            self.refresh_attention()
            if self.store.config() != config:
                self.status("Settings changed · refresh issues again")
            elif errors:
                self.status("Issue refresh completed with errors · cached issues retained")
            else:
                self.status(f"Issue refresh complete · {len(tasks)} issues")
        except asyncio.CancelledError:
            self.status("Issue refresh cancelled · cached issues retained")
            raise
        except Exception as exc:
            self.status("Issue refresh failed: " + str(exc))
        finally:
            self.issues_refreshing = False

    def checkout_badge(self, record):
        from .checkout import badge
        path = (record.get('target') or {}).get('path', '')
        value = self.store.preference('checkout:' + path, {})
        return badge(value) + (' · stale' if value and attention.age(value.get('at')) > 30 else '')

    def paint(self, recompute_attention=True):
        from . import pr_links
        self.project_filters()
        self.pending_writes = {r["ticket"] for r in self.store.db.execute("SELECT ticket,data FROM writes") if json.loads(r["data"])["state"] == "pending"}
        self.history = self.store.records("handovers")
        # Rendering consumes the last observation; Git/provider work belongs to workers.
        table = self.query_one("#issues-table", DataTable)
        issue_rows = []
        query = self.query_one("#search", Input).value.casefold()
        connection = self.query_one("#connection", Select).value
        repo_filter = self.query_one("#repository-filter", Select).value
        view = self.query_one("#view", Select).value
        favorites = self.store.preference("favorites", [])
        recent = self.store.preference("recent", [])
        sort_key = {1: "key", 2: "status", 3: "title", 4: "connection"}.get(self.sort_column, "key")
        tasks = sorted(self.tasks, key=lambda t: str(t.get(sort_key, "")).casefold(), reverse=self.sort_reverse)
        if view == "recent":
            tasks.sort(key=lambda t: recent.index(ticket_key(t)) if ticket_key(t) in recent else len(recent))
        visible = []
        for t in tasks:
            key = ticket_key(t)
            if self.scope_ticket_keys is not None and key not in self.scope_ticket_keys:
                continue
            if connection not in ("all", t["connection"]) or query not in (t["key"] + " " + t["title"] + " " + t["status"]).casefold():
                continue
            if repo_filter not in ("all", t["project"]):
                continue
            if view == "favorites" and key not in favorites or view == "recent" and key not in recent:
                continue
            if view == "attention" and key not in self.pending_writes and not any(any(ticket_key(t) == key for t in handover_tickets(r)) and self.attention(r) for r in self.history):
                continue
            if view not in ("all", "favorites", "recent", "attention") and view not in t.get("views", []):
                continue
            visible.append(key)
            issue_rows.append((key, ("☑" if key in self.checked else "☐", Text(t["key"] + (" ★" if key in favorites else "")),
                          Text(t["status"]), Text(t["title"]), Text(t["connection"]), Text(t["project"]))))
        self.visible_issue_ids = set(visible)
        if visible and not self.table_rows.get(table.id) and self.selected_issue is None:
            self.selected_issue = visible[0]
        if self.selected_issue not in visible:
            self.selected_issue = None
            self.query_one("#issue-text", Preview).update("Select a visible issue." if visible else "No matching issues. Clear search/filters or use All repos to reset scope.")
        self.replace_rows(table, issue_rows, self.selected_issue)
        self.paint_issue()
        self.history = self.store.records("handovers")
        table = self.query_one("#handovers-table", DataTable)
        history_rows = []
        shown = []
        for r in self.history:
            if self.scope_history_ids is not None and r["id"] not in self.scope_history_ids:
                continue
            activity = self.activity(r)
            mode = self.query_one("#history-filter", Select).value
            if bool(r.get("archived_at")) != (mode == "archived"):
                continue
            if mode == "draft" and r["stage"] != "draft" or mode == "attention" and not self.attention(r):
                continue
            if mode == "active" and (not matching_agent(self.agents, r) or self.attention(r)):
                continue
            shown.append(r["id"])
            history_rows.append((r["id"], (Text(handover_label(r)),
                          Text((r.get("target") or r.get("inputs") or {}).get("branch", "Not chosen")), Text(pr_links.summary(self.store, r)))))
        if shown and not self.table_rows.get(table.id) and self.selected_handover is None:
            self.selected_handover = shown[0]
        if self.selected_handover not in shown:
            self.selected_handover = None
            self.query_one("#handover-text", Preview).update("Select a visible handover." if shown else "No matching handovers. Choose All handovers or All repos to reset filters/scope.")
        available = max(60, self.size.width - 8)
        for column, width in zip(table.columns.values(), (int(available * .36), int(available * .27), int(available * .37))):
            column.auto_width = False
            column.width = width
        self.replace_rows(table, history_rows, self.selected_handover)
        self.selection_buttons()
        self.query_one("#summary", Static).update(f"{len(visible)} issues · {len(self.checked)} selected ({len(self.checked & set(visible))} visible, {len(self.checked - set(visible))} hidden) · {len(self.history)} handovers · "
                                                  f"{sum(self.attention(r) for r in self.history)} need attention" + (" · Scope: " + self.dashboard_scope if self.dashboard_scope else ""))
        self.paint_attention()
        self.paint_work()

    def paint_attention(self):
        table = self.query_one("#attention-table", DataTable)
        rows = []
        query = self.query_one("#attention-search", Input).value.casefold()
        visible = []
        source = self.query_one('#attention-source', Select).value
        snoozed_view = self.query_one('#attention-visibility', Select).value == 'snoozed'
        ordered = sorted(self.attention_items, key=lambda x: (x['state'] != 'active', x['source'] not in ('agent-input', 'ci', 'validation', 'uncertainty'), x.get('handover') or x['id'], x['since']))
        groups = {}
        for item in ordered:
            if productivity.snoozed(self.store, item) != snoozed_view or source not in ('all', item['source']):
                continue
            if query not in (item['task'] + ' ' + item['title'] + ' ' + item['worktree']).casefold():
                continue
            groups.setdefault(item.get('handover') or item['id'], []).append(item)
        for group, items in groups.items():
            shown = items if group in self.expanded_attention else items[:1]
            for i, item in enumerate(shown):
                title = item['title']
                if i == 0 and len(items) > 1:
                    title += ' · ' + str(len(items)) + ' observations (Expand / Collapse)'
                rows.append((item['id'], (Text(item['task']), Text(title), Text(item['worktree']), item['state'], item['since'])))
                visible.append(item['id'])
        if self.selected_attention not in visible:
            self.selected_attention = visible[0] if visible else None
        self.replace_rows(table, rows, self.selected_attention)
        self.paint_attention_detail()

    def paint_attention_detail(self):
        item = next((x for x in self.attention_items if x["id"] == self.selected_attention), None)
        self.query_one("#attention-open", Button).disabled = item is None
        can_ack = item is not None and item["source"] not in ("agent-input", "uncertainty", "validation", "ci")
        self.query_one("#attention-ack", Button).disabled = not can_ack
        if item is None:
            text = "No matching attention items. Refresh to check native agent state. PR/CI freshness is shown with each result."
        else:
            text = item["title"] + "\n" + item["task"] + " · " + item["worktree"]
            text += "\nSource: " + item["source"] + " · " + item["state"] + " · Since: " + item["since"]
            text += "\nOpen focuses the recorded agent." if item["target"].get("agent") else "\nOpen shows this handover's evidence."
            text += "\nMark reviewed acknowledges this observation only." if can_ack else "\nThis clears at its source. Opening never approves or resolves it."
        self.query_one("#attention-detail", Preview).update(text)

    def paint_work(self):
        from . import pr_links
        record = next((r for r in self.store.records("handovers") if r["id"] == self.selected_handover), None)
        if not record:
            self.query_one("#handover-text", Preview).update("Select a handover to see its issues, branch and PRs.")
            return
        target = record.get("target") or record.get("inputs") or {}
        lines = [handover_label(record), "Issues: " + ", ".join(t["key"] for t in handover_tickets(record)),
                 "Branch: " + (target.get("branch") or "Not chosen") + " · " + record["stage"],
                 "Agent: " + record.get("agent", "Not chosen")]
        for pr, item in pr_links.items(self.store, record):
            lines.append((pr.get('branch') or target.get('branch', '')) + ' · ' + pr_links.short_status(pr, item))
            if pr.get('url'):
                lines.append(pr['url'])
        if not pr_links.items(self.store, record):
            lines.append("PRs are discovered automatically after launch. Link / Open PRs for existing PRs.")
        if record.get('pr_discovery_notice'):
            lines.append(record['pr_discovery_notice'])
        if target.get('path') and not Path(target['path']).is_dir():
            lines += ["Local worktree no longer exists: " + target['path'], "Saved issues and PR links remain available. Archive this handover if finished."]
        if record.get('error'):
            lines.append(record['error'])
        self.query_one("#handover-text", Preview).update("\n".join(lines))

    @on(Select.Changed, "#attention-source")
    @on(Select.Changed, "#attention-visibility")
    @on(Input.Changed, "#attention-search")
    def attention_search_changed(self):
        self.paint_attention()

    @work(group="pr-refresh", exclusive=True, exit_on_error=False)
    async def refresh_linked_prs(self):
        if not self.network or self.busy:
            return
        try:
            await self.io(lambda s: workflow.poll_prs(s))
            self.refresh_attention()
            self.paint()
            from .ui import App as NativeApp
            await self.io(lambda s: NativeApp(s, self.host).dock(False))
        except TaskError as exc:
            self.status(str(exc))

    def activity(self, record):
        agent = matching_agent(self.agents, record)
        return agent.get("status", "unknown") if agent else "unavailable"

    def attention(self, record):
        return any(x["handover"] == record["id"] for x in self.attention_items)

    @on(Input.Changed, "#search")
    def search_changed(self):
        if self.filter_ready:
            self.paint(False)

    @on(Select.Changed)
    def filter_changed(self, event):
        if event.select.id in ("view", "connection", "repository-filter", "history-filter") and self.filter_ready:
            self.paint(False)

    @on(DataTable.RowHighlighted)
    def row(self, event):
        key = event.row_key.value
        table = event.data_table
        if table.id in ("issues-table", "handovers-table"):
            if not table.show_cursor or not table.row_count or table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value != key:
                return
        if event.data_table.id == "attention-table":
            self.selected_attention = key
            self.paint_attention_detail()
            return
        if event.data_table.id == "issues-table":
            self.selected_issue = key
            self.selection_buttons()
            self.paint_issue()
        elif event.data_table.id == "handovers-table":
            self.selected_handover = key
            self.selection_buttons()
            r = next((r for r in self.history if r["id"] == key), None)
            if r:
                self.paint_work()

    def paint_issue(self):
        key = self.selected_issue
        t = next((t for t in self.tasks if ticket_key(t) == key), None)
        if t:
            self.query_one("#issue-text", Preview).update(clean(f"{t['key']} · {t['status']}\n{t['title']}\n{t['url']}\n"
                f"Refreshed: {t.get('refreshed', 'lookup')} {'[cached]' if t.get('stale') else ''}\n\n{t.get('description', '')}\n\n"
                f"Acceptance criteria\n{t.get('acceptance') or 'Not supplied'}\n\n"
                + "\n".join(f"Handover: {r['stage']} · {self.activity(r)}" for r in self.history if any(ticket_key(t) == key for t in handover_tickets(r)))))

    @on(DataTable.RowSelected, "#issues-table")
    def toggle_row(self, event):
        # Row activation highlights only; batch selection is an explicit action.
        self.selected_issue = event.row_key.value
        event.data_table.show_cursor = True
        self.row(event)
        self.selection_buttons()

    @on(DataTable.RowSelected, "#handovers-table")
    def select_handover_row(self, event):
        self.selected_handover = event.row_key.value
        event.data_table.show_cursor = True
        self.row(event)
        self.selection_buttons()

    def toggle_checked(self):
        key = self.selected_issue
        if not key:
            return
        self.checked.symmetric_difference_update({key})
        self.paint()

    @on(DataTable.HeaderSelected, "#issues-table")
    def sort_issues(self, event):
        self.sort_reverse = not self.sort_reverse if self.sort_column == event.column_index else False
        self.sort_column = event.column_index
        self.paint()

    @on(Button.Pressed)
    def button(self, event):
        self.dispatch(event.button.id)

    def action_task_key(self, action):
        if isinstance(self.screen, ModalScreen):
            return
        if action == 'task-review':
            if self.query_one('#tabs', TabbedContent).active == 'handover':
                self.dispatch(action)
        elif isinstance(self.focused, DataTable):
            if action == 'task-start' or action == 'task-next-attention' or self.focused.id == 'handovers-table':
                self.dispatch(action)

    def action_refresh(self):
        self.dispatch("refresh")

    def action_actions(self):
        self.dispatch("history-actions" if self.query_one("#tabs", TabbedContent).active == "handovers" else "actions")

    def action_search(self):
        self.show_tab("issues")
        self.query_one("#search", Input).focus()

    def is_navigation(self, action, payload=None):
        if action in ("handover-choose-issue", "work-choose-issue", "work-browse-handovers", "handover-browse-drafts", "show-attention", "work-search", "edit", "clear-scope", "history-clear-scope", "clear-checked", "toggle", "reset-filters", "task-evidence", "task-next-attention", "task-expand-attention", "attention-open", "copy", "copy-history", "browser"):
            return True
        if action == "inbox" and payload:
            key = payload.get("ticket", "") or ""
            if payload.get("action") == "open" and not key.startswith("dashboard:"):
                return True
            if payload.get("action") in ("attention", "find-work", "copy-selection"):
                return True
            if key.startswith("dashboard:"):
                data = json.loads(key[10:])
                return data.get("action") in (None, "work-pr-details", "task-evidence", "task-next-attention", "task-expand-attention")
        return False

    @work(group="navigation", exclusive=True, exit_on_error=False)
    async def navigate(self, action, payload=None):
        self.navigation_revision += 1
        self.action_context.set(None)
        if isinstance(self.screen, Form):
            self.screen.stash()
            self.screen.dismiss(None)
        try:
            await self.perform(action, payload)
            if action not in ("edit", "handover-choose-issue", "work-choose-issue", "show-attention", "copy", "copy-history", "browser"):
                self.paint()
        except (TaskError, ValueError, OSError, KeyError) as exc:
            self.status(str(exc))

    def read_inbox(self):
        session = ops.session_key()
        owner = self.store.preference("console:" + session, {})
        if self.network and owner.get("inbox") and str(owner.get("pane")) != os.environ.get("LUVUS_PANE_ID"):
            return
        self.pending.extend(self.store.take_actions(owner.get("inbox", session)))
        navigation = [m for m in self.pending if self.is_navigation("inbox", m)]
        if navigation:
            self.pending = [m for m in self.pending if not self.is_navigation("inbox", m)]
            self.navigate("inbox", navigation[-1])
        if self.pending and not self.busy:
            self.dispatch("inbox", self.pending.pop(0))

    @work(exclusive=True, group="host", exit_on_error=False)
    async def update_host(self):
        if self.busy or self.host_busy or not self.network:
            return
        self.host_busy = True
        try:
            self.agents = await self.io(lambda s: self.host.agents())
            self.host_available = True
            if self.store.config().get("orchestration"):
                state = await self.io(lambda s: ops.orch_snapshot(self.host))
                self.orch = state["tasks"]
            self.refresh_attention()
            self.paint()
            from .ui import App as NativeApp
            await self.io(lambda s: NativeApp(s, self.host).dock(False))
        except TaskError:
            self.host_available = False
            self.agents = []
            self.paint()
        finally:
            self.host_busy = False

    @work(group="action", exit_on_error=False)
    async def dispatch(self, action, payload=None):
        from .editor import HandoverEditor
        # Reopening a single saved draft is navigation, even through Start / Continue.
        if action in ("task-start", "handover", "Handover"):
            if self.query_one("#tabs", TabbedContent).active in ("handovers", "work") and action == "task-start":
                matches = [r for r in self.history if r["id"] == self.selected_handover]
            else:
                ticket = next((t for t in self.tasks if ticket_key(t) == self.selected_issue), None)
                matches = productivity.candidates(self.store, ticket) if ticket else []
            if len(matches) == 1 and matches[0].get("stage") == "draft" and not matches[0].get("approved"):
                self.selected_handover = matches[0]["id"]
                self.history = self.store.records("handovers")
                action = "edit"
        if self.is_navigation(action, payload):
            self.navigate(action, payload)
            return
        if self.busy:
            self.status("Finish the current action first.")
            return
        self.busy = True
        record = next((r for r in self.history if r["id"] == self.selected_handover), None)
        issue = next((t for t in self.tasks if ticket_key(t) == self.selected_issue), None)
        chosen = record["ticket"] if record and (action.startswith("work-") or action in ("resume", "history-actions", "copy-history")) else issue
        token = self.begin_action({"record": copy.deepcopy(record), "issue": copy.deepcopy(issue),
            "target": (chosen["project"] + " · " + chosen["key"] + " · " + chosen["title"]) if chosen else ""})
        if action == "inbox":
            self.action_context.get().update(record=None, issue=None, target="")
        self.status("Working…")
        try:
            await self.perform(action, payload)
            configuration = action in ("connections", "filters", "repositories", "prompts", "orch-settings", "doctor")
            copied = action in ("copy", "copy-history") or (action == "inbox" and (payload or {}).get("action") == "copy-selection")
            changed = not copied and action != "browser"
            if not configuration and changed:
                self.paint()
            if self.network and not configuration and changed:
                try:
                    from .ui import App as LegacyApp
                    await self.io(lambda s: LegacyApp(s, self.host).dock(False))
                except TaskError:
                    pass  # The action itself is durable; a stale sidebar is not a failed write.
            if not copied and not self.action_context.get().get("cancelled") and not self.action_context.get().get("refresh_scheduled"):
                self.status("Ready · external changes require confirmation")
        except (TaskError, ValueError, OSError, KeyError) as exc:
            self.status(str(exc))
            await self.form("Action could not complete", message=str(exc), submit="Close")
        finally:
            self.end_action(token)
            self.busy = False
            if action in ("initial", "refresh"):
                self.refresh_linked_prs()

    async def group_issues(self, record=None):
        cached, _ = await self.io(lambda s: ops.refresh(s, False))
        members = handover_tickets(record) if record else []
        tickets = {productivity.identity(t): t for t in cached + self.tasks + members}
        if record is None:
            members = [t for t in tickets.values() if ticket_key(t) in self.checked]
        chosen = await self.push_screen_wait(IssueSelection(list(tickets.values()), members))
        if chosen is None:
            return None
        if not chosen:
            raise TaskError('Select at least one issue.')
        value = await self.form('Grouped handover', [('title', 'Group name', (record or {}).get('title') or chosen[0]['title'], 'input')],
                                message='One agent and branch will receive:\n' + '\n'.join(t['key'] + ' · ' + t['title'] for t in chosen), submit='Save draft')
        if value is None:
            return None
        draft = copy.deepcopy(record) if record else await self.io(lambda s: ops.draft(s, chosen[0], persist=False))
        await self.io(lambda s: ops.set_group_issues(s, draft, chosen, value['title']))
        save_record(self.store, draft)
        if record is None:
            self.selected_handover = draft['id']
            await self.wizard(draft)
        return draft

    async def perform(self, action, payload=None):
        if action == "group-issues":
            return await self.group_issues()
        if action.startswith('task-'):
            return await productivity_ui.action(self, action, payload)
        if action in ("handover-choose-issue", "work-choose-issue"):
            self.show_tab("issues")
            self.query_one("#search", Input).focus()
            return
        if action == "work-browse-handovers":
            self.query_one("#history-filter", Select).value = "all"
            self.show_tab("handovers")
            self.query_one("#handovers-table", DataTable).focus()
            return
        if action == "handover-browse-drafts":
            self.query_one("#history-filter", Select).value = "draft"
            self.show_tab("handovers")
            self.query_one("#handovers-table", DataTable).focus()
            return
        if action == "show-attention":
            self.show_tab("attention")
            return
        if action in ('workflow-recipes', 'workflow-runs', 'work-bounded', 'work-bounded-runs'):
            from . import bounded_ui
            if action == 'workflow-recipes':
                return await bounded_ui.configure(self)
            if action == 'work-bounded':
                return await bounded_ui.start(self, self.record())
            return await bounded_ui.manage(self, self.record() if action == 'work-bounded-runs' else None)
        if action.startswith("attention-"):
            return await workflow_ui.attention_action(self, action)
        if action in ("work-search", "work-bundles", "work-integrations", "work-scheduling"):
            return await workflow_ui.global_action(self, action)
        if action == 'connection-health':
            from . import forge
            def inspect(store):
                issues = []
                for c in store.config()['connections']:
                    if c['provider'] == 'github':
                        try:
                            github_repositories(c)
                        except TaskError:
                            issues.append((c['id'] + ' · incomplete GitHub scope', 'connection:' + c['id']))
                for r in store.records('handovers'):
                    if not (r.get('target') or {}).get('path') or r.get('archived_at'):
                        continue
                    try:
                        if not forge.preferred(store, r):
                            issues.append((r['ticket']['key'] + ' · choose PR account/repository', 'record:' + r['id']))
                    except TaskError:
                        issues.append((r['ticket']['key'] + ' · checkout/origin unavailable', 'record:' + r['id']))
                return issues
            issues = await self.io(inspect)
            if not issues:
                await self.form('Connection health', message='No unresolved scopes or PR connection choices for current handovers.', submit='Close')
                return
            choice = await self.choice('Connection health · choose an item to repair', issues)
            if choice and choice.startswith('connection:'):
                config = self.store.config()
                await self.configure_github(config, next(c for c in config['connections'] if c['id'] == choice[11:]))
            elif choice:
                record = next(r for r in self.store.records('handovers') if r['id'] == choice[7:])
                if await workflow_ui.repair_connection(self, record, force=True):
                    await self.io(lambda s: workflow.refresh_pr(s, record))
            return
        if action == "repair-connection":
            invalid = []
            for c in self.store.config()["connections"]:
                if c["provider"] == "github":
                    try:
                        github_repositories(c)
                    except TaskError:
                        invalid.append(c)
            selected = invalid[0]["id"] if len(invalid) == 1 else await self.choice("Repair invalid connection", [(c["id"], c["id"]) for c in invalid])
            if selected:
                config = self.store.config()
                return await self.configure_github(config, next(c for c in config["connections"] if c["id"] == selected))
            return
        if action.startswith("work-"):
            if action == "work-menu":
                action = await self.choice("More handover actions", [("Start / Continue", "task-start"), ("Next phase", "task-next-phase"), ("Archive / Restore", "task-archive"), ("Copy", "copy-history"), ("Tracking / recovery actions", "history-actions")] + [(title, "work-" + ident) for title, ident in [
                    ("Artifacts / logs", "evidence"), ("Add artifact", "artifact"), ("Summary", "summary"), ("History", "timeline"),
                    ("PR & CI / refresh", "pr"), ("Select PR connection", "pr-link"), ("Create PR (reviewed)", "pr-create"),
                    ("Open PR in browser", "pr-browser"),
                    ("Address PR feedback workflow…", "bounded"), ("Workflow runs", "bounded-runs"), ("Address PR comments", "pr-comments"), ("Address failing checks / builds", "pr-failures"), ("Failing job output", "failing-logs"),
                    ("Send PR/CI feedback", "pr-feedback"), ("Job output", "logs"), ("Run validation", "validation"),
                    ("Independent read-only review", "review"), ("Prepare two approaches", "compare"), ("Compare results", "comparison"),
                    ("Path overlap / leases", "overlap"), ("Repository bundles", "bundles"), ("Find work", "search")]])
                if not action:
                    return
                if not action.startswith("work-"):
                    return await self.perform(action)
                if action in ("work-bundles", "work-search"):
                    return await workflow_ui.global_action(self, action)
            if action in ('work-bounded', 'work-bounded-runs'):
                return await self.perform(action)
            self.show_tab("work")
            return await workflow_ui.action(self, action, self.record())
        if action == "reset-filters":
            for ident in ("connection", "repository-filter", "view"):
                self.query_one("#" + ident, Select).value = "all"
            self.query_one("#search", Input).value = ""
            self.dashboard_scope = ""
            self.scope_ticket_keys = self.scope_history_ids = None
            return
        if action == "clear-checked":
            self.checked.clear()
            return
        if action in ("clear-scope", "history-clear-scope"):
            self.dashboard_scope = ""
            self.scope_ticket_keys = self.scope_history_ids = None
            return
        if action in ("initial", "refresh", "refresh-repository"):
            if action == "initial":
                self.tasks, _ = await self.io(lambda s: ops.refresh(s, False))
                self.paint()
                self.read_inbox()
                try:
                    await self.io(lambda s: ops.register_console(s, self.host))
                except TaskError:
                    pass
            scope = None
            if action == 'refresh-repository':
                ticket = self.issue()
                scope = (ticket['connection'], ticket['project'])
            context = self.action_context.get()
            if context is not None:
                context["refresh_scheduled"] = True
            self.refresh_issues(scope)
            self.update_host()
            return
        if action == "inbox":
            return await self.inbox_action(payload)
        if action in ("connections", "filters", "repositories", "prompts", "orch-settings", "doctor"):
            self.editing_configuration = True
            try:
                await self.configure(action)
            except (TaskError, ValueError, OSError, KeyError) as exc:
                await self.form("Configuration needs attention", message=str(exc), submit="Close")
            finally:
                self.editing_configuration = False
            return
        if action in ("copy", "copy-history"):
            return await self.copy_data()
        if action.startswith("orch-"):
            await self.orchestration(action)
            return
        if action == "lookup":
            values = await self.form("Open issue", [("lookup", "URL or issue ID", "", "input")])
            if values:
                candidates = lookup_candidates(self.store.config(), values["lookup"])
                index = await self.choice("Connection / issue", [(c[0]["id"] + " · " + c[1], str(i)) for i, c in enumerate(candidates)])
                if index is not None:
                    c, ident = candidates[int(index)]
                    ticket = await self.io(lambda s: provider(c).get(ident))
                    self.tasks = [t for t in self.tasks if ticket_key(t) != ticket_key(ticket)] + [ticket]
                    self.selected_issue = ticket_key(ticket)
            return
        if action in ("resume", "edit", "history-actions"):
            await self.history_action(action)
            return
        ticket = self.issue()
        recent = self.store.preference("recent", [])
        self.store.set_preference("recent", [ticket_key(ticket)] + [k for k in recent if k != ticket_key(ticket)][:49])
        if action == "actions":
            action = await self.choice("Issue actions · " + ticket["key"], [(x, x) for x in
                ["Handover", "New handover", "Add to Up next", "Batch edit selected drafts", "Change status", "Publish comment", "Open browser", "Copy data", "Copy link", "Select / deselect for batch", "Pin / unpin", "Previous handovers", "Prepare selected drafts"]])
        if action in ("handover", "Handover"):
            await productivity_ui.continue_task(self, ticket)
        elif action == "New handover":
            await productivity_ui.continue_task(self, ticket, new=True)
        elif action == "Add to Up next":
            await productivity_ui.action(self, "task-enqueue")
        elif action == "Batch edit selected drafts":
            await productivity_ui.action(self, 'task-batch')
        elif action in ("status-change", "Change status"):
            await self.change_status(ticket)
        elif action == "Publish comment":
            await self.comment(ticket)
        elif action in ("browser", "Open browser"):
            await asyncio.to_thread(webbrowser.open, ops.browser_url(ticket["url"]))
        elif action == "Copy link":
            self.copy_to_clipboard(ops.browser_url(ticket["url"]))
        elif action == "Copy data":
            await self.copy_data()
        elif action in ("toggle", "Select / deselect for batch"):
            self.toggle_checked()
        elif action == "Pin / unpin":
            values = self.store.preference("favorites", [])
            key = ticket_key(ticket)
            self.store.set_preference("favorites", [v for v in values if v != key] if key in values else values + [key])
        elif action == "Previous handovers":
            values = [r for r in self.store.records("handovers") if has_ticket(r, ticket)]
            selected = await self.choice("Previous handovers", [(r["created"] + " · " + r["stage"], r["id"]) for r in values])
            if selected:
                self.selected_handover = selected
                self.show_tab("handovers")
        elif action == "Prepare selected drafts":
            tickets = [t for t in self.tasks if ticket_key(t) in self.checked]
            if not tickets:
                raise TaskError("Click the checkbox column or use Select / deselect for batch in issue actions first.")
            if await self.confirm("Prepare drafts only", "\n".join(t["key"] + " · " + t["title"] for t in tickets) + "\nNo worker will start."):
                for t in tickets:
                    ops.draft(self.store, t)
                self.show_tab("handovers")

    async def inbox_action(self, message):
        action, key, context = message["action"], message.get("ticket"), message.get("context", {})
        if action == "open" and not key:
            self.show_tab("issues")
            self.call_after_refresh(self.query_one("#issues-table", DataTable).focus)
            return
        if action == 'orch-open':
            self.status('Manage task coordination in native ORCH.')
            self.show_tab('handovers')
            return
        if action == 'capture':
            return await productivity_ui.capture(self, context=context)
        if action in ("attention", "find-work"):
            return await self.perform("show-attention" if action == "attention" else "work-search")
        if key and key.startswith("dashboard:"):
            data = json.loads(key.removeprefix("dashboard:"))
            if data.get("config"):
                self.show_tab("configuration")
                return
            if data.get("section") == "attention-all":
                self.show_tab("attention")
                if data.get("attention"):
                    self.query_one("#attention-search", Input).value = ""
                    self.selected_attention = data["attention"]
                    self.paint_attention()
                return
            if data.get("copy"):
                tickets = self.tasks + [t for r in self.store.records("handovers") for t in handover_tickets(r)]
                ticket = next((t for t in tickets if ticket_key(t) == data["issue"]), None)
                if not ticket:
                    raise TaskError("Issue is no longer cached. Refresh tasks first.")
                self.copy_to_clipboard(clean(ticket["key" if data["copy"] == "id" else "url"]))
                return
            if data.get("record"):
                self.history = self.store.records("handovers")
                record = next((r for r in self.history if r["id"] == data["record"]), None)
                if record is None:
                    raise TaskError("This handover was removed. Choose another saved handover.")
                self.scope_history_ids = None
                self.query_one("#history-filter", Select).value = "all"
                self.selected_handover = data["record"]
                context = self.action_context.get()
                if context is not None:
                    context["record"] = copy.deepcopy(record)
                    context["issue"] = None
                    context["target"] = record["ticket"]["project"] + " · " + record["ticket"]["key"]
                if data.get("action") in ("work-linked-prs", "work-pr-details", "work-pr", "work-pr-link", "work-pr-comments", "work-pr-failures", "work-failing-logs", "work-pr-browser"):
                    self.show_tab("work")
                    if data["action"] != "work-pr-details":
                        await workflow_ui.action(self, data["action"], record)
                    self.paint_work()
                    return
                if str(data.get("action", "")).startswith("task-"):
                    return await productivity_ui.action(self, data["action"])
                if data.get("action") == "follow-up":
                    return await self.history_action("follow-up")
                if data.get("action") == "resume":
                    return await self.history_action("resume")
                if record["stage"] == "draft" and not record.get("approved"):
                    return await self.wizard(record)
                self.show_tab("handovers")
                self.paint()
                return
            self.dashboard_scope = data.get("repo", "")
            if self.dashboard_scope:
                def scoped(s):
                    config = s.config()
                    matched = {}
                    keys = set()
                    for ticket in self.tasks:
                        group = (ticket["connection"], ticket["project"])
                        if group not in matched:
                            matched[group] = ops.ticket_matches(config, ticket, self.dashboard_scope)
                        if matched[group]:
                            keys.add(ticket_key(ticket))
                    identity = ops.repository_identity(self.dashboard_scope)
                    records = {r["id"] for r in s.records("handovers") if identity and ops.repository_identity((r.get("target") or r.get("inputs") or {}).get("repo", "")) == identity}
                    return keys, records
                self.scope_ticket_keys, self.scope_history_ids = await self.io(scoped)
            else:
                self.scope_ticket_keys = self.scope_history_ids = None
            self.project_filters(data.get("project"))
            self.query_one("#repository-filter", Select).value = data.get("project", "all")
            self.query_one("#connection", Select).value = "all"
            self.query_one("#search", Input).value = ""
            section = data.get("section", "issues")
            self.query_one("#history-filter", Select).value = section if section in ("draft", "attention", "active") else "all"
            self.show_tab("issues" if section == "issues" else "handovers")
            if section == "issues":
                self.call_after_refresh(self.query_one("#issues-table", DataTable).focus)
            return
        if key:
            self.dashboard_scope = ""
            self.scope_ticket_keys = self.scope_history_ids = None
            self.tasks, _ = await self.io(lambda s: ops.refresh(s, False))
            self.selected_issue = key
            self.query_one("#connection", Select).value = "all"
            self.query_one("#view", Select).value = "all"
            self.query_one("#repository-filter", Select).value = "all"
            self.query_one("#search", Input).value = ""
            if not any(ticket_key(t) == key for t in self.tasks):
                cid, ident = key.split(":", 1)
                c = next(c for c in self.store.config()["connections"] if c["id"] == cid)
                self.tasks.append(await self.io(lambda s: provider(c).get(ident)))
            if action == "open":
                self.show_tab("issues")
                self.paint()
                self.call_after_refresh(self.query_one("#issues-table", DataTable).focus)
            else:
                await self.perform(action)
        elif action in ("linked", "follow-up"):
            pane = str(context.get("pane", {}).get("id", ""))
            self.history = self.store.records("handovers")
            records = [r for r in self.history if str(r.get("pane", "")) == pane]
            valid = []
            for r in records:
                if await self.io(lambda s, r=r: live_agent(self.host, r)):
                    valid.append(r)
            ident = await self.choice("Linked handovers", [(r["ticket"]["key"] + " · " + r["created"], r["id"]) for r in valid])
            if ident:
                self.selected_handover = ident
                self.show_tab("handovers")
                if action == "follow-up":
                    await self.history_action("follow-up")
        elif action == "copy-selection":
            text = clean(context.get("selection", ""))
            if not text:
                raise TaskError("Select text in the clicked pane first. You can also select text in an issue preview and press Ctrl+C.")
            self.copy_to_clipboard(text)
            self.status("Selected text copied")
        elif action == "selection":
            text = clean(context.get("selection", ""))
            if not text:
                raise TaskError("Select terminal text before invoking Add selection.")
            records = [r for r in self.store.records("handovers") if r["stage"] == "draft" and not r.get("approved")]
            ident = await self.choice("Add selection to draft", [(r["ticket"]["key"] + " · " + r["created"], r["id"]) for r in records])
            if ident and await self.confirm("Review selected text", text):
                record = next(r for r in records if r["id"] == ident)
                from .editor import HandoverEditor
                editors = self.query(HandoverEditor)
                editor = editors.first() if editors and editors.first().record["id"] == ident else None
                if editor:
                    record = editor.record
                record["context"].append({"label": "Pane selection", "mode": "text", "text": text,
                                          "source": context.get("pane", {}).get("cwd", "")})
                ops.update_prompt(self.store, record)
                save_record(self.store, record)
                if editor:
                    editor.paint_context()
                    editor.refresh_prompt()
                    editor.changed()
        elif action == "workspace-handover":
            await self.workspace_handover(context)
        elif context.get("workspace", {}).get("cwd"):
            cwd = context["workspace"]["cwd"]
            mapped = [c["id"] for c in self.store.config()["connections"] if cwd in c.get("repositories", {}).values()]
            if len(mapped) == 1:
                self.query_one("#connection", Select).value = mapped[0]

    async def workspace_handover(self, context):
        cwd = context.get("workspace", {}).get("cwd", "")
        if not cwd or not Path(cwd).is_absolute():
            raise TaskError("The clicked workspace path is missing. Right-click the intended workspace again.")
        repo = await self.io(lambda s: repository(cwd))
        branch = await self.io(lambda s: git(repo, "branch", "--show-current"))
        workspace_id = context.get("workspace", {}).get("id")
        if workspace_id:
            current = await self.io(lambda s: self.host.call("workspace.get", workspace_id=workspace_id))
            if await self.io(lambda s: repository(current["cwd"])) != repo:
                raise TaskError("The clicked workspace changed. Right-click the intended worktree again.")
        if not branch:
            raise TaskError("This worktree has a detached HEAD. Open Tasks and choose an explicit branch in the handover editor.")
        self.history = self.store.records("handovers")
        agents = await self.io(lambda s: self.host.agents())
        linked = [r for r in self.history if r.get("target", {}).get("path") and
                  Path(r["target"]["path"]).resolve() == Path(repo).resolve() and matching_agent(agents, r)]
        if linked:
            ident = linked[0]["id"] if len(linked) == 1 else await self.choice("Choose linked handover", [(r["ticket"]["key"] + " · " + r["name"], r["id"]) for r in linked])
            if ident is None:
                return
            self.selected_handover = ident
            self.scope_history_ids = None
            self.query_one("#history-filter", Select).value = "all"
            self.show_tab("handovers")
            action = await self.choice("Continue in " + branch, [("Prepare reviewed follow-up", "follow-up"), ("Open existing agent", "resume")])
            if action:
                await self.history_action(action)
            return
        saved = [r for r in self.history if (r.get("target", {}).get("path") or (not r.get("inputs", {}).get("new", True) and r.get("inputs", {}).get("repo"))) and
                 Path(r.get("target", {}).get("path") or r["inputs"]["repo"]).resolve() == Path(repo).resolve()]
        if saved:
            ident = await self.choice("Saved work in " + branch,
                [(r["ticket"]["project"] + " · " + r["ticket"]["key"] + " · " + r["stage"], r["id"]) for r in saved] + [("Prepare a new handover…", "new")])
            if ident is None:
                return
            if ident != "new":
                record = next(r for r in saved if r["id"] == ident)
                self.selected_handover = ident
                if record["stage"] == "draft" and not record.get("approved"):
                    await self.wizard(record)
                else:
                    self.scope_history_ids = None
                    self.query_one("#history-filter", Select).value = "all"
                    self.show_tab("work")
                    self.paint_work()
                return
        def in_checkout(agent):
            try:
                return bool(agent.get("cwd")) and repository(agent["cwd"]) == repo
            except TaskError:
                return False
        occupied = await self.io(lambda s: [a for a in agents if in_checkout(a)])
        if occupied and not await self.confirm("Unlinked agent already uses this checkout", "No instructions will be sent to it. Prepare a new isolated handover instead?"):
            return
        cached, _ = await self.io(lambda s: ops.refresh(s, False))
        tickets = {ticket_key(t): t for t in [*cached, *self.tasks, *(r["ticket"] for r in self.history)]}
        choices = await self.io(lambda s: [t for t in tickets.values() if ops.ticket_matches(s.config(), t, repo)])
        if not choices:
            raise TaskError("No cached issues match this checkout. In Tasks, configure its repository mapping and refresh the connection, then retry. Unrelated issues will not be assigned to this worktree.")
        key = await self.choice("Choose issue for " + (branch or "this workspace") + " · no agent starts yet", [(t["project"] + " · " + t["key"] + " · " + t["title"], ticket_key(t)) for t in choices])
        if key is None:
            return
        ticket = next(t for t in choices if ticket_key(t) == key)
        fresh = await self.io(lambda s: provider(ops.connection(s, ticket)).get(ticket["id"]))
        if ticket_key(fresh) != ticket_key(ticket) or fresh.get("project") != ticket.get("project"):
            raise TaskError("The provider returned a different issue. Refresh and choose the issue again.")
        if not await self.io(lambda s: ops.ticket_matches(s.config(), fresh, repo)):
            raise TaskError("The issue's repository mapping changed. Review it before preparing a handover.")
        if await self.io(lambda s: git(repo, "branch", "--show-current")) != branch:
            raise TaskError("The worktree branch changed while choosing the issue. Retry from this workspace.")
        record = await self.io(lambda s: ops.draft(s, fresh, repo))
        if branch and not occupied:
            record["inputs"].update(new=False, branch=branch, base=branch)
            record["manual_fields"] = ["repo", "branch", "new", "base"]
        record["repository_source"] = "Right-clicked workspace · " + (branch or "choose a valid branch / checkout")
        save_record(self.store, record)
        await self.wizard(record)

    async def workspace_path(self, current="", ticket=None):
        try:
            workspaces = (await self.io(lambda s: self.host.call("workspace.list")))["workspaces"]
        except (TaskError, KeyError):
            return current
        options = [("Keep / enter manual path" + (" · " + current if current else ""), current)]
        for workspace in workspaces:
            path = workspace.get("cwd", "")
            if path:
                try:
                    branch = await self.io(lambda s: git(path, "branch", "--show-current"))
                    remote = await self.io(lambda s: git(path, "remote", "get-url", "origin")) if ticket and ticket["provider"] == "GitHub" else ""
                except TaskError:
                    branch, remote = "not a Git checkout", ""
                matches = ticket and ticket["provider"] == "GitHub" and any(remote.lower().removesuffix(".git").rstrip("/").endswith(p + ticket["project"].lower()) for p in ("github.com/", "github.com:"))
                label = workspace.get("name", "Workspace") + " · " + branch + " · " + path + (" (matches issue)" if matches else "")
                options.append((label, path))
        return await self.choice("Local checkout · choose a Luvus workspace or override manually", options)

    def active_editor(self):
        return next((e for e in self.editors.values() if e.display), None)

    async def wizard(self, record):
        if (self.action_context.get() or {}).get("cancelled"):
            return
        if record.get("approved") or record.get("pane"):
            raise TaskError("Approved handovers are immutable. Use follow-up, retry, or a new draft.")
        for editor in self.editors.values():
            editor.flush()
            editor.display = False
        container = self.query_one("#handover-container", Vertical)
        if not self.editors:
            await container.remove_children()
        editor = self.editors.get(record["id"])
        if editor is None:
            record.setdefault("inputs", {k: (record.get("target") or {}).get(k, "") for k in ("repo", "branch", "base", "new")})
            record["inputs"].setdefault("worktree_parent", "")
            available = {"Saved agent · checking availability": record["agent"]} if record.get("agent") else {}
            editor = HandoverEditor(record, [], available)
            self.editors[record["id"]] = editor
            await container.mount(editor)
            self.discover_editor(editor)
        editor.display = True
        self.show_tab("handover")
        self.status("Draft · " + record["ticket"]["key"])

    @work(group="editor-discovery", exit_on_error=False)
    async def discover_editor(self, editor):
        record = copy.deepcopy(editor.record)
        workspaces = []
        try:
            available = await self.io(lambda s: self.host.available_agents())
        except TaskError:
            available = {}
        try:
            raw = (await self.io(lambda s: self.host.call("workspace.list")))["workspaces"]
        except TaskError:
            raw = []
        for w in raw:
            try:
                path = await self.io(lambda s, w=w: repository(w["cwd"]))
                await self.io(lambda s: validate_issue_repository(record["ticket"], path))
                branch = await self.io(lambda s: git(path, "branch", "--show-current"))
            except (TaskError, KeyError):
                continue
            if not any(x["cwd"] == path for x in workspaces):
                workspaces.append({"cwd": path, "label": w.get("name", "Workspace") + " · " + (branch or "detached HEAD") + " · " + path})
        if not editor.is_mounted:
            return
        editor.workspaces = workspaces
        editor.agents = available
        choices = list(available.items())
        saved = editor.record.get("agent")
        if saved and saved not in available.values():
            choices.append((saved + " · unavailable", saved))
        editor.synchronizing = True
        try:
            editor.query_one("#workspace-choice", Select).set_options(editor.workspace_options())
            editor.query_one("#target-agent", Select).set_options(choices)
            editor.query_one("#target-agent", Select).value = saved or Select.NULL
        finally:
            editor.synchronizing = False
        if not record["inputs"].get("repo") and not editor.record["inputs"].get("repo") and "repo" not in editor.record.get("manual_fields", []):
            matches = []
            for workspace in workspaces:
                if await self.io(lambda s, w=workspace: ops.ticket_matches(s.config(), record["ticket"], w["cwd"])):
                    matches.append(workspace)
            if len(matches) == 1 and editor.is_mounted and not editor.record["inputs"].get("repo") and "repo" not in editor.record.get("manual_fields", []):
                path = matches[0]["cwd"]
                default = await self.io(lambda s: default_base(path, s.config().get("repositories", {}).get(path, {}).get("base", "")))
                if not editor.is_mounted or editor.record["inputs"].get("repo") or "repo" in editor.record.get("manual_fields", []):
                    return
                editor.record["inputs"]["repo"] = path
                editor.query_one("#target-repo", Input).value = path
                editor.apply_defaults()
                if "base" not in editor.record.get("manual_fields", []):
                    editor.record["inputs"]["base"] = default
                    editor.query_one("#target-base", Input).value = default
                editor.record["repository_source"] = "Suggested: unique matching workspace (review or override)"
                editor.refresh_prompt()
                editor.changed()
                editor.load_branches()
        editor.query_one("#repository-source", Static).update(
            editor.record.get("repository_source", "Saved draft") +
            (" · choose a matching workspace below" if workspaces else " · no matching workspace available; enter a checkout path"))

    async def action_quit(self):
        if any(e.operation_busy for e in self.editors.values()):
            self.status("An operation is still finishing; navigation is available. Close after it finishes.")
            return
        failed = [e for e in self.editors.values() if not e.flush()]
        if failed or self.form_buffers:
            def decision(value):
                if value is not None:
                    self.exit()
            pending = [e.record["ticket"]["key"] for e in failed] + [key[0] for key in self.form_buffers]
            self.push_screen(Form("Unsaved drafts", message="Unsaved content: " + ", ".join(pending) +
                ". Cancel to keep editing or retry Save. Discard exits and loses these unsaved edits.", submit="Discard and exit"), decision)
            return
        await super().action_quit()

    async def review_launch(self, record):
        context = self.action_context.get()
        if record.get("approved"):
            raise TaskError("Already approved. Inspect the saved launch in Handovers; do not launch again.")
        config = self.store.config()
        result = await self.io(lambda s: ops.preflight(s, self.host, record))
        record = result["record"]
        plan = record["target"]
        from .checkout import snapshot, badge, identity
        observation = await self.io(lambda s: snapshot(plan["path"]))
        if observation.get("state") == "observed":
            record["checkout_identity"] = identity(observation)
        default_branch = await self.io(lambda s: default_base(plan["repo"]))
        default_branch = default_branch.removeprefix("origin/")
        on_default = not plan.get("new", True) and plan["branch"] in {default_branch, "main", "master"}
        fields = [("dirty", "I acknowledge existing checkout changes", False, "bool")] if result["dirty"] else []
        if on_default:
            fields.append(("default_branch", "I explicitly approve working on the default branch instead of an isolated feature branch", False, "bool"))
        if result["images"]:
            fields.append(("images", "I accept image paths only; native image delivery is not guaranteed", False, "bool"))
        fields.append(("remember", "Remember this repository mapping", False, "bool"))
        fields.append(("edit-details", "Edit details instead of launching", False, "bool"))
        setup_options = []
        setup_note = ""
        if config.get("integrations", {}).get("project-commands"):
            try:
                setup_options = [x for x in await self.io(lambda s: workflow.commands(s, record)) if x["kind"] == "setup"]
                if not setup_options:
                    setup_note = "\nNo setup command configured for this exact checkout in Project Commands."
            except TaskError as exc:
                setup_note = "\nSetup integration unavailable: " + str(exc)
        if setup_options:
            fields.append(("setup", "Optional setup before agent starts", "", [("No setup", "")] + [(x.get("title", x["id"]), x["id"]) for x in setup_options]))
        if config.get("orchestration"):
            fields += [("orch-paths", "ORCH path scopes (optional, one per line)", "\n".join(record.get("orch_paths", [])), "text"),
                       ("orch-deps", "ORCH dependency task IDs (optional, one per line)", "\n".join(record.get("orch_deps", [])), "text")]
            setup_note += "\nORCH: create/reuse and claim for this worker before delivery. Scope is undeclared unless specified above."
        value = await self.form("Review & launch handover", fields,
            message=f"{record['ticket']['url']}\nAgent: {record['agent']}\nRepository: {plan['repo']}\nTarget: {plan['path']}\n"
                    f"Branch: {plan['branch']}\nCheckout: {badge(observation)}\nBase commit: {plan['commit']}\nContext: {len(record['context'])} items\n\nExisting changes:\n{result['dirty'] or 'None'}{setup_note}", exact_prompt=record["prompt"], submit="Launch")
        if value is None:
            return
        if value.get("edit-details"):
            await self.wizard(record)
            return
        if result["dirty"] and not value["dirty"] or result["images"] and not value["images"] or on_default and not value["default_branch"]:
            raise TaskError("Required acknowledgement was not selected. Draft preserved.")
        record["approved"] = True
        record["orch_enabled"] = bool(config.get("orchestration"))
        record["orch_paths"] = [x.strip() for x in value.get("orch-paths", "").splitlines() if x.strip()]
        record["orch_deps"] = [x.strip() for x in value.get("orch-deps", "").splitlines() if x.strip()]
        record.pop("setup", None)
        if value.get("setup"):
            record["setup"] = next(x for x in setup_options if x["id"] == value["setup"])
        save_record(self.store, record)
        def progress():
            current = next((r for r in self.store.records("handovers") if r["id"] == record["id"]), record)
            self.status("Launch: " + current["stage"] + " · " + current.get("error", ""))
        timer = self.set_interval(0.25, progress)
        try:
            await self.io(lambda s: ops.preflight(s, self.host, record))
            record = await self.io(lambda s: launch(s, self.host, record, dirty_ok=bool(result["dirty"])))
        except TaskError as exc:
            current = next(r for r in self.store.records("handovers") if r["id"] == record["id"])
            current["error"] = str(exc)
            save_record(self.store, current)
            record.update(current)
            raise TaskError(f"Launch stopped at {current['stage']}: {exc}. Inspect Handovers for retry / reconciliation; nothing is resent automatically.") from exc
        finally:
            timer.stop()
        if self.store.preference("orch-display-error"):
            self.status("Delivered; ORCH display needs attention: " + self.store.preference("orch-display-error"))
        if value["remember"]:
            config = self.store.config()
            c = next(c for c in config["connections"] if c["id"] == record["ticket"]["connection"])
            c.setdefault("repositories", {})[record["ticket"]["project"]] = plan["repo"]
            self.store.save_config(config)
        if not context or not context.get("editor") or (self.active_editor() is context["editor"] and self.query_one("#tabs", TabbedContent).active == "handover"):
            self.selected_handover = record["id"]
            self.show_tab("handovers")


    async def change_status(self, ticket):
        def fetch(s):
            p = provider(ops.connection(s, ticket))
            fresh = p.get(ticket["id"])
            return fresh, p.actions(fresh)
        fresh, actions = await self.io(fetch)
        selected = await self.choice("Available status actions", [(a["name"], str(i)) for i, a in enumerate(actions)])
        if selected is None:
            return
        action = actions[int(selected)]
        fields = {}
        for key, info in action.get("fields", {}).items():
            if not info.get("required"):
                continue
            choices = info.get("allowedValues", [])
            kind = info.get("schema", {}).get("type")
            if choices:
                index = await self.choice(info.get("name", key), [(str(x.get("name", x.get("value", x.get("id")))) if isinstance(x, dict) else str(x), str(i)) for i, x in enumerate(choices)])
                if index is None:
                    return
                item = choices[int(index)]
                fields[key] = {"id": item["id"]} if isinstance(item, dict) and "id" in item else item
                if kind == "array":
                    fields[key] = [fields[key]]
            elif kind in ("string", "number", "integer", "boolean"):
                value = await self.form("Required transition field", [("value", info.get("name", key), False if kind == "boolean" else "", "bool" if kind == "boolean" else "input")])
                if value is None:
                    return
                raw = value["value"]
                if kind != "boolean" and not str(raw).strip():
                    raise TaskError("Required field is empty.")
                fields[key] = float(raw) if kind == "number" else int(raw) if kind == "integer" else raw
            else:
                raise TaskError(f"Required field {info.get('name', key)} needs unsupported input. Complete this transition in the tracker.")
        if await self.confirm("Confirm status change", f"{fresh['url']}\nCurrent: {fresh['status']}\nAction: {action['name']}\n{json.dumps(fields, ensure_ascii=False)}"):
            updated = await self.io(lambda s: ops.transition(s, fresh, action, fields))
            self.tasks = [updated if ticket_key(t) == ticket_key(updated) else t for t in self.tasks]

    async def comment(self, ticket):
        value = await self.form("Prepare tracker comment", [("text", "Exact comment", "", "text"), ("link", "Optional HTTPS branch / PR link", "", "input")])
        if value is None:
            return
        text = value["text"]
        if value["link"]:
            text += "\n" + ops.browser_url(value["link"])
        if await self.confirm("Publish comment", ticket["url"] + "\n\n" + text + "\n\nA duplicate-prevention marker will be appended."):
            await self.io(lambda s: ops.publish_comment(s, ticket, text))

    async def history_action(self, action):
        record = self.record()
        if action == "history-actions":
            options = ["Open / Resume", "Edit draft", "Open native DIFF", "Review completion", "Reviewed follow-up", "Follow-up from DIFF notes", "Publish comment", "Change status", "Retry launch", "Reconcile uncertain launch", "Remove history"]
            if self.store.config().get("orchestration"):
                options += ["Link ORCH task", "Claim linked ORCH task"]
            action = await self.choice("Handover actions", [(x, x) for x in options])
        if action in ("resume", "Open / Resume"):
            await self.io(lambda s: resume(s, self.host, record))
        elif action in ("edit", "Edit draft"):
            await self.wizard(record)
        elif action == "Open native DIFF":
            snapshot = await self.io(lambda s: ops.review_diff(self.host, record))
            files = snapshot["files"]
            selected = await self.choice("Changed files in reviewed checkout", [(f["layer"] + " · " + f["path"], str(i)) for i, f in enumerate(files)])
            if selected is not None:
                await self.io(lambda s: ops.open_review_diff(self.host, record, files[int(selected)]))
        elif action == "Follow-up from DIFF notes":
            await self.io(lambda s: ops.review_diff(self.host, record))
            notes = await self.io(lambda s: self.host.call("diff.note.list", state="open")["notes"])
            index = await self.choice("Select review feedback", [(n.get("body", "Note")[:100], str(i)) for i, n in enumerate(notes)])
            if index is not None:
                value = await self.form("Review feedback before sending", [("text", "Follow-up instructions", json.dumps(notes[int(index)], ensure_ascii=False, indent=2), "text")])
                if value and await self.confirm("Send selected feedback", record["name"] + "\n\n" + value["text"]):
                    await self.io(lambda s: ops.followup(s, self.host, record, value["text"]))
        elif action == "Publish comment":
            await self.comment(record["ticket"])
        elif action == "Change status":
            await self.change_status(record["ticket"])
        elif action in ("follow-up", "Reviewed follow-up"):
            value = await self.form("Follow-up to " + record["name"], [("text", "Instructions / selected review feedback", "", "text")], message=record["ticket"]["url"])
            if value and value["text"].strip() and await self.confirm("Send follow-up", record["name"] + "\n\n" + value["text"]):
                await self.io(lambda s: ops.followup(s, self.host, record, value["text"]))
        elif action == "Review completion":
            path = record["target"]["path"]
            report = await self.io(lambda s: "Working tree:\n" + git(path, "status", "--short") + "\nChanges since base:\n" + git(path, "diff", "--no-ext-diff", "--no-textconv", "--stat", record["target"]["commit"]))
            evidence = await self.io(lambda s: [{"title": e["title"], "state": e["state"], "run": e.get("run_id"),
                "producer_freshness": e.get("producer", {}).get("freshness", "unknown"), "checkout_freshness": workflow.freshness(e, path)}
                for e in s.evidence(record["id"]) if e["kind"] in ("validation", "setup")])
            report += "\nExecuted command evidence (producer result; not agent claims):\n" + json.dumps(evidence, indent=2)
            agent = await self.io(lambda s: live_agent(self.host, record))
            if agent:
                output = await self.io(lambda s: self.host.call("agent.read", target=agent["pane"], lines=120))
                report += "\nAgent output (reported, not independently verified):\n" + json.dumps(output, ensure_ascii=False)
            value = await self.form("Completion checklist", [("changes", "I reviewed the changes", False, "bool"),
                ("criteria", "I checked acceptance criteria", False, "bool"), ("validation", "Verification evidence / remaining concerns", record.get("verification", ""), "text")],
                message=record["ticket"].get("acceptance", "No acceptance criteria supplied") + "\n\nOriginal prompt/context:\n" + record.get("prompt", "") + "\n\n" + report + "\nOpening this checklist does not execute validation. Free-text verification below is user-reported.", submit="Save review")
            if value:
                record["verification"] = value["validation"]
                record["review"] = {**value, "at": now()}
                save_record(self.store, record)
                checkout = await self.io(lambda s: workflow.fingerprint(record["target"]["path"]))
                self.store.observe("completion:" + record["id"], record["id"], {"kind": "completion", "title": "Completion review",
                    "state": "reviewed" if value["changes"] and value["criteria"] else "incomplete", "checkout": checkout,
                    "reported_evidence": value["validation"]})
                if value["changes"] and value["criteria"]:
                    if record.get("orch_enabled") and record.get("orch_id"):
                        from .checkout import guard
                        await self.io(lambda s: guard(record))
                        fresh_task = await self.io(lambda s: self.host.call("task.get", id=record["orch_id"])["task"])
                        await self.io(lambda s: ops.orch_action(s, self.host, record, fresh_task, "done"))
                    for item in self.attention_items:
                        if item["handover"] == record["id"] and item["source"] == "agent-done":
                            attention.acknowledge(self.store, item)
        elif action == "Retry launch":
            if not record.get("approved"):
                raise TaskError("Edit and review this draft before launch.")
            result = await self.io(lambda s: ops.preflight(s, self.host, record))
            if await self.confirm("Retry reviewed launch", json.dumps(record["target"], indent=2) + "\nExisting changes (confirm to acknowledge):\n" + result["dirty"]):
                record = await self.io(lambda s: launch(s, self.host, record, dirty_ok=bool(result["dirty"])))
                record.pop("error", None)
                save_record(self.store, record)
        elif action == "Reconcile uncertain launch":
            stage = record["stage"]
            options = {"prompt_pending": [("I inspected the agent: prompt WAS delivered", "delivered"), ("I inspected the agent: prompt was NOT delivered", "agent_ready")],
                       "terminal_pending": [("I verified no terminal was created", "draft")],
                       "agent_pending": [("I verified no agent process started", "terminal_ready")]}.get(stage, [])
            state = await self.choice("Inspect Luvus first · " + record["name"], options)
            if state and await self.confirm("Confirm manual reconciliation", f"Record {record['id']}\n{stage} → {state}\nThis does not send input or stop processes."):
                record["stage"] = state
                record.pop("error", None)
                save_record(self.store, record)
        elif action == "Remove history":
            if await self.confirm("Permanently remove local history", record["id"] + "\nDeletes this saved prompt and attachment copies. Cannot be undone. Original files, workers, ORCH tasks, and worktrees remain."):
                editor = self.editors.get(record["id"])
                if editor and editor.operation_busy:
                    raise TaskError("This draft still has an operation running. Retry removal when it finishes.")
                self.store.delete_handover(record)
                if editor:
                    self.editors.pop(record["id"])
                    await editor.remove()
                    self.save_warning()
        elif action == "Link ORCH task":
            state = await self.io(lambda s: ops.orch_snapshot(self.host))
            selected = await self.choice("Link or create ORCH task", [("Create tracking task", "new")] + [(t["id"] + " · " + t["title"], t["id"]) for t in state["tasks"]])
            if selected:
                paths, deps = [], []
                if selected == "new":
                    value = await self.form("ORCH coordination scope", [("paths", "Path scopes (one per line; optional)", "", "text"),
                        ("deps", "Dependency task IDs (one per line; optional)", "", "text")], message="No worker starts and no gate is added. Native dependencies retain native completion rules.")
                    if value is None:
                        return
                    paths, deps = value["paths"].splitlines(), value["deps"].splitlines()
                if await self.confirm("Confirm ORCH link", record["ticket"]["key"] + " → " + selected + "\nTracking only; no native branch/merge management."):
                    await self.io(lambda s: ops.link_orch(s, self.host, record, "" if selected == "new" else selected, paths, deps))
        elif action == "Claim linked ORCH task":
            if not record.get("orch_id"):
                raise TaskError("Link an ORCH task first.")
            state = await self.io(lambda s: ops.orch_snapshot(self.host))
            task = next((t for t in state["tasks"] if t["id"] == record["orch_id"]), None)
            if await self.confirm("Claim task and acquire declared path leases", json.dumps({"task": task, "leases": state["leases"], "worker": record["name"]}, indent=2) + "\nA lease conflict leaves the task claimed; it does not stop the worker. Release is a separate action."):
                await self.io(lambda s: ops.claim_orch(s, self.host, record))

    async def configure(self, action, connection_id=None):
        config = self.store.config()
        if action == "doctor":
            result = await self.io(lambda s: {"capabilities": self.host.capabilities()["protocol_version"] if "protocol_version" in self.host.capabilities() else "OK", "agents": self.host.available_agents()})
            await self.form("Luvus check", message=json.dumps(result, indent=2), submit="Close")
            return
        if action == "orch-settings":
            value = await self.form("Optional ORCH integration", [("enabled", "Automatically coordinate approved launches and reviews with ORCH", config.get("orchestration", False), "bool")], submit="Save")
            if value:
                config["orchestration"] = value["enabled"]
                config.pop("orch_auto_show", None)
        elif action == "prompts":
            selected = await self.choice("Prompt configuration", [("Global instructions", "global"), ("Clear global instructions", "clear"), ("Restore default instructions", "restore"), ("Delete a preset", "delete")] + [(x, "preset:" + x) for x in config["presets"]] + [("New custom preset", "new")])
            if selected is None:
                return
            if selected in ("clear", "restore"):
                if await self.confirm("Confirm instructions change", "Existing handover prompts are unchanged."):
                    config["instructions"] = "" if selected == "clear" else DEFAULT_INSTRUCTIONS
                    self.store.save_config(config)
                return
            if selected == "delete":
                name = await self.choice("Delete preset", [(x, x) for x in config["presets"]])
                if name is None:
                    return
                defaults = [config.setdefault("branch_defaults", {})] + list(config["repositories"].values())
                refs = [d for d in defaults if d.get("preset", "Implement") == name]
                replacement = await self.choice("Replacement for defaults", [(x, x) for x in config["presets"] if x != name]) if refs else None
                if refs and replacement is None:
                    return
                if await self.confirm("Delete " + name, "Remove this preset only. Saved handover prompts and attachments are preserved."):
                    for d in refs:
                        d["preset"] = replacement
                    del config["presets"][name]
                    self.store.save_config(config)
                return
            is_global, is_new = selected == "global", selected == "new"
            selected = selected.removeprefix("preset:")
            fields = [("text", "Handover instructions", config["instructions"] if is_global else config["presets"].get(selected, ""), "text")]
            if is_new:
                fields.insert(0, ("name", "Preset name", "Custom", "input"))
            value = await self.form("Prompt configuration", fields, submit="Save")
            if value:
                if is_global:
                    config["instructions"] = value["text"]
                else:
                    config["presets"][value.get("name", selected)] = value["text"]
        elif action == "connections":
            selected = connection_id or await self.choice("Connection", [("Add connection", "new")] + [(c["id"], c["id"]) for c in config["connections"]])
            if selected is None:
                return
            old = next((c for c in config["connections"] if c["id"] == selected), {})
            if old:
                operation = await self.choice("Connection · " + selected, [("Edit / repair", "edit"), ("Remove connection", "remove")])
                if operation is None:
                    return
                if operation == "remove":
                    value = await self.form("Remove connection · " + selected,
                        [("credential", "Also delete its OS keyring credential (environment variables are untouched)", False, "bool")],
                        message="This stops future refresh and clears this connection's issue cache. History, worktrees, prompts and attachments remain.", submit="Remove connection")
                    if value is not None:
                        if value["credential"] and old["provider"] != "github":
                            await self.io(lambda s: delete_token(old))
                        config["connections"] = [c for c in config["connections"] if c["id"] != selected]
                        self.store.save_config(config)
                        with self.store.db:
                            self.store.db.execute("DELETE FROM cache WHERE substr(key,1,?)=?", (len(selected) + 1, selected + ":"))
                        self.tasks = [t for t in self.tasks if t["connection"] != selected]
                        self.load_filters()
                        self.paint()
                    return
            kind = old.get("provider") or await self.choice("Provider", [("Jira", "jira"), ("Azure DevOps (REST/PAT)", "azure"), ("GitHub (gh)", "github")])
            if not kind:
                return
            if kind == "github":
                await self.configure_github(config, old)
                return
            fields = [("ident", "Connection name (letters, digits, underscores)", old.get("id", ""), "input")]
            keys = {"jira": [("url", "Jira HTTPS site URL"), ("email", "Atlassian email"), ("cloud_id", "Cloud ID (scoped token only)"), ("acceptance_field", "Acceptance custom field (optional)")],
                    "azure": [("organization", "Azure organization name"), ("project", "Project"), ("acceptance_field", "Acceptance field (optional)")],
                    "github": [("repository", "GitHub owner/repository")]}[kind]
            fields += [(k, label, old.get(k, ""), "input") for k, label in keys]
            if kind != "github":
                fields.append(("token", "New token (blank keeps keyring / environment credential)", "", "secret"))
            value = await self.form("Connection settings", fields, message="GitHub uses gh auth login. Tracker tokens are saved only in the OS credential store.", submit="Review")
            if value is None:
                return
            import re
            ident = value.pop("ident")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", ident) or old and ident != old["id"]:
                raise TaskError("Use a valid name; existing connection IDs cannot be renamed.")
            if not old and any(c["id"] == ident for c in config["connections"]):
                raise TaskError("Connection name already exists. Edit it instead.")
            token = value.pop("token", "")
            c = {**old, **value, "id": ident, "provider": kind, "filters": old.get("filters", default_filters(kind))}
            if kind != "github":
                c["token_env"] = "LUVUS_TASKS_TOKEN_" + ident.upper()
            if not all(c.get(k) for k in {"jira": ["url", "email"], "azure": ["organization", "project"], "github": ["repository"]}[kind]):
                raise TaskError("Required connection fields are missing.")
            if not await self.confirm("Save connection", json.dumps(c, indent=2) + ("\nNew token will be saved to keyring." if token else "")):
                return
            if token:
                await self.io(lambda s: save_token(c, token))
            config["connections"] = [x for x in config["connections"] if x["id"] != ident] + [c]
            self.store.save_config(config)
            if await self.confirm("Test connection now?", "Read the configured Assigned to me query; no tracker writes."):
                tasks = await self.io(lambda s: provider(c).query(""))
                await self.form("Connection works", message=f"Read {len(tasks)} issues.", submit="Close")
        elif action == "filters":
            ident = await self.choice("Connection", [(c["id"], c["id"]) for c in config["connections"]])
            if ident is None:
                return
            c = next(c for c in config["connections"] if c["id"] == ident)
            selected = await self.choice("Saved filter", [("Add filter", "new")] + [(f["name"], str(i)) for i, f in enumerate(c.get("filters", []))])
            if selected is None:
                return
            old = {} if selected == "new" else c["filters"][int(selected)]
            value = await self.form("Saved filter", [("name", "Name", old.get("name", ""), "input"),
                ("query", {"azure": "Flat WIQL", "jira": "JQL", "github": "GitHub search qualifiers"}[c["provider"]], old.get("query", ""), "text"),
                ("remove", "Remove this filter", False, "bool")], message=("Empty query shows all open issues in selected repositories. Use is:closed for closed issues or is:open assignee:@me for your open work." if c["provider"] == "github" else "Empty query uses Assigned to me."), submit="Save")
            if value:
                if selected != "new":
                    c["filters"].pop(int(selected))
                if not value["remove"]:
                    if not value["name"].strip():
                        raise TaskError("Filter name is required.")
                    c.setdefault("filters", []).append({k: value[k] for k in ("name", "query")})
        elif action == "repositories":
            scope = await self.choice("Branch defaults", [("Global branch defaults", "global"), ("Local repository override / mapping", "repo")])
            if scope is None:
                return
            if scope == "global":
                old = config.get("branch_defaults", {})
                value = await self.form("Global branch defaults", [
                    ("branch_pattern", "Pattern: {type}, {key}, {number}, {repo}, {slug}", old.get("branch_pattern", "feature/{number}-{slug}"), "input"),
                    ("base", "Preferred base branch (blank uses remote default)", old.get("base", ""), "input"),
                    ("worktree_parent", "Worktree parent folder (blank uses module storage)", old.get("worktree_parent", ""), "input")], submit="Save")
                if value:
                    branch_name(value["branch_pattern"], {"key": "TEST-1", "title": "Example"})
                    config["branch_defaults"] = {**old, **value}
                    self.store.save_config(config)
                return
            path = await self.workspace_path("")
            if path is None:
                return
            value = await self.form("Repository defaults", [("repo", "Local repository path", path, "input")])
            if value is None:
                return
            repo = await self.io(lambda s: repository(value["repo"]))
            old = config["repositories"].get(repo, {})
            value = await self.form("Repository defaults · " + repo, [
                ("agent", "Default agent", old.get("agent", "codex"), [(k, v) for k, v in AGENTS.items()]),
                ("base", "Base branch", old.get("base", ""), "input"),
                ("worktree_parent", "Worktree parent folder (blank uses module storage)", old.get("worktree_parent", ""), "input"),
                ("branch_pattern", "Branch pattern: {type}, {key}, {number}, {repo}, {slug}", old.get("branch_pattern", config.get("branch_defaults", {}).get("branch_pattern", "feature/{number}-{slug}")), "input"),
                ("preset", "Default preset", old.get("preset", "Implement"), list(config["presets"])),
                ("override", "Override global instructions", "instructions" in old, "bool"),
                ("instructions", "Repository instructions", old.get("instructions", config["instructions"]), "text"),
                ("validation", "Validation instructions (not automatically executed)", old.get("validation", ""), "text"),
                ("remove", "Remove repository override and inherit global defaults", False, "bool")], submit="Save")
            if value:
                if value.pop("remove"):
                    config["repositories"].pop(repo, None)
                    self.store.save_config(config)
                    return
                branch_name(value["branch_pattern"], {"key": "TEST-1", "title": "Example"})
                if not value.pop("override"):
                    value.pop("instructions")
                config["repositories"][repo] = {**old, **value}
                suggestions = await self.io(lambda s: ops.suggested_connections(config, repo))
                selected = await self.choice("Map repository to connection", [("Keep existing mappings", "skip")] +
                    [(c["id"] + (" (matches origin)" if c["id"] in suggestions else ""), c["id"]) for c in config["connections"]])
                if selected and selected != "skip":
                    c = next(c for c in config["connections"] if c["id"] == selected)
                    project = c.get("project", c.get("repository", ""))
                    mapping = await self.form("Confirm repository mapping", [("project", "Tracker project key/name (GitHub: owner/repository)", project, "input")], message=repo + " → " + selected, submit="Save mapping")
                    if mapping and mapping["project"].strip():
                        c.setdefault("repositories", {})[mapping["project"]] = repo
        self.store.save_config(config)
        self.load_filters()

    async def configure_github(self, config, old):
        # Discovery needs gh, but deliberately does not use a possibly broken saved scope.
        client = GitHub({"id": old.get("id", "github"), "repository": "discovery/only"})
        login, accounts = await self.io(lambda s: client.accounts())
        saved_owner = old.get("repository", "") if not old.get("selected_repositories") and "/" not in old.get("repository", "") else ""
        owner = next((x for x in accounts if x.lower() == saved_owner.lower()), None)
        if owner is None:
            owner = await self.choice("GitHub · already signed in as " + login, [(x, x) for x in accounts])
        if owner is None:
            return
        selected = old.get("selected_repositories", [old.get("repository", "")])
        while True:
            repos = await self.io(lambda s: client.repositories(owner, login))
            result = await self.inline(RepositorySelection(repos, [r for r in selected if r in repos]))
            if result is None:
                return
            action, selected = result
            if action != "refresh-repos":
                break
        if not selected:
            raise TaskError("Select at least one repository. The connection was not changed.")
        value = await self.form("Save GitHub connection", [("ident", "Connection name", old.get("id", "github"), "input")],
            message=f"Using local gh authentication: {login}\nOwner: {owner}\n" + "\n".join(selected), submit="Save")
        if value is None:
            return
        import re
        ident = value["ident"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", ident) or old and ident != old["id"]:
            raise TaskError("Use a valid name; existing connection IDs cannot be renamed.")
        if not old and any(c["id"] == ident for c in config["connections"]):
            raise TaskError("Connection name already exists.")
        primary = old.get("repository") or selected[0]
        try:
            github_repositories({"repository": primary})
        except TaskError:
            prior = self.store.github_legacy_repositories(ident)
            choices = prior or selected
            primary = choices[0] if len(choices) == 1 else await self.choice("Repair original repository identity · preserves saved issue IDs", [(r, r) for r in choices])
            if primary is None:
                return
            if len(prior) > 1:
                raise TaskError("Legacy numeric IDs refer to multiple repositories. Repair the ambiguous stored history before changing this connection.")
        c = {**old, "id": ident, "provider": "github", "owner": owner, "account": login,
             "repository": primary, "selected_repositories": selected,
             "filters": old.get("filters", default_filters("github"))}
        github_repositories(c)
        config["connections"] = [x for x in config["connections"] if x["id"] != ident] + [c]
        self.store.save_config(config)
        self.load_filters()
        context = self.action_context.get()
        if context is not None:
            context["refresh_scheduled"] = True
        self.refresh_issues(message="Selection saved · refreshing issues…")

    async def orchestration(self, action):
        self.status("Manage task coordination in native ORCH.")
