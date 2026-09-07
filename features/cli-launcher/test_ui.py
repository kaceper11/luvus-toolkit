"""Headless mouse/keyboard UI checks using only temporary configuration."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from textual.widgets import Button, DataTable, Input, Select, TextArea

import launcher
import project_launcher as backend
from launcher_ui import Form, LauncherApp


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="launcher-ui-")
        self.addCleanup(self.temp.cleanup)
        self.cwd = backend.canonical(self.temp.name)
        self.presets = Path(self.cwd) / "presets.json"
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("LUVUS_BIN_PATH", None)
        self.app = LauncherApp(self.presets, self.cwd, rpc=self.rpc)
        self.calls = []

    def rpc(self, method, **params):
        self.calls.append((method, params))
        if method == "terminal.backend.inventory":
            return {"server_generation": "a" * 32, "terminals": [], "truncated": False}
        raise ValueError("No native launch should be needed for this UI check.")

    async def ready(self, pilot):
        await pilot.pause()
        await self.app.workers.wait_for_complete()
        await pilot.pause()

    def value(self, key, value):
        self.app.screen.query_one("#" + key, Input).value = value

    async def test_add_link_with_mouse_and_worktree_scope(self):
        async with self.app.run_test(size=(100, 34)) as pilot:
            await self.ready(pilot)
            self.assertTrue(self.app.query_one("#open", Button).disabled)
            await pilot.click("#add")
            await pilot.pause()
            self.value("name", "Documentation")
            self.value("address", "https://example.com/docs")
            self.app.screen.query_one("#scope", Select).value = "worktrees"
            await pilot.click("#save")
            await self.ready(pilot)
            saved = backend.load_config(self.app.config_path)
            links = saved["worktrees"][self.cwd]["links"]
            self.assertEqual(next(iter(links.values())), {"name": "Documentation", "destination": "https://example.com/docs"})
            self.assertEqual(self.app.query_one("#entries", DataTable).row_count, 1)
            self.assertEqual(self.calls, [])

    async def test_validation_keeps_form_values_and_escape_cancels(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            await pilot.press("ctrl+n")
            await pilot.pause()
            self.value("name", "Unsafe")
            self.value("address", "javascript:alert(1)")
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, Form)
            self.assertEqual(self.app.screen.query_one("#name", Input).value, "Unsafe")
            self.assertFalse(self.app.config_path.exists())
            await pilot.press("escape")
            await self.ready(pilot)
            self.assertFalse(self.app.busy)
            self.assertFalse(self.app.config_path.exists())

    async def test_layout_wizard_saves_without_spawning(self):
        self.app.section = "arrangements"
        async with self.app.run_test(size=(100, 38)) as pilot:
            await self.ready(pilot)
            await pilot.click("#add")
            await pilot.pause()
            self.app.screen.query_one("#layout", Select).value = "columns"
            await pilot.click("#save")
            await pilot.pause()
            self.value("name", "Development")
            await pilot.press("ctrl+s")
            await self.ready(pilot)
            saved = backend.effective(backend.load_config(self.app.config_path), self.app.project, "arrangements")
            layout = next(iter(saved.values()))
            self.assertEqual(layout["name"], "Development")
            self.assertEqual(len(layout["roles"]), 2)
            self.assertEqual(layout["tree"]["Split"]["axis"], 0)
            self.assertEqual(layout["roles"]["pane1"]["tool"], launcher.DEFAULTS[0])
            self.assertEqual([name for name, _ in self.calls], ["terminal.backend.inventory"])

    async def test_search_and_keyboard_navigation(self):
        self.app.section = "tools"
        launcher.save_presets(self.presets, [*launcher.DEFAULTS, {"name": "Python", "command": "python3"}], launcher.DEFAULTS)
        async with self.app.run_test(size=(90, 28)) as pilot:
            await self.ready(pilot)
            await pilot.press("down", "down")
            self.assertEqual(self.app.rows[self.app.current()]["name"], "Python")
            await pilot.press("ctrl+f")
            await pilot.press(*"codex")
            await pilot.pause()
            self.assertEqual(len(self.app.visible_ids), 1)
            self.assertEqual(self.app.rows[self.app.current()]["name"], "Codex")

    async def test_search_enter_opens_and_escape_clears(self):
        self.app.section = "tools"
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            with patch.object(self.app, "launch_agent", new_callable=AsyncMock) as opened:
                await pilot.press("ctrl+f", *"codex", "enter")
                await self.ready(pilot)
                opened.assert_awaited_once()
            await pilot.press("ctrl+f", "escape")
            self.assertEqual(self.app.query_one("#filter", Input).value, "")
            self.assertIsInstance(self.app.focused, DataTable)

    async def test_compact_dashboard_and_focus_rendering(self):
        self.app.section = "tools"
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            self.assertGreaterEqual(self.app.query_one("#entries").region.height, 8)
            await pilot.resize_terminal(40, 24)
            await pilot.pause()
            for key in ("open", "add", "edit", "more"):
                self.assertLessEqual(self.app.query_one("#" + key).region.right, 40)
            await pilot.press("ctrl+f")
            await pilot.pause()
            self.assertIn("Search", self.app.export_screenshot())
            await pilot.click("#links")
            await self.ready(pilot)
            await pilot.press("ctrl+n")
            await pilot.pause()
            for selector in ("#name", "#kind", "#address"):
                widget = self.app.screen.query_one(selector)
                widget.focus()
                await pilot.pause()
                bordered = widget.query_one("SelectCurrent") if isinstance(widget, Select) else widget
                self.assertEqual(bordered.styles.border_top[0], "round")
                self.assertIn("<svg", self.app.export_screenshot())
            await pilot.press("escape")
            await self.ready(pilot)

    async def test_layout_save_refreshes_unchanged_command_ids(self):
        self.app.section = "arrangements"
        item = {"name": "Dev", "roles": {"pane1": {"tool": launcher.DEFAULTS[0]}},
                "tree": {"Leaf": "pane1"}, "commands": [{"id": "test", "revision": "old", "kind": "command"}]}
        config = deepcopy(backend.EMPTY)
        config["providers"]["commands"] = ["/fake/provider"]
        config["repositories"][self.cwd] = {"arrangements": {"dev": item}}
        backend.save_config(self.app.config_path, config, deepcopy(backend.EMPTY))
        async with self.app.run_test(size=(100, 38)) as pilot:
            await self.ready(pilot)
            await pilot.click("#edit")
            await pilot.pause()
            await pilot.click("#save")
            await pilot.pause()
            with patch("project_launcher.provider", return_value={"id": "test", "revision": "new", "kind": "command"}) as owner:
                await pilot.press("ctrl+s")
                await self.ready(pilot)
            owner.assert_called_once()
            saved = backend.effective(backend.load_config(self.app.config_path), self.app.project, "arrangements")
            self.assertEqual(saved["dev"]["commands"][0]["revision"], "new")

    async def test_information_has_one_focused_close_and_multiline_focus_renders(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            worker = self.app.run_worker(self.app.message("Status", "Nothing launched."))
            await pilot.pause()
            self.assertEqual(len(self.app.screen.query("Button")), 1)
            self.assertEqual(self.app.focused.id, "save")
            await pilot.press("enter")
            await worker.wait()
            self.app.section = "settings"
            self.app.refresh_data()
            self.app.draw_rows()
            with patch("project_launcher.installed_provider", return_value=["/fake/python", "owner.py"]):
                self.app.perform("open")
                await pilot.pause()
            area = self.app.screen.query_one("#arguments", TextArea)
            area.focus()
            await pilot.pause()
            self.assertEqual(area.styles.border_top[0], "round")
            self.assertIn("<svg", self.app.export_screenshot())
            await pilot.press("escape")
            await self.ready(pilot)

    async def test_connection_failure_keeps_settings_and_does_not_save(self):
        self.app.section = "settings"
        async with self.app.run_test(size=(100, 34)) as pilot:
            await self.ready(pilot)
            with patch("project_launcher.installed_provider", return_value=["/fake/python", "owner.py"]):
                await pilot.click("#open")
                await pilot.pause()
            with patch("project_launcher.check_provider", side_effect=ValueError("Connection refused")):
                await pilot.press("ctrl+s")
                await pilot.pause()
            self.assertEqual(self.app.screen.query_one("#program", Input).value, "/fake/python")
            self.assertFalse(self.app.config_path.exists())
            await pilot.press("escape")
            await self.ready(pilot)

    async def test_conflicting_edit_does_not_overwrite_external_changes(self):
        async with self.app.run_test(size=(100, 34)) as pilot:
            await self.ready(pilot)
            await pilot.click("#add")
            await pilot.pause()
            self.value("name", "New link")
            self.value("address", "https://example.com")
            other = deepcopy(backend.EMPTY)
            other["worktrees"][self.cwd] = {"links": {"other": {"name": "Other", "destination": "https://other.example"}}}
            backend.save_config(self.app.config_path, other, deepcopy(backend.EMPTY))
            await pilot.click("#save")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, Form)
            self.assertEqual(backend.load_config(self.app.config_path), other)
            self.assertEqual(self.app.screen.query_one("#name", Input).value, "New link")
            self.assertEqual(self.app.screen.query_one("#address", Input).value, "https://example.com")
            self.assertIn("changed", str(self.app.screen.query_one("#form-error").render()))
            await pilot.press("escape")
            await self.ready(pilot)

    async def test_missing_integrations_are_empty_states_not_errors(self):
        self.app.section = "bundles"
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            self.assertNotIsInstance(self.app.screen, Form)
            self.assertTrue(self.app.query_one("#empty").display)
            self.assertEqual(self.calls, [])
            await pilot.click("#settings")
            await self.ready(pilot)
            self.assertEqual(self.app.section, "settings")
            self.assertEqual(len(self.app.rows), 3)

    async def test_form_buttons_fit_small_terminal_and_resize(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            await pilot.click("#add")
            await pilot.pause()
            for key in ("save", "cancel"):
                button = self.app.screen.query_one("#" + key, Button)
                self.assertLess(button.region.bottom, 25)
                self.assertGreaterEqual(button.region.x, 0)
            await pilot.resize_terminal(100, 36)
            await pilot.pause()
            self.assertTrue(await pilot.click("#cancel"))
            await self.ready(pilot)

    async def test_double_action_does_not_open_duplicate_forms(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            self.app.perform("add")
            self.app.perform("add")
            await pilot.pause()
            self.assertEqual(len(self.app.screen_stack), 2)
            await pilot.press("escape")
            await self.ready(pilot)
            self.assertEqual(len(self.app.screen_stack), 1)


if __name__ == "__main__":
    unittest.main()


class AgentWizardTests(unittest.IsolatedAsyncioTestCase):
    setUp = DashboardTests.setUp
    rpc = DashboardTests.rpc
    ready = DashboardTests.ready
    value = DashboardTests.value
    async def test_agent_wizard_cancel_and_retry_keeps_prepared_request(self):
        self.app.section = "tools"
        async with self.app.run_test(size=(40, 24)) as pilot:
            await self.ready(pilot)
            await pilot.click("#open")
            await pilot.pause()
            self.assertEqual(self.app.screen.query_one("#mode", Select).value, "current")
            await pilot.press("escape")
            await self.ready(pilot)
            self.assertFalse(self.presets.with_name("agent-launch.json").exists())
            await pilot.click("#open")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            with patch("agent_launch.execute", side_effect=[ValueError("Native launch rejected"), {"pane_id": "test"}]) as run:
                await pilot.press("ctrl+s")
                await pilot.pause()
                self.assertIsInstance(self.app.screen, Form)
                self.assertIn("Native launch rejected", str(self.app.screen.query_one("#form-error").render()))
                await pilot.press("ctrl+s")
                await self.ready(pilot)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0], run.call_args_list[1])
            self.assertFalse(self.app.busy)

    async def test_git_wizard_defaults_to_new_and_reviews_before_mutation(self):
        import subprocess
        subprocess.run(["git", "init", "-b", "main", self.cwd], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.cwd, "-c", "user.name=Test", "-c", "user.email=t@example.com", "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
        self.app = LauncherApp(self.presets, self.cwd, section="tools", rpc=self.rpc)
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.ready(pilot)
            await pilot.click("#open")
            await pilot.pause()
            for _ in range(100):
                if self.app.screen.query("#mode"): break
                await pilot.pause(0.05)
            self.assertEqual(self.app.screen.query_one("#mode", Select).value, "new")
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.value("branch", "work/test")
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.value("destination", str(Path(self.cwd).parent / (Path(self.cwd).name + "-work")))
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertIn("work/test", str(self.app.screen.query_one(".form-help").render()))
            self.assertFalse(self.presets.with_name("agent-launch.json").exists())
            await pilot.press("escape")
            await self.ready(pilot)
