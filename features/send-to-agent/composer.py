"""One keyboard-accessible composer, backed by local draft snapshots."""
import asyncio
import json
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Select, Static, TextArea, Collapsible

from send_agent import Error, Host, Store, deliver, file_item, files, identity, message, text_item, validate_context, validate_target


class Composer(App):
    TITLE = "Send to agent"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("ctrl+q", "quit", "Save & close"), ("ctrl+r", "review", "Preview")]
    CSS = """
    Screen { background: $surface; }
    VerticalScroll { padding: 0 1; }
    Label { margin-top: 0; }
    Input, Select { width: 1fr; }
    TextArea { height: 7; }
    Input, TextArea, Select > SelectCurrent, Select > SelectOverlay {
        border: solid $border-blurred;
    }
    Input:focus, TextArea:focus, Select:focus > SelectCurrent, Select > SelectOverlay:focus {
        border: solid $primary;
    }
    Input.-invalid, Input.-invalid:focus { border: solid $error; }
    #preview { height: 1fr; min-height: 8; }
    #form { height: 1fr; }
    Collapsible { padding: 0; }
    #actions { padding: 0 1; }
    #source { padding: 0 2; }
    Horizontal { height: auto; }
    Horizontal > Button { margin: 1 1 0 0; }
    #status { padding: 0 2; color: $warning; max-height: 4; overflow-y: auto; }
    #source, #target-info { height: auto; }
    #instruction { height: 3; }
    #item-preview { height: 3; }
    #instruction-error, #target-error { height: auto; color: $error; }
    .small { width: 1fr; }
    """

    def __init__(self, root, ident, host=None):
        super().__init__()
        self.store = Store(root)
        self.record = self.store.get(ident)
        self.host_override = host
        self.host = host or Host(self.record["session_env"])
        self.agents = []
        self.checkout_badges = {}
        self.kinds = []
        self.loading = True
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="source", markup=False)
        with VerticalScroll(id="form"):
            yield Label("Context")
            yield Select([], id="items", prompt="Captured files and text")
            yield TextArea(read_only=True, id="item-preview")
            with Collapsible(title="Add context…", id="context-extra"):
                yield Button("Remove selected item", id="remove")
                yield Label("Additional text · included automatically")
                yield TextArea(id="quote")
                yield Select([], id="files", prompt="Choose a file, or enter a relative path")
                yield Input(placeholder="path/to/file.py", id="path")
                yield Select([("File content", "file"), ("Unstaged change", "unstaged"), ("Staged change", "staged")], value="file", allow_blank=False, id="layer")
                with Horizontal():
                    yield Input(placeholder="First line", id="start")
                    yield Input(placeholder="Last line", id="end")
                yield Button("Add file / lines / change", id="add-file")
            yield Label("What should the agent do?")
            yield TextArea(id="instruction")
            yield Static("", id="instruction-error", markup=False)
            yield Label("Recipient")
            yield Select([], id="target", prompt="Choose an agent")
            yield Static("", id="target-error", markup=False)
            yield Select([], id="kind", prompt="Provider for new agent")
            yield Static("", id="target-info", markup=False)
            yield Button("Refresh / Retry", id="refresh")
            with Collapsible(title="Message preview", id="preview-section"):
                yield Static("", id="review-target", markup=False)
                yield TextArea(read_only=True, id="preview")
            with Collapsible(title="History…", id="history-section"):
                yield Select([], id="history", prompt="Saved drafts and outcomes")
                with Horizontal():
                    yield Button("Load selected", id="load")
                    yield Button("New draft", id="new")
                yield Static("", id="delivery-info", markup=False)
                yield Checkbox("I inspected the agent/pane and resolved this delivery manually", id="inspected")
                yield Button("Close uncertain outcome without retry", id="resolve")
        yield Static("", id="status", markup=False)
        with Horizontal(id="actions"):
            yield Button("Send", id="send", variant="primary")
            yield Button("Preview", id="review")
            yield Button("Save & close", id="close")
        yield Footer()

    async def on_mount(self):
        import os
        from tab_titles import remember
        if os.environ.get("LUVUS_PANE_ID"):
            await asyncio.to_thread(remember, self.host.call, os.environ["LUVUS_PANE_ID"], "↗ Send to agent")
        self.paint()
        self.loading = False
        self.refresh_inventory()

    def status(self, text):
        self.query_one("#status", Static).update(text)

    def paint(self):
        self.loading = True
        self.query_one("#source", Static).update("Source checkout: " + self.record["cwd"] + "\n" + self.record["created"] + " · " + self.record["state"])
        self.query_one("#history", Select).set_options([(r["created"] + " · " + r["state"] + " · " + Path(r["cwd"]).name + " · " + r["id"][:6], r["id"]) for r in self.store.records()])
        self.query_one("#instruction", TextArea).load_text(self.record["instruction"])
        self.query_one("#quote", TextArea).load_text(self.record.get("quote_draft", ""))
        self.query_one("#preview", TextArea).load_text(self.record.get("prompt", ""))
        self.paint_items()
        self.query_one("#send", Button).disabled = self.busy or self.record["state"] != "draft"
        self.query_one("#inspected", Checkbox).value = False
        uncertain = self.record["state"] in ("pending", "uncertain")
        self.query_one("#inspected").display = uncertain
        self.query_one("#resolve").display = uncertain
        target = self.record.get("target") or {}
        pane = target.get("pane") or self.record.get("launch", {}).get("pane_id", "not created")
        self.query_one("#delivery-info", Static).update("Delivery target: " + str(target.get("name") or self.record.get("name") or "new agent") + " · pane " + str(pane) + "\n" + self.record.get("outcome", "Not submitted"))
        self.query_one("#review-target", Static).update("Recipient: " + str(target.get("name") or self.record.get("name") or "new agent") + " · " + str(target.get("agent") or self.record["kind"]) + "\n" + str(target.get("cwd") or self.record["cwd"]))
        self.status(self.record.get("outcome", "Draft saved locally. Cancel / close sends nothing."))
        self.loading = False

    def paint_items(self):
        self.query_one("#items", Select).set_options([(f"{i+1}. {x['label']}", str(i)) for i, x in enumerate(self.record["items"])])
        if self.record["items"]:
            self.query_one("#items", Select).value = "0"
        else:
            self.query_one("#item-preview", TextArea).load_text("")

    def save(self):
        if self.record["state"] == "draft":
            self.record["instruction"] = self.query_one("#instruction", TextArea).text
            self.record["quote_draft"] = self.query_one("#quote", TextArea).text
            self.store.save(self.record)

    def invalidate(self):
        self.query_one("#instruction-error", Static).update("")
        self.query_one("#target-error", Static).update("")
        self.query_one("#send", Button).disabled = self.busy or self.record["state"] != "draft"
        if not self.loading and not self.busy:
            try:
                self.save()
                if self.record["state"] == "draft":
                    self.status("Draft saved.")
            except Exception as exc:
                self.status("Save failed: " + str(exc))

    @work(exclusive=True, group="inventory")
    async def refresh_inventory(self):
        if self.busy:
            return
        self.invalidate()
        ident = self.record["id"]
        host, cwd = self.host, self.record["cwd"]
        try:
            agents, kinds = await asyncio.to_thread(lambda: (host.agents(), host.discover()))
            from checkout_state import observe
            def badges():
                result = {}
                for path in {cwd, *(a['cwd'] for a in agents)}:
                    try:
                        result[path] = observe(path)['badge']
                    except Exception:
                        result[path] = 'checkout unknown'
                return result
            observed = await asyncio.to_thread(badges)
            if ident != self.record["id"]:
                return
            self.checkout_badges = observed
            self.query_one('#source', Static).update('Source: ' + cwd + '\n' + observed[cwd])
            self.agents = sorted(agents, key=lambda a: (Path(a["cwd"]).resolve() != Path(cwd).resolve(), a.get("name") or "", str(a["pane"])))
            self.kinds = kinds
            options = [(f"{a.get('name') or a['pane']} · {a.get('agent')} · {a.get('status', 'unknown')} · {a['cwd']}", str(i)) for i, a in enumerate(self.agents)]
            self.query_one("#target", Select).set_options([("New agent in source checkout", "new"), *options])
            target = self.record.get("target")
            value = next((str(i) for i, a in enumerate(self.agents) if target and identity(a) == identity(target)), None)
            self.query_one("#target", Select).value = value if value is not None else Select.NULL if target else "new"
            self.query_one("#kind", Select).set_options([(k, k) for k in kinds])
            kind = self.record["kind"]
            self.query_one("#kind", Select).value = kind if kind in kinds else kinds[0] if kinds else Select.NULL
            self.target_info()
        except Exception as exc:
            self.agents = []
            self.query_one("#target", Select).value = Select.NULL
            self.status(str(exc))
        try:
            paths = await asyncio.to_thread(files, cwd)
            if ident == self.record["id"]:
                self.query_one("#files", Select).set_options([(p, p) for p in paths])
        except (Error, UnicodeError):
            if ident == self.record["id"]:
                self.query_one("#files", Select).set_options([])

    def target_info(self):
        value = self.query_one("#target", Select).value
        if value == "new":
            others = [a for a in self.agents if Path(a["cwd"]).resolve() == Path(self.record["cwd"]).resolve()]
            names = ", ".join(str(a.get("name") or a["pane"]) for a in others)
            text = "Fresh conversation in " + self.record["cwd"] + ". Uses its current uncommitted changes."
            if others:
                text += "\nAgents already sharing this checkout: " + names
        elif value is Select.NULL:
            text = "Select a recipient. A missing conversation is never redirected."
        else:
            a = self.agents[int(value)]
            text = "Existing conversation in " + a["cwd"]
            if Path(a["cwd"]).resolve() != Path(self.record["cwd"]).resolve():
                text += "\nDifferent checkout: the recipient gets quoted source snapshots, not copied working files."
        selected = self.record['cwd'] if value == 'new' else self.agents[int(value)]['cwd'] if value is not Select.NULL else ''
        text += '\n' + self.checkout_badges.get(selected, 'checkout unknown')
        self.query_one("#target-info", Static).update(text)
        self.query_one("#kind").display = value == "new"

    def on_text_area_changed(self, event):
        if event.text_area.id in ("instruction", "quote") and not self.loading:
            self.invalidate()

    def on_select_changed(self, event):
        if event.value != event.select.value:
            return
        if event.select.id == "files" and event.value is not Select.NULL:
            self.query_one("#path", Input).value = str(event.value)
        elif event.select.id == "items":
            item = "" if event.value is Select.NULL else self.record["items"][int(event.value)]["text"]
            self.query_one("#item-preview", TextArea).load_text(item)
        elif event.select.id in ("target", "kind") and not self.loading:
            # Persist selection, but never substitute a missing existing target with a new agent.
            value = self.query_one("#target", Select).value
            if value == "new":
                self.record["target"] = None
            elif value is not Select.NULL and int(value) < len(self.agents):
                self.record["target"] = self.agents[int(value)]
            kind = self.query_one("#kind", Select).value
            if kind is not Select.NULL:
                self.record["kind"] = str(kind)
            self.target_info()
            self.invalidate()

    async def action_quit(self):
        if self.busy:
            self.status("Delivery is running. Wait for its outcome before closing.")
            return
        try:
            self.save()
            self.exit()
        except Exception as exc:
            self.status("Could not save draft: " + str(exc))

    def action_review(self):
        self.review()

    @work(exclusive=True, group="review")
    async def review(self):
        if self.busy:
            return
        self.invalidate()
        try:
            if self.record["state"] != "draft":
                raise Error("This record is already submitted; inspect its outcome below.")
            if self.query_one("#target", Select).value is Select.NULL:
                raise Error("Choose an available recipient.")
            self.save()
            text = message(self.outgoing())
            # Freeze this review; changes during host reads invalidate its result.
            snapshot = json.loads(json.dumps(self.record))
            selection = (self.query_one("#target", Select).value, self.query_one("#kind", Select).value)
            await asyncio.to_thread(validate_context, snapshot)
            if snapshot.get("target"):
                await asyncio.to_thread(validate_target, self.host, snapshot["target"])
            elif snapshot["kind"] not in self.kinds:
                raise Error("Choose an installed provider for the new agent.")
            if snapshot != self.record:
                raise Error("Draft changed while preparing preview. Try again.")
            target = self.record.get("target") or {}
            self.query_one("#review-target", Static).update("Recipient: " + str(target.get("name") or target.get("pane") or "new agent") + " · " + str(target.get("agent") or self.record["kind"]) + "\n" + str(target.get("cwd") or self.record["cwd"]) + "\n" + str(self.query_one("#target-info", Static).render()))
            self.query_one("#preview", TextArea).load_text(text)
            self.query_one("#preview-section", Collapsible).collapsed = False
            self.query_one("#send", Button).disabled = False
            self.status("Preview ready. Send will validate and submit once.")
        except Exception as exc:
            self.status(str(exc))

    def outgoing(self):
        record = json.loads(json.dumps(self.record))
        quote = self.query_one("#quote", TextArea).text
        if quote.strip():
            record["items"].append(text_item(quote))
            record["quote_draft"] = ""
        return record

    @work(group="delivery")
    async def send(self):
        if self.busy or self.record["state"] != "draft":
            return
        try:
            self.save()
            if self.query_one("#target", Select).value is Select.NULL:
                self.query_one("#target-error", Static).update("Choose a recipient, or use Refresh / Retry.")
                self.query_one("#target").focus()
                raise Error("Choose an available recipient. Use Refresh / Retry if agents could not be loaded.")
            snapshot = self.outgoing()
            text = message(snapshot)
        except Exception as exc:
            self.status(str(exc))
            if not self.record["instruction"].strip():
                self.query_one("#instruction-error", Static).update("Enter what you want the agent to do.")
                self.query_one("#instruction").focus()
            return
        self.busy = True
        for widget in self.query("Button, Input, Select, TextArea, Checkbox"):
            widget.disabled = True
        self.status("Checking and sending…")
        host, root = self.host, self.store.root
        def dispatch():
            from checkout_state import review as checkout_review
            snapshot["reviewed_checkouts"] = checkout_review(snapshot)
            store = Store(root)
            try:
                return deliver(store, host, snapshot, text)
            finally:
                store.db.close()
        error = None
        try:
            await asyncio.to_thread(dispatch)
        except Exception as exc:
            error = str(exc)
        finally:
            self.record = self.store.get(snapshot["id"])
            self.busy = False
            for widget in self.query("Button, Input, Select, TextArea, Checkbox"):
                widget.disabled = False
            self.paint()
            if error:
                self.status(error)

    @work(group="actions", exclusive=True)
    async def on_button_pressed(self, event):
        action = event.button.id
        if self.busy:
            return
        try:
            if action == "close":
                await self.action_quit()
            elif action == "refresh":
                self.refresh_inventory()
            elif action == "review":
                self.review()
            elif action == "send":
                self.send()
            elif action in ("new", "load"):
                self.save()
                if action == "new":
                    record = self.store.create({"pane": {"cwd": self.record["cwd"]}}, self.record["session_env"])
                else:
                    ident = self.query_one("#history", Select).value
                    if ident is Select.NULL:
                        raise Error("Choose a saved draft first.")
                    record = self.store.get(str(ident))
                self.record = record
                self.host = self.host_override or Host(record["session_env"])
                self.paint()
                self.refresh_inventory()
            elif action == "resolve":
                if not self.query_one("#inspected", Checkbox).value:
                    raise Error("Inspect the recorded agent/pane and check the acknowledgement first.")
                if self.record["state"] not in ("pending", "uncertain"):
                    raise Error("This record has no uncertain outcome.")
                self.store.transition(self.record, self.record["state"], "resolved", outcome="Manually inspected; closed without retry.")
                self.paint()
            elif action in ("add-file", "remove"):
                if self.record["state"] != "draft":
                    raise Error("Create a new draft to compose another message.")
                if action == "add-file":
                    first = self.query_one("#start", Input).value.strip()
                    last = self.query_one("#end", Input).value.strip()
                    item = await asyncio.to_thread(file_item, self.record["cwd"], self.query_one("#path", Input).value,
                                                   str(self.query_one("#layer", Select).value), int(first) if first else None, int(last) if last else None)
                    self.record["items"].append(item)
                else:
                    selected = self.query_one("#items", Select).value
                    if selected is Select.NULL:
                        raise Error("Select a context item first.")
                    self.record["items"].pop(int(selected))
                self.paint_items()
                self.invalidate()
        except Exception as exc:
            self.status(str(exc))

    def on_unmount(self):
        self.store.db.close()
