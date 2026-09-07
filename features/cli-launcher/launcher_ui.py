"""Keyboard and mouse dashboard over the launcher's existing operations."""
import asyncio
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import uuid

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, Select, Static, Tab, Tabs, TextArea

import launcher
import project_launcher as backend
import agent_launch


SECTIONS = {"tools": "Tools", "links": "Links", "arrangements": "Layouts", "bundles": "Bundles", "settings": "Settings"}
HINTS = {
    "tools": "Launch any saved tool here or on a selected branch/worktree. More… also offers an immediate launch.",
    "links": "Keep repository, documentation, design and app links together for this project.",
    "arrangements": "Save a set of tools and their pane layout. Reopening reuses your existing session.",
    "bundles": "Open related repositories from Tasks. Branches stay as they are.",
    "settings": "Optional integrations and settings for this checkout.",
}
EMPTY_HINTS = {
    "tools": "No saved tools yet. Choose Add to save a command such as codex or claude.",
    "links": "No project links yet. Choose Add to save a website or local document.",
    "arrangements": "No saved layouts yet. Choose Add to start with one or two panes, or copy an open tab.",
    "bundles": "Connect Tasks in Settings to see related repositories here.",
}


def field(label, widget):
    return [Label(label, classes="field-label", markup=False), widget]


def scope_field(value="repositories"):
    return field("Save for", Select([("All worktrees of this repository", "repositories"),
                                     ("Only this worktree", "worktrees")], value=value, allow_blank=False, id="scope"))


def scope_of(config, project, kind, entry_id):
    return "worktrees" if entry_id in config["worktrees"].get(project["worktree"], {}).get(kind, {}) else "repositories"


def describe_layout(item):
    lines = []
    for number, role in enumerate(item["roles"].values(), 1):
        if "tool" in role:
            lines.append(f"Pane {number} · {role['tool']['name']}\n  {role['tool']['command']}")
        else:
            lines.append(f"Pane {number} · Shell\n  " + "  ".join(role["shell"]))
    if item["commands"]:
        lines.append("Startup commands")
        lines.extend(f"  {c['id']} · {c['kind']}" for c in item["commands"])
    return "\n\n".join(lines)


class Form(ModalScreen):
    """A labelled, scrollable form with validation that keeps entered values."""
    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+s", "save", "Save")]

    def __init__(self, title, widgets=(), *, help_text="", submit="Save", validate=None, commit=None, information=False):
        super().__init__()
        self.heading, self.widgets, self.help_text = title, list(widgets), help_text
        self.submit, self.validate_values = submit, validate
        self.commit, self.information, self.saving = commit, information, False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.heading, id="dialog-title", markup=False)
            with VerticalScroll(id="form-fields"):
                if self.help_text:
                    yield Static(self.help_text, classes="form-help", markup=False)
                yield from self.widgets
                yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                if not self.information:
                    yield Button("Cancel", id="cancel")
                yield Button(self.submit, variant="primary", id="save")

    def on_mount(self):
        controls = list(self.query("Input, Select, TextArea, Checkbox"))
        (controls[0] if controls else self.query_one("#save" if self.information else "#cancel", Button)).focus()
        self.update_shell_fields()

    def update_shell_fields(self):
        for select in self.query("Select"):
            if select.id and select.id.startswith("role-"):
                self.query_one("#shell-" + select.id).display = select.value == "shell"

    def on_select_changed(self, event: Select.Changed):
        if self.is_mounted:
            self.update_shell_fields()

    def action_cancel(self):
        if not self.saving:
            self.dismiss(None)

    async def action_save(self):
        if self.saving:
            return
        values = {}
        for control in self.query("Input, Select, TextArea, Checkbox"):
            if control.id:
                values[control.id] = control.text if isinstance(control, TextArea) else control.value
        try:
            result = self.validate_values(values) if self.validate_values else values
            if self.commit:
                self.saving = True
                self.query_one("#save", Button).disabled = True
                await self.commit(result)
        except Exception as error:
            message = self.query_one("#form-error", Static)
            message.update(str(error))
            message.scroll_visible()
            return
        finally:
            self.saving = False
            self.query_one("#save", Button).disabled = False
        self.dismiss(result)

    async def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == "save":
            await self.action_save()
        else:
            self.action_cancel()


class LauncherApp(App):
    TITLE = "CLI Launcher"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("ctrl+q", "close", "Close"), ("ctrl+n", "new", "Add"),
                ("ctrl+r", "reload", "Refresh"), ("ctrl+f", "search", "Search"),
                ("escape", "clear_search", "Clear search")]
    CSS = """
    Screen { background: #0c1524; color: #dbe5f2; }
    #dashboard { height: 1fr; }
    #masthead { height: 3; padding: 1 2 0 2; color: #8bd5ca; text-style: bold; }
    #location { height: 2; padding: 0 2; color: #9bacc2; }
    Tabs { padding: 0 1; background: #111e30; }
    #hint { height: auto; padding: 1 2; color: #a9b9ce; }
    Input, TextArea, SelectCurrent, SelectOverlay { border: round #304560; }
    Input:focus, TextArea:focus, Select:focus > SelectCurrent, SelectOverlay:focus {
        border: round #8bd5ca;
    }
    Input.-invalid { border: round #ff9f9f; }
    #filter { margin: 0 2 1 2; height: 3; }
    #body { height: 1fr; padding: 0 2; }
    #list-panel { width: 3fr; min-width: 24; }
    #entries { height: 1fr; background: #111e30; border: round #304560; }
    #entries > .datatable--header { background: #20334d; color: #c9d9ed; }
    #entries > .datatable--cursor { background: #264c63; color: #ffffff; }
    #empty { height: auto; padding: 2; color: #a9b9ce; border: round #304560; }
    #details { width: 2fr; padding: 1 2; margin-left: 1; border: round #304560; background: #101c2d; }
    #selection-title { height: auto; text-style: bold; color: #8bd5ca; margin-bottom: 1; }
    #selection-meta { height: auto; color: #9bacc2; margin-bottom: 1; }
    #selection-body { height: auto; }
    #buttons { height: 3; margin: 1 2 0 2; }
    #buttons Button { margin-right: 1; min-width: 10; }
    #status { height: auto; max-height: 4; padding: 1 2; color: #a9b9ce; }
    Footer { background: #17263b; }
    .narrow #body { layout: vertical; }
    .narrow #list-panel { width: 1fr; height: 1fr; }
    .narrow #details { width: 1fr; height: 8; margin: 1 0 0 0; padding: 0 1; }
    .short #hint { display: none; }
    .short #masthead { height: 1; padding: 0 2; }
    .short #location { height: 1; }
    .short #buttons { margin-top: 0; }
    .short #status { height: 1; padding: 0 2; }
    .tiny #buttons { height: 6; layout: grid; grid-size: 2 2; grid-columns: 1fr 1fr; grid-rows: 3 3; }
    .tiny #buttons Button { width: 1fr; min-width: 0; margin: 0; }
    .tiny #masthead { text-overflow: ellipsis; }
    .short #details { display: none; }
    .short #filter { margin-bottom: 0; }
    Form { align: center middle; background: #030a14 75%; }
    #dialog { width: 76; max-width: 96%; height: 90%; max-height: 40; border: round #5c93ab; background: #111e30; padding: 1 2; }
    #dialog-title { height: auto; text-style: bold; color: #8bd5ca; margin-bottom: 1; }
    #form-fields { height: 1fr; }
    .field-label { height: auto; margin-top: 1; color: #c9d9ed; }
    .form-help { height: auto; color: #9bacc2; margin-bottom: 1; }
    Form Input, Form Select { height: 3; }
    Form TextArea { height: 4; }
    .shell-fields { height: auto; }
    #form-error { height: auto; color: #ff9f9f; margin-top: 1; }
    .dialog-actions { height: 3; margin-top: 1; align-horizontal: right; }
    .dialog-actions Button { margin-left: 1; }
    """

    def __init__(self, presets_path, cwd, source=None, section="links", rpc=launcher.call):
        super().__init__()
        self.presets_path = Path(presets_path)
        self.config_path = self.presets_path.with_name("projects.json")
        self.cwd, self.source, self.section, self.rpc = cwd, source or {}, section, rpc
        self.project = backend.identity(cwd)
        self.rows, self.visible_ids, self.data, self.bundles = {}, [], deepcopy(backend.EMPTY), []
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard"):
            yield Static("CLI LAUNCHER   /   " + Path(self.project["worktree"]).name, id="masthead", markup=False)
            yield Static(self.project["worktree"], id="location", markup=False)
            yield Tabs(*(Tab(label, id=key) for key, label in SECTIONS.items()), active=self.section)
            yield Static(HINTS[self.section], id="hint", markup=False)
            yield Input(placeholder="Search by name or destination…", id="filter")
            with Horizontal(id="body"):
                with Vertical(id="list-panel"):
                    yield DataTable(id="entries", cursor_type="row", zebra_stripes=True, show_row_labels=False)
                    yield Static("", id="empty", markup=False)
                with VerticalScroll(id="details"):
                    yield Static("", id="selection-title", markup=False)
                    yield Static("", id="selection-meta", markup=False)
                    yield Static("", id="selection-body", markup=False)
            with Horizontal(id="buttons"):
                yield Button("Open", id="open", variant="primary")
                yield Button("Add", id="add")
                yield Button("Edit", id="edit")
                yield Button("More…", id="more")
            yield Static("Select an item with ↑/↓ or the mouse. Enter opens it.", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        self.query_one("#entries", DataTable).add_columns("Name", "Details")
        self.perform("reload")

    def on_resize(self, event):
        self.set_class(event.size.width < 95, "narrow")
        self.set_class(event.size.height < 29, "short")
        self.set_class(event.size.width < 50, "tiny")

    def action_close(self):
        if self.busy:
            self.notify("Wait for the current action, or cancel its dialog first.")
        else:
            self.exit()

    def action_new(self):
        if self.section in ("tools", "links", "arrangements"):
            self.perform("add")

    def action_reload(self):
        self.perform("reload")

    def action_search(self):
        self.query_one("#filter", Input).focus()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated):
        if event.tab.id != self.section:
            self.section = event.tab.id
            self.query_one("#filter", Input).value = ""
            self.perform("reload")

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "filter":
            self.draw_rows()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "filter":
            self.perform("open")

    def action_clear_search(self):
        self.query_one("#filter", Input).value = ""
        self.query_one("#entries", DataTable).focus()

    def current(self):
        table = self.query_one("#entries", DataTable)
        return self.visible_ids[table.cursor_row] if self.visible_ids and table.cursor_row < len(self.visible_ids) else None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        self.show_selection(event.row_key.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.perform("open")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id in ("open", "add", "edit", "more"):
            self.perform(event.button.id)

    def scope_label(self, kind, entry_id):
        return "This worktree" if scope_of(self.data, self.project, kind, entry_id) == "worktrees" else "Repository default"

    def refresh_data(self):
        if backend.identity(self.cwd) != self.project:
            raise ValueError("This checkout changed. Close and reopen the dashboard.")
        self.data = backend.load_config(self.config_path)
        if self.section == "tools":
            self.rows = {backend.digest(item): item for item in launcher.load_presets(self.presets_path)}
        elif self.section in ("links", "arrangements"):
            self.rows = backend.effective(self.data, self.project, self.section)
        elif self.section == "bundles":
            self.rows = {item["id"]: backend.validate_bundle(item) for item in self.bundles}
        else:
            self.rows = {"tasks": {"name": "Tasks", "description": "Related repository bundles"},
                         "commands": {"name": "Project Commands", "description": "Managed services, tests and live URLs"},
                         "overrides": {"name": "Worktree overrides", "description": "Use repository defaults for this checkout"}}

    def row_summary(self, key, item):
        if self.section == "tools":
            return item["command"]
        if self.section == "links":
            return item.get("destination", "Service · " + item.get("service", ""))
        if self.section == "arrangements":
            return f"{len(item['roles'])} panes · {self.scope_label('arrangements', key)}"
        if self.section == "bundles":
            return f"{len(item['members'])} repositories"
        if key == "overrides":
            count = sum(len(v) for v in self.data["worktrees"].get(self.project["worktree"], {}).values())
            return f"{count} overrides"
        return "Configured" if key in self.data["providers"] else "Not connected · optional"

    def draw_rows(self):
        if not self.is_mounted:
            return
        old = self.current()
        table = self.query_one("#entries", DataTable)
        table.clear()
        query = self.query_one("#filter", Input).value.casefold().strip()
        self.visible_ids = []
        for key, item in self.rows.items():
            summary = self.row_summary(key, item)
            if query and query not in (item["name"] + " " + summary).casefold():
                continue
            self.visible_ids.append(key)
            table.add_row(Text(item["name"]), Text(summary), key=key)
        empty = self.query_one("#empty", Static)
        empty.display = not self.visible_ids
        table.display = bool(self.visible_ids)
        hint = "No bundles for this checkout. Create repository relationships in Tasks." if self.section == "bundles" and "tasks" in self.data["providers"] else EMPTY_HINTS.get(self.section, "No entries.")
        empty.update("No matches. Try a different search." if query else hint)
        self.query_one("#hint", Static).update(HINTS[self.section])
        if self.visible_ids:
            table.move_cursor(row=self.visible_ids.index(old) if old in self.visible_ids else 0)
        self.show_selection(self.current())

    def show_selection(self, key):
        item = self.rows.get(key)
        title, meta, body = "Your project, in one place", "", "Save links and layouts here.\n\nTools are shared across projects. Links and layouts can apply to this worktree or the entire repository."
        if item:
            title = item["name"]
            if self.section == "tools":
                meta, body = "Global tool · opens in a new tab", item["command"] + "\n\nWorking directory\n" + self.cwd
            elif self.section == "links":
                meta = self.scope_label("links", key)
                body = item.get("destination", "Current URL from Project Commands\nService: " + item.get("service", ""))
            elif self.section == "arrangements":
                meta, body = self.scope_label("arrangements", key), describe_layout(item)
            elif self.section == "bundles":
                meta = "Owned by Tasks · opening does not switch branches"
                body = "\n\n".join(f"{Path(m['cwd']).name}\n{m['cwd']}\n{m['branch']['value'] or 'Non-Git folder'}" for m in item["members"])
            else:
                meta = self.row_summary(key, item)
                body = item["description"] + ("\n\nExisting panes and global tools are not changed." if key == "overrides" else
                    "\n\nConfigure detects an installed module and tests the connection before saving. This integration is optional.")
        for selector, value in (("#selection-title", title), ("#selection-meta", meta), ("#selection-body", body)):
            self.query_one(selector, Static).update(value)
        self.query_one("#open", Button).label = "Configure" if self.section == "settings" else "Launch…" if self.section == "tools" else "Open"
        self.query_one("#open", Button).disabled = not bool(item)
        for button in ("add", "edit"):
            self.query_one("#" + button, Button).display = self.section in ("tools", "links", "arrangements")
        self.query_one("#edit", Button).disabled = not bool(item)
        self.query_one("#more", Button).display = self.section in ("tools", "links", "arrangements")
        self.query_one("#more", Button).disabled = not bool(item)

    async def ask(self, title, widgets=(), **kwargs):
        return await self.push_screen_wait(Form(title, widgets, **kwargs))

    async def message(self, title, body):
        await self.ask(title, help_text=body, submit="Close", information=True)

    async def save_entry(self, kind, key, item, scope, previous):
        if backend.identity(self.cwd) != self.project:
            raise ValueError("This checkout changed. Reopen the dashboard.")
        updated = deepcopy(previous)
        target = self.project["worktree" if scope == "worktrees" else "repository"]
        updated[scope].setdefault(target, {}).setdefault(kind, {})[key] = item
        await asyncio.to_thread(backend.save_config, self.config_path, updated, previous)

    @work
    async def perform(self, action):
        if self.busy:
            return
        self.busy = True
        self.query_one("#dashboard").disabled = True
        self.query_one("#status", Static).update("Working…")
        try:
            key = self.current()
            item = deepcopy(self.rows.get(key))
            if action == "reload":
                if self.section == "bundles":
                    config = await asyncio.to_thread(backend.load_config, self.config_path)
                    self.bundles = await asyncio.to_thread(backend.provider, config, "tasks", "bundles.list", self.project["worktree"]) if "tasks" in config["providers"] else []
                if self.is_mounted:
                    self.refresh_data()
            elif action in ("add", "edit"):
                if self.section == "tools":
                    await self.edit_tool(item)
                elif self.section == "links":
                    await self.edit_link(key, item)
                elif self.section == "arrangements":
                    await self.edit_layout(key, item)
            elif item is not None:
                if action == "more":
                    await self.more(key, item)
                elif self.section == "tools":
                    await self.launch_agent(item)
                elif self.section == "links":
                    await asyncio.to_thread(backend.open_link, self.config_path, self.project, key, item)
                    self.notify("Opened " + item["name"])
                elif self.section == "arrangements":
                    await self.open_layout(key, item)
                elif self.section == "bundles":
                    await self.open_bundle(item)
                elif self.section == "settings":
                    await self.settings(key)
        except Exception as error:
            await self.message("Couldn't complete that action", str(error))
        finally:
            if self.is_mounted:
                self.busy = False
                self.query_one("#dashboard").disabled = False
                try:
                    self.refresh_data()
                    self.draw_rows()
                except Exception as error:
                    self.query_one("#status", Static).update(str(error))
                else:
                    self.query_one("#status", Static).update("↑/↓ Select   Enter Open   Tab Move focus   Ctrl+N Add")
                self.query_one("#entries", DataTable).focus()

    async def edit_tool(self, item):
        previous = launcher.load_presets(self.presets_path)
        if item and item not in previous:
            raise ValueError("This tool changed. Refresh the list first.")
        old = item or {"name": "", "command": ""}
        def validate(values):
            value = {"name": values["name"].strip(), "command": values["command"].strip()}
            changed = deepcopy(previous)
            if item:
                changed[changed.index(item)] = value
            else:
                changed.append(value)
            return launcher.validate(changed)
        async def commit(result):
            await self.save_tools(result, previous)
        await self.ask("Edit tool" if item else "Add a tool",
            field("Name", Input(old["name"], placeholder="Codex", id="name")) +
            field("Command", Input(old["command"], placeholder="codex", id="command")),
            help_text="Available in every project. Permission flags are part of the saved command.", validate=validate, commit=commit)

    async def save_tools(self, result, previous):
        def save():
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                launcher.save_presets(self.presets_path, result, previous)
            return output.getvalue()
        warning = await asyncio.to_thread(save)
        self.notify(warning.strip() if warning else "Tools saved", severity="warning" if warning else "information", timeout=10)

    async def open_tool(self, item):
        launcher.selected_preset(self.presets_path, json.dumps(item))
        if self.source:
            await asyncio.to_thread(launcher.open_launcher, self.source, self.presets_path, self.rpc, "run", item)
        else:
            command = backend.role_command({"tool": item}, self.cwd, self.presets_path)
            opened = await asyncio.to_thread(self.rpc, "terminal.backend.create", cwd=self.project["worktree"],
                                    placement={"kind": "workspace"}, command=command, label=item["name"], focus=True)
            from tab_titles import remember
            await asyncio.to_thread(remember, self.rpc, opened["pane_id"], "▷ " + item["name"])

    async def launch_agent(self, item):
        launcher.selected_preset(self.presets_path, json.dumps(item))
        if self.source:
            await asyncio.to_thread(launcher.launch_target, self.source, self.rpc)
        try:
            state = await asyncio.to_thread(agent_launch.snapshot, self.cwd)
        except (ValueError, OSError):
            state = None
        modes = agent_launch.MODES if state else [("Current directory", "current")]
        choice = await self.ask("Launch " + item["name"],
            field("Destination", Select(modes, value=modes[0][1], allow_blank=False, id="mode")),
            help_text="Choose where the new agent will work. Other agents keep their saved command arguments.", submit="Next")
        if choice is None:
            return
        mode, base, branch, destination = choice["mode"], "HEAD", "", ""
        if mode != "current":
            if mode == "new":
                choices = [("Current HEAD", "HEAD")] + [(ref, ref) for ref in state["refs"]]
                widgets = field("Start from", Select(choices, value="HEAD", allow_blank=False, id="base")) + field("New branch name", Input(id="branch"))
            else:
                choices = [(ref.removeprefix("refs/heads/"), ref.removeprefix("refs/heads/")) for ref in state["refs"] if ref.startswith("refs/heads/")]
                if not choices:
                    raise ValueError("This repository has no local branches.")
                widgets = field("Local branch", Select(choices, value=choices[0][1], allow_blank=False, id="branch"))
            async def check_branch(values):
                await asyncio.to_thread(agent_launch.prepare, self.cwd, mode, values.get("base", "HEAD"),
                                        values["branch"], str(Path(self.project["worktree"]).parent / ("launch-check-" + uuid.uuid4().hex)))
            answer = await self.ask("Select branch", widgets, submit="Next", commit=check_branch)
            if answer is None:
                return
            branch, base = answer["branch"], answer.get("base", "HEAD")
            occupied = any(w.get("branch") == "refs/heads/" + branch for w in state["worktrees"])
            if mode == "new" or mode == "existing" and not occupied:
                async def check_destination(values):
                    await asyncio.to_thread(agent_launch.prepare, self.cwd, mode, base, branch, values["destination"])
                answer = await self.ask("Worktree destination", field("Directory", Input(agent_launch.default_path(self.presets_path, self.project["worktree"], branch), id="destination")), submit="Review", commit=check_destination)
                if answer is None:
                    return
                destination = answer["destination"]
        plan = await asyncio.to_thread(agent_launch.prepare, self.cwd, mode, base, branch, destination)
        operation = uuid.uuid4().hex
        async def commit(_):
            await asyncio.to_thread(agent_launch.execute, self.presets_path, plan, item, operation, self.rpc)
        await self.ask("Launch " + item["name"], help_text=agent_launch.summary(plan, item), submit="Launch", commit=commit)

    async def retry_agent(self):
        record = backend.read_json(self.presets_path.with_name("agent-launch.json"), {})
        if not record:
            raise ValueError("There is no recorded launch to retry.")
        async def commit(_):
            await asyncio.to_thread(agent_launch.execute, self.presets_path, record["plan"], record["preset"], record["operation"], self.rpc)
        await self.ask("Retry last launch", help_text=agent_launch.summary(record["plan"], record["preset"]) + "\nState: " + record["state"], submit="Retry", commit=commit)

    async def edit_link(self, key, item):
        previous = backend.load_config(self.config_path)
        if item:
            backend.selected(self.config_path, self.project, "links", key, item)
        old = item or {"name": "", "destination": ""}
        scope = scope_of(previous, self.project, "links", key)
        kind = "service" if "service" in old else "destination"
        def validate(values):
            value = {"name": values["name"].strip(), values["kind"]: values["address"].strip()}
            backend.validate_link(value)
            if "destination" in value:
                backend.destination(value["destination"], self.project["worktree"])
            return value, values["scope"]
        async def commit(result):
            value, scope = result
            await self.save_entry("links", key or uuid.uuid4().hex, value, scope, previous)
            self.notify("Link saved")
        await self.ask("Edit link" if item else "Add a project link",
            field("Name", Input(old["name"], placeholder="Documentation", id="name")) +
            field("Link type", Select([("Website or local document", "destination"), ("Running service URL", "service")],
                                      value=kind, allow_blank=False, id="kind")) +
            field("Address, document path, or service name", Input(old.get(kind, ""), placeholder="https://… or docs/README.md", id="address")) +
            scope_field(scope), help_text="Relative document paths use this checkout. Service URLs require Project Commands.", validate=validate, commit=commit)

    async def edit_layout(self, key, item):
        previous = backend.load_config(self.config_path)
        if item:
            backend.selected(self.config_path, self.project, "arrangements", key, item)
        layouts = [("One pane", "one"), ("Two panes · side by side", "columns"), ("Two panes · stacked", "rows")]
        sources = {}
        try:
            inv = await asyncio.to_thread(backend.inventory, self.rpc)
            for terminal in inv["terminals"]:
                if backend.canonical(terminal["workspace"]["root"]) != self.project["worktree"]:
                    continue
                tab = terminal["tab"]["index"]
                source_key = f"tab-{tab}"
                if source_key not in sources:
                    sources[source_key] = terminal["pane_id"]
                    layouts.append((Text(f"Copy open tab {tab} · {terminal['tab'].get('name') or 'Terminal'}"), source_key))
        except (ValueError, OSError, KeyError):
            pass  # Built-in layouts still work when live capture is unavailable.
        if item:
            layouts.insert(0, ("Keep saved layout", "saved"))
        choice = await self.ask("Choose a layout", field("Pane arrangement", Select(layouts, value="saved" if item else "one", allow_blank=False, id="layout")),
                                help_text="Create a simple layout or copy the geometry of an open tab. Tool conversations are never copied.", submit="Next")
        if choice is None:
            return
        shape = choice["layout"]
        if shape == "saved":
            tree = deepcopy(item["tree"])
        elif shape in sources:
            tree = await asyncio.to_thread(self.capture_tree, sources[shape])
        elif shape == "one":
            tree = {"Leaf": "pane1"}
        else:
            tree = {"Split": {"axis": int(shape == "rows"), "ratio": .5, "a": {"Leaf": "pane1"}, "b": {"Leaf": "pane2"}}}
        role_ids = []
        backend.map_tree(tree, lambda role: role_ids.append(role) or role)
        if not 1 <= len(role_ids) <= 16:
            raise ValueError("Choose a tab with 1–16 panes, or a simple layout.")
        presets = launcher.load_presets(self.presets_path)
        widgets = field("Name", Input(item["name"] if item else "", placeholder="Development", id="name"))
        for number, role_id in enumerate(role_ids):
            role = item["roles"].get(role_id, {}) if item else {}
            options = [(Text(preset["name"]), str(i)) for i, preset in enumerate(presets)] + [("Shell", "shell")]
            selected = str(presets.index(role["tool"])) if role.get("tool") in presets else "shell" if "shell" in role or not presets else "0"
            if "tool" in role and role["tool"] not in presets:
                options.insert(0, (Text("Changed or missing tool · " + role["tool"]["name"]), "stale"))
                selected = "stale"
            shell = role.get("shell", [shutil.which("pwsh") or shutil.which("powershell") or "powershell"] if os.name == "nt" else [os.environ.get("SHELL") or "/bin/sh"])
            widgets += field(f"Pane {number + 1} · tool", Select(options, value=selected, allow_blank=False, id=f"role-{number}"))
            widgets.append(Vertical(*field("Shell program", Input(shell[0], id=f"program-{number}")),
                                    *field("Shell arguments · one per line", TextArea("\n".join(shell[1:]), id=f"arguments-{number}")),
                                    id=f"shell-role-{number}", classes="shell-fields"))
        widgets += scope_field(scope_of(previous, self.project, "arrangements", key))
        old_commands = item["commands"] if item else []
        widgets += field("Optional startup services / tests · one name per line",
                         TextArea("\n".join(c["id"] for c in old_commands), id="commands",
                                  disabled="commands" not in previous["providers"]))
        widgets.append(Static("Connect Project Commands in Settings to add managed services or tests.", classes="form-help", markup=False))
        def validate(values):
            roles = {}
            for number, role_id in enumerate(role_ids):
                selected = values[f"role-{number}"]
                if selected == "stale":
                    raise ValueError(f"Choose a current tool for pane {number + 1}; its saved tool changed.")
                roles[role_id] = {"shell": backend.argv([values[f"program-{number}"].strip(), *values[f"arguments-{number}"].splitlines()])} if selected == "shell" else {"tool": presets[int(selected)]}
            result = backend.validate_arrangement({"name": values["name"].strip(), "roles": roles, "tree": tree, "commands": old_commands})
            return result, values["scope"], [name.strip() for name in values["commands"].splitlines() if name.strip()]
        async def commit(result):
            value, scope, command_ids = result
            if "commands" in previous["providers"]:
                value["commands"] = []
                for command_id in command_ids:
                    description = await asyncio.to_thread(backend.provider, previous, "commands", "commands.describe", self.project["worktree"], id=command_id)
                    value["commands"].append({k: description[k] for k in ("id", "revision", "kind")})
            backend.validate_arrangement(value)
            await self.save_entry("arrangements", key or uuid.uuid4().hex, value, scope, previous)
            self.notify("Layout saved")
        await self.ask("Set up your panes", widgets,
                       help_text="Saving does not launch anything. Startup command revisions are refreshed on save; review them before opening.",
                       validate=validate, commit=commit)

    def capture_tree(self, pane_id):
        pane = self.rpc("pane.get", pane=pane_id)
        workspace = self.rpc("workspace.get", workspace_id=pane["workspace_id"])
        if backend.identity(workspace["cwd"]) != self.project:
            raise ValueError("That tab moved to another project. Choose a tab again.")
        exported = self.rpc("layout.export", workspace_id=pane["workspace_id"], tab_id=pane["tab_id"])
        roles = []
        def role(_):
            roles.append(f"pane{len(roles) + 1}")
            return roles[-1]
        return backend.map_tree(exported["tree"], role)

    async def open_layout(self, key, item, action="open"):
        title = {"open": "Open layout", "recover": "Recover missing or failed steps", "copy": "Open another copy"}[action]
        answer = await self.ask(title, help_text=item["name"] + "\n" + self.project["worktree"] + "\n\n" + describe_layout(item), submit="Open" if action == "open" else "Continue")
        if answer is None:
            return
        try:
            result = await asyncio.to_thread(backend.run_arrangement, self.config_path, self.project, key, item,
                                             rpc=self.rpc, recover=action == "recover", new_copy=action == "copy")
        except Exception as error:
            await self.message("Layout needs attention", str(error) + "\n\n" + self.run_summary(key))
        else:
            self.notify(result.get("notice", "Layout opened"), timeout=10)

    def run_summary(self, key):
        records = backend.read_json(self.config_path.with_name("arrangement-runs.json"), {})
        record = records.get(backend.digest([self.project["worktree"], key]))
        if not record:
            return "This layout has not been launched in this worktree."
        labels = {"succeeded": "Created / dispatched", "pending": "Not started", "failed": "Failed", "uncertain": "Needs inspection"}
        lines = ["Recorded outcomes of the last launch; not a live process check.", "Layout applied" if record["layout"] else "Layout not yet applied"]
        for kind in ("roles", "commands"):
            for name, step in record[kind].items():
                lines.append(f"{name}: {labels.get(step['state'], step['state'])}")
        return "\n".join(lines)

    async def more(self, key, item):
        options = [("Remove from this list…", "delete")]
        if self.section == "arrangements":
            options = [("Show launch status", "status"), ("Recover missing / failed steps…", "recover"),
                       ("Open another copy…", "copy"), ("Forget launch tracking…", "forget"), *options]
        elif self.section == "tools":
            options = [("Open here immediately", "open-here"), ("Retry last launch…", "retry-agent"), *options,
                       ("Refresh right-click menu", "refresh-menu")]
        answer = await self.ask(item["name"], field("Action", Select(options, value=options[0][1], allow_blank=False, id="action")), submit="Continue")
        if answer is None:
            return
        action = answer["action"]
        if action in ("recover", "copy"):
            await self.open_layout(key, item, action)
        elif action == "status":
            await self.message(item["name"] + " · launch status", self.run_summary(key))
        elif action == "refresh-menu":
            await asyncio.to_thread(launcher.refresh_menu, self.presets_path)
        elif action == "open-here":
            await self.open_tool(item)
        elif action == "retry-agent":
            await self.retry_agent()
        elif action == "forget":
            answer = await self.ask("Forget launch tracking?", help_text="Inspect existing panes first. They will stay open, and another launch may duplicate tools.", submit="Forget tracking")
            if answer is not None:
                path = self.config_path.with_name("arrangement-runs.json")
                with backend.locked(path.with_suffix(".lock")):
                    state = backend.read_json(path, {})
                    state.pop(backend.digest([self.project["worktree"], key]), None)
                    backend.atomic_write(path, state)
        elif action == "delete":
            previous = backend.load_config(self.config_path)
            tools = launcher.load_presets(self.presets_path)
            widgets = [] if self.section == "tools" else scope_field(scope_of(previous, self.project, self.section, key))
            answer = await self.ask("Remove " + item["name"] + "?", widgets, help_text="Existing tool tabs and services will remain open.", submit="Remove")
            if answer is None:
                return
            if self.section == "tools":
                if item not in tools:
                    raise ValueError("This tool changed. Refresh first.")
                await self.save_tools([t for t in tools if t != item], tools)
            else:
                backend.selected(self.config_path, self.project, self.section, key, item)
                await self.save_entry(self.section, key, None, answer["scope"], previous)

    async def open_bundle(self, bundle):
        config = backend.load_config(self.config_path)
        summaries, revisions = [], {}
        for member in bundle["members"]:
            summaries.append(member["cwd"] + "\n  " + (member["branch"]["value"] or "Non-Git folder"))
            if member.get("arrangement"):
                try:
                    project = backend.identity(member["cwd"])
                    layout = backend.effective(config, project, "arrangements").get(member["arrangement"])
                except (ValueError, OSError):
                    layout = None
                if layout:
                    revisions[backend.canonical(member["cwd"])] = backend.digest(layout)
                    summaries.append("Optional layout: " + layout["name"] + "\n" + describe_layout(layout))
                else:
                    summaries.append("The optional saved layout is unavailable for this checkout.")
        answer = await self.ask("Open " + bundle["name"], [Checkbox("Also open the saved layouts shown below", id="layouts")],
                                help_text="Branches will not be switched.\n\n" + "\n\n".join(summaries), submit="Open workspaces")
        if answer is not None:
            results = await asyncio.to_thread(backend.open_bundle, self.config_path, self.project, bundle, answer["layouts"],
                                             rpc=self.rpc, arrangement_revisions=revisions)
            await self.message("Bundle results", "\n\n".join(r["cwd"] + "\n" + ("Opened" if r["state"] == "succeeded" else r["error"]) for r in results))

    async def settings(self, key):
        previous = backend.load_config(self.config_path)
        if key == "overrides":
            async def commit(_):
                updated = deepcopy(previous)
                updated["worktrees"].pop(self.project["worktree"], None)
                await asyncio.to_thread(backend.save_config, self.config_path, updated, previous)
            await self.ask("Use repository defaults?", help_text="Remove all link and layout overrides for this worktree. Global tools, existing panes and services are not changed.", submit="Reset overrides", commit=commit)
            return
        old = previous["providers"].get(key)
        help_text = "Test reads available bundles or commands; it does not launch services. Leave Program empty to disconnect."
        if not old:
            try:
                old = await asyncio.to_thread(backend.installed_provider, key, self.rpc)
                help_text = "Installed module detected. " + help_text
            except (ValueError, OSError, KeyError) as error:
                old = [""]
                help_text = str(error) + " Supply an owner-compatible entrypoint. " + help_text
        def validate(values):
            executable = values["program"].strip()
            if not executable:
                return []
            if not Path(executable).is_absolute():
                raise ValueError("Choose an absolute path to the integration's executable.")
            return backend.argv([executable, *values["arguments"].splitlines()])
        async def commit(answer):
            updated = deepcopy(previous)
            if answer:
                updated["providers"][key] = answer
                status = await asyncio.to_thread(backend.check_provider, updated, key, self.project["worktree"])
            else:
                updated["providers"].pop(key, None)
                status = "Disconnected"
            await asyncio.to_thread(backend.save_config, self.config_path, updated, previous)
            self.notify(status, timeout=10)
        await self.ask("Connect " + ("Tasks" if key == "tasks" else "Project Commands"),
            field("Program", Input(old[0], placeholder="/absolute/path/to/python", id="program")) +
            field("Arguments · one per line", TextArea("\n".join(old[1:]), id="arguments")),
            help_text=help_text, validate=validate, commit=commit, submit="Test & save")
