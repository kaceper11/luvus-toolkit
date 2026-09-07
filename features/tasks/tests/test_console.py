import asyncio
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from textual.widgets import Checkbox, DataTable, Input, Select, SelectionList, Static, TabbedContent, TextArea

from luvus_tasks.core import Store, TaskError, default_config, ticket_key
from luvus_tasks.console import Cockpit, Form, InlineForm, Preview, RepositorySelection
from luvus_tasks import operations as ops
from luvus_tasks.handover import save_record


async def wait_until(pilot, predicate):
    for _ in range(100):
        if predicate():
            await pilot.pause()
            return
        await pilot.pause(0.05)
    raise AssertionError("UI did not reach the expected state within five seconds")


TICKET = {"provider": "Azure DevOps", "connection": "azure", "project": "Example", "id": "1", "key": "AB#1",
          "title": "Fix [red]literal markup[/red]", "status": "Active", "description": "Description", "acceptance": "Tests pass",
          "url": "https://dev.azure.com/example/Example/_workitems/edit/1", "rev": 1}
CONNECTION = {"id": "azure", "provider": "azure", "organization": "example", "project": "Example"}


class Host:
    def available_agents(self):
        return {"Codex": "codex"}

    def call(self, method, **params):
        if method == "terminal.backend.inventory":
            return {"server_generation": "g", "terminals": []}
        if method == "task.list":
            return {"tasks": []}
        if method == "lease.list":
            return {"leases": []}
        raise TaskError("Unsupported test method: " + method)

    def agents(self):
        return []

    def capabilities(self):
        return {"method_contracts": [{"method": m} for m in ("task.list", "lease.list", "task.add", "task.claim", "task.done")]}


class OperationTests(unittest.TestCase):
    def setUp(self):
        naming = patch("luvus_tasks.tab_titles.remember", return_value=True)
        naming.start()
        self.addCleanup(naming.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        config = default_config()
        config["connections"] = [copy.deepcopy(CONNECTION)]
        self.store.save_config(config)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_session_scoped_inbox_does_not_consume_other_sessions_or_legacy(self):
        self.store.put("inbox", "1", {"session": "a", "action": "open"})
        self.store.put("inbox", "2", {"session": "b", "action": "open"})
        self.store.put("inbox", "3", {"ticket": "legacy"})
        self.assertEqual(len(self.store.take_actions("a")), 1)
        self.assertEqual(len(self.store.records("inbox")), 2)

    def test_preferences_roundtrip(self):
        self.store.set_preference("favorites", ["azure:1"])
        self.assertEqual(self.store.preference("favorites"), ["azure:1"])

    def test_standalone_uses_native_module_config_directory(self):
        with patch.dict(os.environ, {"LUVUS_HOME": self.tmp.name}, clear=True):
            store = Store()
            self.assertEqual(store.root, Path(self.tmp.name) / "modules/config/kacper.toolkit/features/tasks")
            store.db.close()

    def test_session_socket_symlink_path_normalization(self):
        with patch.dict(os.environ, {"LUVUS_HOME": self.tmp.name}, clear=True):
            plain = ops.session_key()
            os.environ["LUVUS_SOCKET_PATH"] = str(Path(self.tmp.name) / "luvus.sock")
            self.assertEqual(ops.session_key(), plain)

    def test_refresh_preserves_filter_membership_and_offline_cache(self):
        config = self.store.config()
        config["connections"][0]["filters"] = [{"name": "One", "query": "a"}, {"name": "Two", "query": "b"}]
        self.store.save_config(config)
        factory = Mock()
        factory.return_value.query.return_value = [TICKET]
        tasks, errors = ops.refresh(self.store, True, factory)
        self.assertEqual(tasks[0]["views"], ["azure / One", "azure / Two"])
        self.assertFalse(errors)
        factory.return_value.query.side_effect = TaskError("offline")
        tasks, errors = ops.refresh(self.store, True, factory)
        self.assertTrue(tasks[0]["stale"])
        self.assertEqual(len(errors), 2)

    def test_open_reuses_only_exact_terminal_generation(self):
        host = Mock()
        inventory = {"server_generation": "g", "terminals": [{"terminal_id": "t", "pane_id": "4"}]}
        host.call.return_value = inventory
        self.store.set_preference("console:" + ops.session_key(), {"pane": "4", "terminal": "t", "generation": "g"})
        ops.open_console(self.store, host, ticket="azure:1")
        host.call.assert_called_with("pane.focus", pane="4")
        self.assertEqual(self.store.take_actions(ops.session_key())[0]["ticket"], "azure:1")

    def test_open_does_not_focus_recycled_pane(self):
        host = Mock()
        inventory = {"server_generation": "new", "terminals": [{"terminal_id": "other", "pane_id": "4"}]}
        host.call.side_effect = [inventory, {"pane": "5"}, inventory]
        self.store.set_preference("console:" + ops.session_key(), {"pane": "4", "terminal": "t", "generation": "old"})
        ops.open_console(self.store, host)
        self.assertNotIn("pane.focus", [c.args[0] for c in host.call.call_args_list])

    def test_status_conflict_prevents_write(self):
        with patch.object(ops, "provider") as factory:
            factory.return_value.get.return_value = {**TICKET, "rev": 2}
            with self.assertRaisesRegex(TaskError, "changed"):
                ops.transition(self.store, TICKET, {"name": "Done"}, {})
            factory.return_value.transition.assert_not_called()

    def test_uncertain_status_not_retried(self):
        with patch.object(ops, "provider") as factory:
            factory.return_value.get.return_value = TICKET
            factory.return_value.transition.side_effect = TaskError("lost response")
            for _ in range(2):
                with self.assertRaises(TaskError):
                    ops.transition(self.store, TICKET, {"name": "Done"}, {})
            factory.return_value.transition.assert_called_once()

    def test_comment_duplicate_prevented(self):
        with patch.object(ops, "provider") as factory:
            factory.return_value.comments.side_effect = lambda t: [{"text": self.store.records("writes")[0]["text"]}]
            ops.publish_comment(self.store, TICKET, "Progress")
            ops.publish_comment(self.store, TICKET, "Progress")
            factory.return_value.comment.assert_called_once()

    def test_orch_lost_create_not_repeated(self):
        record = ops.draft(self.store, TICKET)
        host = Host()
        original = host.call
        host.call = Mock(side_effect=lambda m, **p: (_ for _ in ()).throw(TaskError("lost response")) if m == "task.add" else original(m, **p))
        for _ in range(2):
            with self.assertRaises(TaskError):
                ops.link_orch(self.store, host, record)
        self.assertEqual(sum(c.args[0] == "task.add" for c in host.call.call_args_list), 1)

    def test_browser_rejects_credentials_and_unsafe_schemes(self):
        for url in ("file:///tmp/a", "javascript:alert(1)", "https://user:secret@example.com"):
            with self.assertRaises(TaskError):
                ops.browser_url(url)

    def test_orch_gate_requires_exact_claimed_worker(self):
        record = ops.draft(self.store, TICKET)
        record.update(orch_id="t1", pane="4", target={"path": "/tmp/reviewed"})
        task = {"id": "t1", "gate": "run-checks", "assignee": None}
        host = Mock()
        host.call.return_value = {"task": task}
        with self.assertRaisesRegex(TaskError, "Claim the task"):
            ops.orch_action(self.store, host, record, task, "done")
        self.assertNotIn("task.done", [c.args[0] for c in host.call.call_args_list])

    def test_orch_claim_explicitly_acquires_path_leases(self):
        record = ops.draft(self.store, TICKET)
        record.update(orch_id="t1", pane="4")
        task = {"id": "t1", "assignee": None, "paths": ["src/**"]}
        host = Mock()
        host.call.side_effect = lambda method, **params: {"task": task}
        with patch.object(ops, "orch_snapshot", return_value={"tasks": [task], "leases": {"leases": []}}), patch.object(ops, "live_agent", return_value={"pane": "4"}):
            ops.claim_orch(self.store, host, record)
        self.assertIn(unittest.mock.call("lease.acquire", task="t1", pane="4", paths=["src/**"]), host.call.call_args_list)

    def test_diff_wrong_checkout_does_not_open_file(self):
        host = Mock()
        host.call.return_value = {"repo": "/tmp/unrelated"}
        with self.assertRaisesRegex(TaskError, "different checkout"):
            ops.review_diff(host, {"pane": "4", "target": {"path": "/tmp/reviewed"}})
        self.assertNotIn("diff.open", [c.args[0] for c in host.call.call_args_list])

    def test_remote_mapping_suggestion_does_not_change_config(self):
        config = self.store.config()
        config["connections"].append({"id": "gh", "provider": "github", "repository": "example/repo"})
        before = copy.deepcopy(config)
        with patch.object(ops, "git", return_value="git@github.com:example/repo.git"):
            self.assertEqual(ops.suggested_connections(config, "/repo"), ["gh"])
        self.assertEqual(config, before)


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Cockpit(self.tmp.name, Host(), network=False)
        config = default_config()
        config["connections"] = [copy.deepcopy(CONNECTION)]
        self.app.store.save_config(config)
        self.app.store.put("cache", "azure:" + hashlib.sha256(b"").hexdigest(), [TICKET])

    async def asyncTearDown(self):
        self.app.store.db.close()
        self.tmp.cleanup()

    async def test_tabs_mouse_navigation_and_literal_issue_text(self):
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(self.app.query_one("#issues-table", DataTable).row_count, 1)
            self.assertEqual(self.app.selected_issue, "azure:1")
            await pilot.click("#--content-tab-configuration")
            await pilot.click("#connections")
            self.assertNotIsInstance(self.app.screen, Form)
            self.assertTrue(self.app.query(InlineForm))
            await wait_until(pilot, lambda: bool(self.app.query("#cancel")))
            await pilot.click("#cancel")
            await pilot.pause()
            self.assertFalse(self.app.busy)

    async def test_narrow_layout_and_search_keyboard(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertTrue(self.app.has_class("narrow"))
            await pilot.press("ctrl+f")
            await pilot.press("z", "z", "z")
            self.assertEqual(self.app.query_one("#issues-table", DataTable).row_count, 0)

    async def test_empty_handover_explains_next_steps_and_navigates_without_creating_draft(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            tabs = self.app.query_one("#tabs", TabbedContent)
            await pilot.click("#--content-tab-handover")
            await pilot.pause()
            self.assertTrue(self.app.query_one("#handover-empty-title", Static).is_on_screen)
            await pilot.click("#handover-choose-issue")
            await pilot.pause()
            self.assertEqual(tabs.active, "issues")
            await pilot.click("#--content-tab-handover")
            await pilot.pause()
            await pilot.click("#handover-browse-drafts")
            await pilot.pause()
            self.assertEqual(tabs.active, "handovers")
            self.assertEqual(self.app.query_one("#history-filter", Select).value, "draft")
            self.assertEqual(self.app.store.records("handovers"), [])

    async def test_evidence_and_attention_narrow_navigation_and_cancelled_pr(self):
        from luvus_tasks import workflow_ui
        record = ops.draft(self.app.store, copy.deepcopy(TICKET))
        record["target"] = {"repo": self.tmp.name, "path": self.tmp.name, "branch": "feature", "base": "main"}
        record["forge"] = {"kind": "azure", "repository": "test", "connection": "azure"}
        save_record(self.app.store, record)
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.app.selected_handover = record["id"]
            self.app.query_one("#tabs", TabbedContent).active = "handovers"
            self.app.paint_work()
            await pilot.pause()
            self.assertTrue(self.app.query_one("#handover-text", Preview).is_on_screen)
            self.assertIn("Branch:", self.app.query_one("#handover-text", Preview).text)
            with patch.object(workflow_ui.forge, "preferred", return_value=record["forge"]), patch.object(self.app, "form", new=AsyncMock(return_value=None)), patch.object(workflow_ui.forge, "publish") as publish:
                await workflow_ui.action(self.app, "work-pr-create", record)
            publish.assert_not_called()
            self.app.query_one("#tabs", TabbedContent).active = "attention"
            await pilot.pause()
            self.assertTrue(self.app.query_one("#attention-search", Input).is_on_screen)

    async def test_sidebar_pr_shortcut_routes_to_captured_handover_without_resume(self):
        from luvus_tasks import workflow_ui
        record = ops.draft(self.app.store, copy.deepcopy(TICKET))
        async with self.app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            with patch.object(workflow_ui, "action", new=AsyncMock()) as action, patch.object(self.app, "history_action", new=AsyncMock()) as resume:
                await self.app.inbox_action({"action": "open", "ticket": "dashboard:" + json.dumps({"record": record["id"], "action": "work-pr-comments"})})
            self.assertEqual(action.call_args.args[1], "work-pr-comments")
            self.assertEqual(action.call_args.args[2]["id"], record["id"])
            resume.assert_not_called()

    async def test_work_empty_state_and_cached_navigation_do_not_fetch_or_resume(self):
        from luvus_tasks import workflow_ui
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.app.selected_handover = None
            self.app.paint_work()
            self.assertFalse(self.app.query("#work"))
            self.assertFalse(self.app.query("#orchestration"))
            self.assertIn("Select a handover", self.app.query_one("#handover-text", Preview).text)
            record = ops.draft(self.app.store, copy.deepcopy(TICKET))
            with patch.object(workflow_ui, "action", new=AsyncMock()) as action, patch.object(self.app, "history_action", new=AsyncMock()) as resume:
                await self.app.inbox_action({"action": "open", "ticket": "dashboard:" + json.dumps({"record": record["id"], "action": "work-pr-details"})})
            action.assert_not_called()
            resume.assert_not_called()
            self.assertTrue(self.app.query_one("#work-linked-prs").display)

    async def test_form_controls_keep_solid_borders_when_focused_and_selected(self):
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            form = Form("Border regression", [
                ("line", "Single line", "Select this text", "input"),
                ("multi", "Multiple lines", "First line\nSecond line", "text"),
                ("pick", "Dropdown", "one", ["one", "two"]),
                ("check", "Checkbox", False, "bool")], message="Selectable preview")
            await self.app.push_screen(form)
            await form.query_one(".form-body").mount(SelectionList(("Repository", "repo", True)))
            def assert_solid():
                for widget in form.query("Input, TextArea, SelectCurrent, SelectOverlay, Checkbox, SelectionList"):
                    for edge in ("top", "right", "bottom", "left"):
                        self.assertEqual(getattr(widget.styles, "border_" + edge)[0], "solid", (type(widget).__name__, edge))
            assert_solid()
            for widget in form.query("Input, TextArea, Select, Checkbox, SelectionList"):
                widget.focus()
                await pilot.pause()
                if isinstance(widget, (Input, TextArea)):
                    widget.action_select_all()
                assert_solid()
            form.query_one(Select).focus()
            await pilot.press("enter", "down")
            await pilot.pause()
            assert_solid()

    async def test_text_selection_copy_and_row_selection_are_independent(self):
        async with self.app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            table = self.app.query_one("#issues-table", DataTable)
            table.focus()
            await pilot.press("enter")
            self.assertFalse(self.app.checked)
            await pilot.click("#issues-table", offset=(1, 1))
            await pilot.pause()
            self.assertEqual(self.app.checked, {"azure:1"})
            preview = self.app.query_one("#issue-text", Preview)
            preview.focus()
            await pilot.press("ctrl+a", "ctrl+c")
            self.assertEqual(self.app.clipboard, preview.text)
            self.assertEqual(self.app.checked, {"azure:1"})

    async def test_remove_connection_preserves_handover_and_requires_explicit_submit(self):
        record = ops.draft(self.app.store, TICKET)
        async with self.app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.click("#--content-tab-configuration")
            await pilot.click("#connections")
            await wait_until(pilot, lambda: self.app.query(InlineForm) and self.app.query_one(InlineForm).query(Select) and self.app.query_one(InlineForm).query_one(Select).value == "new")
            self.app.query_one(InlineForm).query_one(Select).value = "azure"
            await pilot.click("#submit")
            await wait_until(pilot, lambda: self.app.query(InlineForm) and self.app.query_one(InlineForm).query(Select) and self.app.query_one(InlineForm).query_one(Select).value == "edit")
            self.app.query_one(InlineForm).query_one(Select).value = "remove"
            await pilot.click("#submit")
            await wait_until(pilot, lambda: bool(self.app.query("#credential")))
            self.assertEqual(len(self.app.store.config()["connections"]), 1)
            await pilot.click("#submit")
            await pilot.pause()
            await wait_until(pilot, lambda: self.app.store.config()["connections"] == [])
            self.assertEqual(self.app.store.config()["connections"], [])
            self.assertEqual(self.app.store.records("handovers")[0]["id"], record["id"])
            self.assertEqual(self.app.store.db.execute("SELECT COUNT(*) FROM cache").fetchone()[0], 0)

    async def test_native_copy_uses_captured_clicked_pane_selection(self):
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.app.dispatch("inbox", {"action": "copy-selection", "context": {"pane": {"id": "clicked"}, "selection": "exact\x1b[31m text"}})
            await pilot.pause()
            self.assertEqual(self.app.clipboard, "exact text")
            self.assertNotIsInstance(self.app.screen, Form)
            self.assertIn("copied", str(self.app.query_one("#status", Static).render()).lower())

    async def test_context_menu_pin_is_clickable_and_persistent(self):
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#actions")
            await wait_until(pilot, lambda: self.app.screen.query("#submit") and self.app.screen.query(Select) and self.app.screen.query_one(Select).value == "Handover")
            self.app.screen.query_one(Select).value = "Pin / unpin"
            await pilot.click("#submit")
            await pilot.pause()
            self.assertEqual(self.app.store.preference("favorites"), ["azure:1"])

    async def test_cancel_configuration_warns_before_discard(self):
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.app.query_one("#tabs", TabbedContent).active = "configuration"
            await pilot.pause()
            await pilot.click("#prompts")
            await wait_until(pilot, lambda: self.app.query(InlineForm) and self.app.query_one(InlineForm).query(Select) and self.app.query_one(InlineForm).query_one(Select).value == "global")
            await pilot.click("#submit")
            await wait_until(pilot, lambda: bool(self.app.query("#text")))
            self.app.query_one(InlineForm).query_one("#text", TextArea).load_text("Unsaved instructions")
            await pilot.click("#cancel")
            self.assertTrue(self.app.screen.query("#discard"))
            await pilot.click("#discard")
            await pilot.pause()
            self.assertNotEqual(self.app.store.config()["instructions"], "Unsaved instructions")

    async def test_batch_creates_drafts_without_launching(self):
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.app.checked.add("azure:1")
            await pilot.click("#actions")
            await wait_until(pilot, lambda: self.app.screen.query("#submit") and self.app.screen.query(Select) and self.app.screen.query_one(Select).value == "Handover")
            self.app.screen.query_one(Select).value = "Prepare selected drafts"
            previous = self.app.screen
            await pilot.click("#submit")
            await wait_until(pilot, lambda: isinstance(self.app.screen, Form) and self.app.screen is not previous and self.app.screen.heading == "Prepare drafts only" and bool(self.app.screen.query("#submit")))
            await pilot.pause()
            self.assertTrue(await pilot.click("#submit"))
            await wait_until(pilot, lambda: len(self.app.store.records("handovers")) == 1)
            records = self.app.store.records("handovers")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["stage"], "draft")
            self.assertNotIn("pane", records[0])

    async def test_draft_target_autosaves_on_cancel(self):
        record = ops.draft(self.app.store, TICKET)
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.app.selected_handover = record["id"]
            self.app.query_one("#tabs", TabbedContent).active = "handovers"
            await pilot.pause()
            await pilot.click("#edit")
            await wait_until(pilot, lambda: bool(self.app.screen.query("#target-branch")))
            self.app.screen.query_one("#target-branch", Input).value = "feature/recovered"
            await pilot.pause()
            await pilot.click("#editor-close")
            await pilot.pause()
            self.assertEqual(self.app.store.records("handovers")[0]["inputs"]["branch"], "feature/recovered")

    async def test_editor_reviews_preset_and_only_launches_after_confirmation(self):
        record = ops.draft(self.app.store, TICKET)
        record["inputs"].update(repo="/repo", base="main")
        save_record(self.app.store, record)
        def prepare(store, r):
            r["target"] = {"repo": "/repo", "path": "/repo/worktree", "branch": "feature/test", "commit": "abc", "existing": False}
            save_record(store, r)
            return r
        def launch(store, host, r, **kwargs):
            r["stage"] = "delivered"
            save_record(store, r)
            return r
        with patch.object(ops, "prepare", side_effect=prepare), \
             patch("luvus_tasks.console.base_branches", return_value=["main"]), \
             patch.object(ops, "preflight", side_effect=lambda s, h, r: {"record": prepare(s, r), "dirty": "", "images": False}), \
             patch("luvus_tasks.console.launch", side_effect=launch) as start:
            async with self.app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.app.selected_handover = record["id"]
                self.app.query_one("#tabs", TabbedContent).active = "handovers"
                await pilot.pause()
                await pilot.click("#edit")
                await pilot.pause()
                await wait_until(pilot, lambda: bool(self.app.query("#editor-sections")))
                self.app.query_one("#editor-sections", TabbedContent).active = "editor-prompt"
                await wait_until(pilot, lambda: self.app.query("#prompt-preset") and self.app.query_one("#prompt-preset", Select).value is not Select.NULL)
                self.app.query_one("#prompt-preset", Select).value = "Review"
                await pilot.pause()
                self.assertIn("Report findings; do not edit files", self.app.query_one("#opening-prompt", TextArea).text)
                await pilot.click("#editor-review")
                await pilot.pause()
                start.assert_not_called()
                await wait_until(pilot, lambda: bool(self.app.screen.query("#submit")))
                await pilot.click("#submit")  # final confirmation
                await pilot.pause()
                await wait_until(pilot, lambda: start.call_count == 1)
                start.assert_called_once()
                self.assertEqual(self.app.store.records("handovers")[0]["stage"], "delivered")


if __name__ == "__main__":
    unittest.main()
