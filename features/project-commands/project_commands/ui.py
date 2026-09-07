"""Explicit project scope; background updates never move keyboard focus."""
import copy
import json
from pathlib import Path
import time
import uuid
import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.containers import HorizontalScroll as Horizontal, VerticalScroll
from rich.text import Text
from textual.screen import ModalScreen
from textual.widgets import Header, Footer, Static, Button, DataTable, TabbedContent, TabPane, TextArea, Input, Label, Select

from . import diagnostics as diag, runtime
from .model import ACTIVE, LOG_LIMIT, validate_definition


class Preview(ModalScreen):
    DEFAULT_CSS = 'Preview {align: center middle;} Preview > VerticalScroll {width: 90%; height: 90%; border: solid $accent; background: $surface;} Preview TextArea {height: 1fr; min-height: 8;} Preview HorizontalScroll {height: 3;}'
    BINDINGS = [('escape', 'cancel', 'Cancel')]
    def __init__(self, title, text, confirm=None, editable=False):
        super().__init__(); self.title_text = title; self.text = text; self.confirm = confirm; self.editable = editable
    def compose(self):
        with VerticalScroll():
            yield Label(self.title_text, markup=False)
            yield TextArea(self.text, read_only=not self.editable, id='preview-text', show_line_numbers=True)
            with Horizontal():
                if self.confirm: yield Button(self.confirm, id='confirm', variant='warning')
                yield Button('Close / Cancel', id='close')
    def on_button_pressed(self, event):
        self.dismiss(self.query_one(TextArea).text if event.button.id == 'confirm' else None)
    def action_cancel(self): self.dismiss(None)


class CommandForm(ModalScreen):
    DEFAULT_CSS = 'CommandForm {align: center middle;} CommandForm > VerticalScroll {width: 90%; height: 90%; background: $surface; border: solid $accent;} CommandForm TextArea {height: 8;} CommandForm Input, CommandForm Select {height: 3;} CommandForm HorizontalScroll {height: 3;}'
    BINDINGS = [('escape', 'cancel', 'Cancel')]
    def __init__(self, definition=None):
        super().__init__(); self.definition = copy.deepcopy(definition or {})
    def compose(self):
        d = self.definition
        with VerticalScroll():
            yield Label('Edit command' if d else 'Add command', markup=False)
            for key, title, value in [('id', 'Stable ID', d.get('id', '')), ('name', 'Name', d.get('name', '')), ('program', 'Program (no shell quoting)', d.get('argv', [''])[0]), ('cwd', 'Directory relative to checkout', d.get('cwd', '.'))]:
                yield Label(title); yield Input(value, id='field-' + key, disabled=key == 'id' and bool(d))
            yield Label('Kind'); yield Select([('Command', 'command'), ('Service', 'service')], value=d.get('kind', 'command'), allow_blank=False, id='field-kind')
            yield Label('Category'); yield Input(d.get('category', 'custom'), id='field-category')
            yield Label('Arguments: one argument per line; no shell quoting')
            yield TextArea('\n'.join(d.get('argv', [])[1:]), id='field-args')
            yield Static('Ports, readiness, platform overrides and reports are preserved. Use Configuration for advanced fields.', markup=False)
            yield Static('', id='form-error', markup=False)
            with Horizontal():
                yield Button('Review command', id='form-save', variant='primary'); yield Button('Cancel', id='form-cancel')
    def on_button_pressed(self, event):
        if event.button.id == 'form-cancel': self.dismiss(None); return
        try:
            d = copy.deepcopy(self.definition)
            for key in ('id', 'name', 'cwd', 'category'): d[key] = self.query_one('#field-' + key, Input).value.strip()
            d['kind'] = self.query_one('#field-kind', Select).value
            d['argv'] = [self.query_one('#field-program', Input).value.strip(), *self.query_one('#field-args', TextArea).text.splitlines()]
            if not d['argv'][0]: raise ValueError('Program is required.')
            self.dismiss(validate_definition(d))
        except (ValueError, TypeError) as e: self.query_one('#form-error', Static).update(str(e))
    def action_cancel(self): self.dismiss(None)


class DiscoveryPicker(ModalScreen):
    DEFAULT_CSS = 'DiscoveryPicker {align: center middle;} DiscoveryPicker > VerticalScroll {width: 95%; height: 95%; background: $surface; border: solid $accent;} DiscoveryPicker DataTable {height: 1fr; min-height: 5;} #suggestion-detail {height: 6; overflow-y: auto;} DiscoveryPicker HorizontalScroll {height: 3;}'
    BINDINGS = [('escape', 'cancel', 'Cancel')]
    def __init__(self, suggestions):
        super().__init__(); self.suggestions = sorted(suggestions, key=lambda d: (d.get('category', ''), d['name'])); self.chosen = set(); self.filtered = []
    def compose(self):
        with VerticalScroll():
            yield Label('Choose commands to add. Nothing is selected or executed automatically.', markup=False)
            yield Input(placeholder='Filter by name, category or script', id='suggestion-search')
            yield DataTable(id='suggestions', cursor_type='row')
            yield Static('', id='suggestion-detail', markup=False)
            with Horizontal():
                yield Button('Toggle selected row', id='suggestion-toggle')
                yield Button('Review selected (0)', id='suggestion-save', disabled=True, variant='primary')
                yield Button('Cancel', id='suggestion-cancel')
    def on_mount(self):
        self.query_one(DataTable).add_columns('Use', 'Category', 'Name'); self.render_rows()
    def render_rows(self):
        query = self.query_one(Input).value.casefold()
        self.filtered = [d for d in self.suggestions if query in (d['name'] + ' ' + d.get('category', '') + ' ' + d.get('source', '')).casefold()]
        table = self.query_one(DataTable); row = table.cursor_row; table.clear()
        for d in self.filtered: table.add_row('✓' if d['id'] in self.chosen else '□', Text(d.get('category', 'custom')), Text(d['name']), key=d['id'])
        if self.filtered: table.move_cursor(row=min(row, len(self.filtered)-1)); self.detail()
        else: self.query_one('#suggestion-detail', Static).update('No matching suggestions.')
        button = self.query_one('#suggestion-save', Button); button.label = f'Review selected ({len(self.chosen)})'; button.disabled = not self.chosen
    def detail(self):
        row = self.query_one(DataTable).cursor_row
        if row < len(self.filtered):
            d = self.filtered[row]
            self.query_one('#suggestion-detail', Static).update(diag.safe_text(f'{d["name"]} — {d.get("kind", "command")}\nDirectory: {d.get("cwd", ".")}\nProgram/arguments: {json.dumps(d["argv"])}\n{d.get("source", "")}'))
    def on_input_changed(self, event): self.render_rows()
    def on_data_table_row_highlighted(self, event): self.detail()
    def on_data_table_row_selected(self, event): self.toggle()
    def toggle(self):
        row = self.query_one(DataTable).cursor_row
        if row < len(self.filtered): self.chosen.symmetric_difference_update({self.filtered[row]['id']}); self.render_rows()
    def on_button_pressed(self, event):
        if event.button.id == 'suggestion-toggle': self.toggle()
        elif event.button.id == 'suggestion-save': self.dismiss([d for d in self.suggestions if d['id'] in self.chosen])
        else: self.dismiss(None)
    def action_cancel(self): self.dismiss(None)


class Console(App):
    TITLE = 'Project Commands'
    CSS = 'Screen {layout: vertical;} #scope {height: auto; max-height: 3;} TabbedContent {height: 1fr;} DataTable {height: 1fr; min-height: 5;} HorizontalScroll {height: 4; min-height: 3;} Button {min-width: 9; margin-right: 1;} #config-text {height: 1fr;} #feedback {height: auto; max-height: 5;} #agent-target {width: 20;}'
    BINDINGS = [('ctrl+q', 'quit', 'Close panel'), ('ctrl+r', 'refresh', 'Refresh'), ('ctrl+p', 'command_palette', 'Commands')]

    def __init__(self, store, root, host=None):
        super().__init__(); self.store = store; self.root = root; self.host = host
        self.rows = {}; self.problem_run = None; self.config_before = None; self.busy = False; self.cpu_samples = {}; self.fresh_checks = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static('Project: ' + self.root + '\nClosing this panel leaves service output terminals running.', id='scope', markup=False)
        with TabbedContent():
            with TabPane('Commands', id='commands-pane'):
                yield DataTable(id='commands', cursor_type='row')
                with Horizontal():
                    yield Button('Run / Start', id='run', variant='primary')
                    yield Button('Discover', id='discover')
                    yield Button('Add', id='command-add')
                    yield Button('Edit', id='command-edit')
                    yield Button('Remove', id='command-remove')
                    yield Button('Refresh', id='refresh')
            with TabPane('Runs / Services', id='runs-pane'):
                yield DataTable(id='runs', cursor_type='row')
                with Horizontal():
                    for name, title in [('output', 'Output'), ('terminal', 'Live terminal'), ('rerun', 'Rerun / Restart'), ('freshness', 'Check freshness'), ('cancel', 'Stop'), ('force', 'Force-stop'), ('problems', 'Problems'), ('urls', 'URLs'), ('resolve', 'Acknowledge lost tracking')]: yield Button(title, id=name)
                with Horizontal():
                    yield Input(placeholder='Agent pane ID or name', id='run-agent-target')
                    yield Button('Preview failure context', id='send-output')
            with TabPane('Problems', id='problems-pane'):
                yield DataTable(id='problems-table', cursor_type='row')
                with Horizontal():
                    yield Button('Open source', id='source')
                    yield Button('Open output', id='problem-output')
                    yield Input(placeholder='Agent pane ID or name', id='agent-target')
                    yield Button('Preview send', id='send')
            with TabPane('Health', id='health-pane'):
                yield DataTable(id='health', cursor_type='row')
                with Horizontal():
                    yield Button('Check health', id='health-check')
                    yield Button('Evidence / next action', id='health-detail')
                    yield Button('Configuration', id='health-config')
                    yield Button('Owner recovery', id='health-recovery')
            with TabPane('Resources', id='resources-pane'):
                yield Static('Host processes only. Unknown attribution is not zero usage. CPU 100% = one core; RSS may include shared pages.', markup=False)
                yield DataTable(id='resources', cursor_type='row')
                with Horizontal():
                    yield Button('Refresh resources', id='resource-check')
                    yield Button('Focus process terminal', id='resource-focus')
            with TabPane('Containers', id='containers-pane'):
                yield Static('Only reviewed project bindings. Aspire children remain controlled by Aspire.', markup=False)
                yield DataTable(id='containers', cursor_type='row')
                with Horizontal():
                    for name, title in [('container-status', 'Status'), ('container-logs', 'Logs'), ('container-start', 'Start'), ('container-stop', 'Stop'), ('container-bind', 'Bind by ID'), ('container-unbind', 'Unbind')]: yield Button(title, id=name)
            with TabPane('Configuration', id='config-pane'):
                yield Static('Local configuration. Review executable argv, health-provider commands and project scope before saving.', markup=False)
                yield TextArea(id='config-text', show_line_numbers=True)
                with Horizontal():
                    yield Button('Reload config', id='config-load')
                    yield Button('Review & save', id='config-save', variant='primary')
        yield Static('Ready. Nothing runs automatically.', id='feedback', markup=False)
        yield Footer()

    def on_mount(self):
        columns = {'commands': ['Name', 'Kind', 'Category', 'Last result'], 'runs': ['Command', 'State', 'Source', 'Readiness', 'Run'], 'problems-table': ['Severity', 'File', 'Line', 'Message'], 'health': ['Check', 'State', 'Next action'], 'resources': ['Workspace', 'Pane', 'Name', 'Attribution', 'CPU %', 'RSS MiB'], 'containers': ['Name', 'Context', 'Owner', 'ID']}
        for name, names in columns.items(): self.query_one('#' + name, DataTable).add_columns(*names)
        self.load_config(); self.refresh_local()
        self.set_interval(2, self.tick)

    def feedback(self, text):
        for widget in self.query('#feedback'): widget.update(diag.safe_text(text))

    def table(self, name, items, values):
        widget = self.query_one('#' + name, DataTable)
        index = widget.cursor_row
        def key(item):
            return item.get('id') or item.get('terminal', {}).get('terminal_id') or (item.get('name'), item.get('file'), item.get('line'), item.get('message'))
        previous = self.rows.get(name, [])
        selected = key(previous[index]) if index < len(previous) else None
        widget.clear(); self.rows[name] = items
        for value in values: widget.add_row(*[Text(diag.safe_text(v)) for v in value])
        if selected is not None: index = next((n for n, item in enumerate(items) if key(item) == selected), index)
        if items: widget.move_cursor(row=min(index, len(items) - 1), animate=False)

    def selected(self, name):
        items = self.rows.get(name, [])
        index = self.query_one('#' + name, DataTable).cursor_row
        if not items or index >= len(items): raise ValueError('Select a row first.')
        return items[index]

    def load_config(self):
        try:
            self.config_before = self.store.config()
            self.query_one('#config-text', TextArea).load_text(json.dumps(self.config_before, indent=2))
        except Exception as e: self.feedback(str(e))

    def config_dirty(self):
        return self.config_before is not None and self.query_one('#config-text', TextArea).text != json.dumps(self.config_before, indent=2)

    def require_saved_config(self):
        if self.config_dirty(): raise ValueError('Save your configuration draft first. Your edits have been preserved.')

    def save_project(self, change, title, detail):
        self.require_saved_config()
        previous = self.store.config(); new = copy.deepcopy(previous)
        project = new['projects'].setdefault(self.root, {'commands': [], 'containers': [], 'health_providers': []})
        change(project)
        def saved(_): self.load_config(); self.refresh_local(); self.feedback('Saved for ' + self.root)
        self.confirm(title + ' — ' + self.root, detail, lambda: self.store.save_config(new, previous), saved)

    def edit_command(self, definition):
        if definition is None: return
        def change(project):
            definitions = project.setdefault('commands', [])
            prior = next((i for i, d in enumerate(definitions) if d['id'] == definition['id']), None)
            if prior is None: definitions.append(definition)
            else: definitions[prior] = definition
        self.save_project(change, 'Save command', json.dumps(definition, indent=2))

    def refresh_local(self):
        try:
            project = self.store.project(self.root)
            runs = [runtime.reconcile(self.store, r) for r in self.store.list(self.root)[:50]]
            definitions = project.get('commands', [])
            self.table('commands', definitions, [(d['name'], d.get('kind', 'command'), d.get('category', 'custom'), next((r['state'] + (' (source unchecked)' if r['state'] == 'passed' else '') for r in runs if r['definition']['id'] == d['id']), 'not-run')) for d in definitions])
            # Cached freshness is deliberately not rendered as current without a fresh check.
            def source(r):
                checked = self.fresh_checks.get(r['id'])
                if checked and checked['updated'] == r['updated']:
                    return f'{checked["freshness"]} as of {time.strftime("%H:%M:%S", time.localtime(checked["checked_at"]))}'
                return 'unchecked — Check freshness' if r['state'] not in ACTIVE else r.get('freshness', 'unknown')
            self.table('runs', runs, [(r['definition']['name'], r['state'] + ' / ' + r.get('problems', {}).get('state', 'unparsed'), source(r), r.get('readiness', 'unknown'), r['id'][:10]) for r in runs])
            bindings = project.get('containers', [])
            self.table('containers', bindings, [(b.get('name', ''), b['context'], b.get('owner', 'unknown'), b['id'][:12]) for b in bindings])
            self.update_controls()
        except Exception as e: self.feedback(str(e))

    def tick(self):
        if self.busy or len(self.screen_stack) > 1 or not self.query('#commands'): return
        if self.query_one(TabbedContent).active == 'resources-pane' and self.host:
            self.perform(lambda: diag.resources(self.host, self.store), self.show_resources)
        else: self.refresh_local()

    def perform(self, fn, callback=None):
        if self.busy: self.feedback('An operation is in progress.'); return
        self.busy = True
        self.feedback('Working…'); self.update_controls()
        return self.perform_worker(fn, callback)

    @work(thread=True, group='operation')
    def perform_worker(self, fn, callback=None):
        try:
            result = fn()
            if self.is_running: self.call_from_thread(callback or self.show_result, result)
        except Exception as e:
            if self.is_running: self.call_from_thread(self.feedback, str(e))
        finally:
            if self.is_running: self.call_from_thread(self.operation_finished)

    def operation_finished(self):
        self.busy = False; self.update_controls()

    def update_controls(self):
        if not self.is_mounted or not self.query('#commands'): return
        def selected(name):
            try: return self.selected(name)
            except ValueError: return {}
        r = selected('runs'); c = selected('commands'); b = selected('containers')
        live = r.get('state') in ('starting', 'running', 'cancelling') and runtime.alive(r.get('supervisor'))
        remaining = max(0, 10 - int(time.time() - r.get('cancel_at', time.time())))
        enabled = {'run': bool(c) and bool(self.host), 'command-edit': bool(c), 'command-remove': bool(c),
                   'cancel': live and not r.get('cancel'), 'force': live and r.get('cancel') and not remaining,
                   'resolve': r.get('state') == 'unknown' and not runtime.alive(r.get('supervisor')) and not runtime.alive(r.get('child')),
                   'rerun': bool(r) and bool(self.host) and not r.get('container_binding') and r.get('state') not in ('starting', 'cancelling', 'unknown') and (r.get('state') not in ACTIVE or r.get('definition', {}).get('kind') == 'service'),
                   'terminal': bool(r.get('locator')) and bool(self.host), 'freshness': bool(r) and r.get('state') not in ACTIVE and not r.get('container_binding'),
                   'output': bool(r), 'problems': bool(r), 'urls': bool(r), 'send-output': bool(r) and bool(self.host),
                   'source': bool(selected('problems-table')), 'problem-output': bool(self.problem_run), 'send': bool(selected('problems-table')) and bool(self.host),
                   'resource-focus': bool(selected('resources')) and bool(self.host), 'health-detail': bool(selected('health')),
                   'health-recovery': bool(selected('health').get('recovery_argv')) and bool(self.host),
                   'container-status': bool(b), 'container-logs': bool(b), 'container-unbind': bool(b),
                   'container-start': bool(b) and b.get('owner') == 'manual', 'container-stop': bool(b) and b.get('owner') == 'manual'}
        for button in self.screen_stack[0].query(Button): button.disabled = self.busy or not enabled.get(button.id, True)
        self.query_one('#force', Button).label = f'Force-stop ({remaining}s)' if live and r.get('cancel') and remaining else 'Force-stop'
        self.query_one('#rerun', Button).label = 'Restart' if r.get('state') == 'running' and r.get('definition', {}).get('kind') == 'service' else 'Rerun'

    def on_data_table_row_highlighted(self, event): self.update_controls()

    def show_result(self, result):
        self.refresh_local()
        if isinstance(result, dict) and result.get('id') and result.get('definition'):
            self.query_one(TabbedContent).active = 'runs-pane'
            row = next((i for i, r in enumerate(self.rows.get('runs', [])) if r['id'] == result['id']), None)
            if row is not None: self.query_one('#runs', DataTable).move_cursor(row=row)
            self.feedback(f'{result["definition"]["name"]}: {result["state"]}. Use Output or Live terminal for details. {result.get("error", "")}')
            return
        self.push_screen(Preview('Result', diag.redact(json.dumps(result, indent=2) if not isinstance(result, str) else result)))

    def confirm(self, title, text, fn, callback=None):
        self.push_screen(Preview(title, diag.redact(text), 'Confirm'), lambda answer: self.perform(fn, callback) if answer is not None else None)

    def action_refresh(self):
        if self.busy or len(self.screen_stack) > 1: return
        self.refresh_local()
        if self.rows.get('runs'):
            r = self.selected('runs')
            if r['state'] not in ACTIVE and not r.get('container_binding'):
                self.perform(lambda: runtime.run_status(self.store, r['id']), self.checked_source)

    def checked_source(self, r):
        self.fresh_checks[r['id']] = r; self.refresh_local()
        self.feedback(f'{r["definition"]["name"]}: {r["state"]}; source {r.get("freshness", "unknown")} checked now. Recheck after edits.')

    def action_quit(self):
        if self.busy: self.feedback('Wait for the current operation before closing.'); return
        if self.config_dirty():
            self.push_screen(Preview('Discard unsaved configuration and close?', self.query_one('#config-text', TextArea).text, 'Discard and close'), lambda answer: self.exit() if answer is not None else None)
        else: self.exit()

    def on_button_pressed(self, event):
        if len(self.screen_stack) > 1: return
        if self.busy: self.feedback('An operation is in progress; wait for its result.'); return
        try: self.handle(event.button.id)
        except Exception as e: self.feedback(str(e))

    def handle(self, action):
        if action == 'refresh': self.action_refresh()
        elif action == 'config-load':
            if self.config_dirty():
                self.push_screen(Preview('Discard unsaved edits and reload? Cancel keeps your draft.', self.query_one('#config-text', TextArea).text, 'Discard and reload'), lambda answer: self.load_config() if answer is not None else None)
            else: self.load_config()
        elif action == 'config-save':
            new = json.loads(self.query_one('#config-text', TextArea).text)
            previous = copy.deepcopy(self.config_before)
            def saved(_):
                self.config_before = copy.deepcopy(new)
                self.show_result('Configuration saved.')
            self.confirm('Save reviewed local configuration', json.dumps(new, indent=2), lambda: self.store.save_config(new, previous), saved)
        elif action == 'discover':
            self.require_saved_config()
            self.perform(lambda: diag.discover(self.root), self.show_discovery)
        elif action in ('command-add', 'command-edit'):
            self.require_saved_config()
            self.push_screen(CommandForm(self.selected('commands') if action == 'command-edit' else None), self.edit_command)
        elif action == 'command-remove':
            d = self.selected('commands')
            self.save_project(lambda p: p.update(commands=[x for x in p['commands'] if x['id'] != d['id']]), 'Remove command', d['name'] + '\nExisting runs and services are retained.')
        elif action == 'run':
            d = self.selected('commands')
            self.review_run(d)
        elif action in ('output', 'problem-output'):
            r = self.problem_run if action == 'problem-output' else self.selected('runs')
            self.perform(lambda: self.output(r), lambda text: self.push_screen(Preview('Run output — ' + r['id'], text)))
        elif action == 'terminal':
            r = self.selected('runs'); self.require_host()
            if not r.get('locator'): raise ValueError('No verified output terminal; use captured Output.')
            self.perform(lambda: self.focus_terminal(r['locator']), lambda _: self.feedback('Focused the selected terminal.'))
        elif action == 'rerun':
            r = self.selected('runs'); self.require_host()
            if r['state'] in ACTIVE:
                self.review_run(self.store.definition(self.root, r['definition']['id']), restart=r['id'])
            else: self.review_run(self.store.definition(self.root, r['definition']['id']))
        elif action == 'freshness':
            r = self.selected('runs'); self.perform(lambda: runtime.run_status(self.store, r['id']), self.checked_source)
        elif action in ('cancel', 'force', 'resolve'):
            r = self.selected('runs')
            self.confirm(action + ': ' + r['definition']['name'], f'Exact run {r["id"]}\nCheckout {r["root"]}\n' + ('Acknowledgement releases the reservation, but does not prove escaped descendants stopped.' if action == 'resolve' else 'Only the verified live supervisor may stop this run.'), lambda: runtime.resolve_interrupted(self.store, r['id']) if action == 'resolve' else runtime.cancel(self.store, r['id'], force=action == 'force'))
        elif action == 'problems':
            r = self.selected('runs')
            self.perform(lambda: runtime.run_status(self.store, r['id']), self.show_problems)
        elif action == 'source':
            p = self.selected('problems-table'); r = self.problem_run
            path, line = diag.source_location(r, p)
            lines = path.read_text(errors='replace').splitlines(); start = max(0, line - 30)
            text = '\n'.join(f'{n + 1:6} {">" if n + 1 == line else " "} {v}' for n, v in enumerate(lines[start:line + 60], start))
            self.push_screen(Preview(f'{path}:{line} — {r.get("freshness", "unknown")} result', diag.safe_text(text)))
        elif action == 'send': self.preview_send()
        elif action == 'send-output': self.preview_send(output=True)
        elif action == 'urls':
            r = self.selected('runs')
            path = self.store.logs / (r['id'] + '.log')
            urls = r['definition'].get('urls', []) + diag.detected_urls(path.read_text(errors='replace') if path.exists() else '')
            text = '\n'.join(dict.fromkeys(urls))
            self.push_screen(Preview('Choose ONE HTTP(S) URL to open; detected URLs are unverified', text, 'Open URL', editable=True), self.open_url)
        elif action == 'health-check': self.perform(lambda: diag.health(self.store, self.root, self.host), self.show_health)
        elif action == 'health-detail':
            c = self.selected('health')
            if c['action'].startswith('Open run '):
                r = self.store.get(c['action'][9:]); self.perform(lambda: self.output(r))
            else:
                self.push_screen(Preview('Health evidence / next action', json.dumps(c, indent=2)))
        elif action == 'health-config': self.query_one(TabbedContent).active = 'config-pane'
        elif action == 'health-recovery':
            self.require_host(); c = self.selected('health'); recovery = c.get('recovery')
            if not recovery: raise ValueError('This owner has not supplied a recovery action. See Evidence / next action.')
            if not c.get('recovery_argv'): raise ValueError('No reviewed, project-scoped recovery adapter is configured. Native module actions cannot safely target this checkout; use the owning module.')
            self.confirm('Invoke this owner-supplied action for ' + self.root, json.dumps({'reference': recovery, 'argv': c['recovery_argv']}, indent=2) + '\n' + c['action'], lambda: self.owner_recovery(recovery, c['recovery_argv']))
        elif action == 'resource-check': self.require_host(); self.perform(lambda: diag.resources(self.host, self.store), self.show_resources)
        elif action == 'resource-focus':
            row = self.selected('resources'); self.require_host()
            def focus():
                locator = self.host.locator(row['pane'])
                if locator['terminal_id'] != row['terminal']['terminal_id']: raise ValueError('Terminal changed. Refresh resources.')
                return self.focus_terminal(locator)
            self.perform(focus, lambda _: self.feedback('Focused the selected terminal.'))
        elif action == 'container-bind':
            self.require_saved_config()
            self.push_screen(Preview('Enter Docker context, container ID, owner (manual or aspire)', '{"context": "default", "id": "", "owner": "aspire"}', 'Inspect binding', editable=True), self.preview_binding)
        elif action == 'container-unbind':
            b = self.selected('containers')
            self.save_project(lambda p: p.update(containers=[x for x in p['containers'] if (x['daemon'], x['id']) != (b['daemon'], b['id'])]), 'Unbind container', json.dumps(b, indent=2) + '\nRemoves this association only; no container is stopped or deleted.')
        elif action.startswith('container-'):
            b = self.selected('containers'); operation = action[10:]
            if operation in ('start', 'stop'): self.confirm(operation + ' exact container', json.dumps(b, indent=2), lambda: diag.recorded_container_action(self.store, self.root, b, operation))
            else: self.perform(lambda: diag.recorded_container_action(self.store, self.root, b, operation))

    def require_host(self):
        if not self.host: raise ValueError('This action needs the selected Luvus session.')

    def focus_terminal(self, locator):
        self.host.validate(locator); return self.host.call('pane.focus', pane=locator['pane_id'])

    def review_run(self, definition, restart=None):
        self.require_host()
        def reviewed(session):
            request = uuid.uuid4().hex
            fn = (lambda: runtime.restart(self.store, self.host, self.root, restart, definition, session, request)) if restart else (lambda: runtime.launch(self.store, self.host, self.root, definition['id'], request, definition, session))
            self.confirm(('Restart in ' if restart else 'Run in ') + self.root, json.dumps(definition, indent=2) + ('\nStop the selected service, wait for confirmed exit, then start this definition. No automatic force-stop.' if restart else ''), fn)
        self.perform(lambda: runtime.session_identity(self.host), reviewed)

    def owner_recovery(self, recovery, argv):
        info = self.host.call('module.info', id=recovery['module'])
        if not info.get('runnable'): raise ValueError('Owning module is unavailable.')
        # Execute only an explicitly configured argv adapter, with target data on stdin.
        result = diag.capture(argv, input=json.dumps({'version': 1, 'root': self.root, 'action': recovery['action']}) + '\n', timeout=30)
        if result.returncode: raise ValueError(diag.redact(result.stderr[:4000]))
        return diag.redact(result.stdout[:8000])

    def output(self, r):
        r = runtime.run_status(self.store, r['id'])
        path = self.store.logs / (r['id'] + '.log')
        text = path.read_bytes()[:LOG_LIMIT].decode('utf-8', 'replace') if path.exists() else '(No captured output.)'
        return diag.redact(f'{r["state"]}; source={r.get("freshness", "unknown")}; run={r["id"]}\nTruncated={r.get("truncated", False)}\n{r.get("error", "")}\n\n' + text)

    def show_discovery(self, suggestions):
        existing = {d['id'] for d in self.store.project(self.root).get('commands', [])}
        choices = [d for d in suggestions if d['id'] not in existing]
        if not choices: self.feedback('No new suggestions. Use Add to define a command.'); return
        def selected(definitions):
            if definitions:
                self.save_project(lambda p: p.setdefault('commands', []).extend(definitions), 'Add selected commands', json.dumps(definitions, indent=2))
        self.push_screen(DiscoveryPicker(choices), selected)

    def show_problems(self, r):
        self.problem_run = r
        parsed = r.get('problems') or diag.parse_run(self.store, r)
        self.table('problems-table', parsed['items'], [(p['severity'], p['file'], p['line'], p['message'][:250]) for p in parsed['items']])
        self.query_one(TabbedContent).active = 'problems-pane'
        self.feedback(f'Run {r["id"]}: {r["state"]}; source={r.get("freshness", "unknown")}; parser={parsed["state"]}; truncated={parsed.get("truncated")}; {parsed.get("error", "")}')

    def show_health(self, checks): self.table('health', checks, [(c['name'], c['state'], c['action']) for c in checks])

    def show_resources(self, rows):
        values = []
        for r in rows:
            cpu = '?'; key = r['terminal']['terminal_id']; previous = self.cpu_samples.get(key)
            if r.get('cpu_seconds') is not None:
                if previous and r['at'] > previous[0] and r['process_times'].keys() == previous[1].keys():
                    cpu = f'{sum(max(0, value - previous[1][pid]) for pid, value in r["process_times"].items()) / (r["at"] - previous[0]) * 100:.1f}'
                self.cpu_samples[key] = (r['at'], r['process_times'])
            values.append((json.dumps(r['workspace']), r['pane'], r['name'] or '', r['state'], cpu, '?' if r['rss_bytes'] is None else f'{r["rss_bytes"] / 1048576:.1f}'))
        self.table('resources', rows, values)

    def open_url(self, answer):
        if answer is None: return
        from urllib.parse import urlparse
        u = urlparse(answer.strip())
        if '\n' in answer.strip() or u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            self.feedback('Enter exactly one HTTP(S) URL without embedded credentials.'); return
        webbrowser.open(answer.strip())

    def preview_binding(self, answer):
        if answer is None: return
        try:
            d = json.loads(answer)
            if d.get('owner') not in ('manual', 'aspire'): raise ValueError('Owner must be manual or aspire.')
            def inspected(b):
                b['owner'] = d['owner']
                def change(project):
                    bindings = project.setdefault('containers', [])
                    if any(x['daemon'] == b['daemon'] and x['id'] == b['id'] for x in bindings): raise ValueError('Container already bound. Unbind it before changing ownership.')
                    bindings.append(b)
                self.save_project(change, 'Bind this exact container', json.dumps(b, indent=2))
            self.perform(lambda: diag.inspect_container(d['context'], d['id']), inspected)
        except Exception as e: self.feedback(str(e))

    def preview_send(self, output=False):
        self.require_host()
        r = self.selected('runs') if output else self.problem_run
        p = None if output else self.selected('problems-table')
        target = self.query_one('#run-agent-target' if output else '#agent-target', Input).value.strip()
        if not target: raise ValueError('Enter an explicit agent pane ID or name.')
        def prepare():
            current = runtime.run_status(self.store, r['id'])
            agent = self.host.call('agent.get', target=target)
            pane = str(agent.get('pane', agent.get('id', '')))
            locator = self.host.locator(pane)
            excerpt = self.output(current)[-8000:] if output else f'{p["file"]}:{p["line"]}\n{p["message"]}'
            text = diag.redact(f'Validation context in {current["root"]}\nRun {current["id"]}; result {current["state"]}; freshness {current.get("freshness", "unknown")}\nSelected excerpt (may be truncated):\n{excerpt}')
            return agent, pane, locator, text
        def preview(prepared):
            agent, pane, locator, text = prepared
            def send(answer):
                if answer is None: return
                def dispatch():
                    if len(answer) > 32000: raise ValueError('Failure excerpt exceeds 32,000 characters.')
                    self.host.validate(locator)
                    fresh = self.host.call('agent.get', target=target)
                    if fresh != agent: raise ValueError('Agent identity/state changed since preview. Preview again.')
                    return self.host.call('agent.send', target=pane, text=answer)
                self.perform(dispatch)
            self.push_screen(Preview('Review exact text and target: ' + target, text, 'Send to this agent', editable=True), send)
        self.perform(prepare, preview)
