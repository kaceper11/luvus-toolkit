"""Tasks opens the dashboard directly and remains navigation while work is busy."""
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from textual.widgets import TabbedContent
from luvus_tasks.module_hub import open_hub
from luvus_tasks.console import Cockpit
from luvus_tasks import operations as ops
from test_console import Host, TICKET, CONNECTION


class DirectTasksTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        naming = patch("luvus_tasks.tab_titles.remember", return_value=True)
        naming.start()
        self.addCleanup(naming.stop)

    def test_context_goes_directly_to_console(self):
        owner = SimpleNamespace(store=Mock(), host=Mock())
        context = {'invocation_source': 'menu:workspace', 'workspace': {'cwd': '/clicked'}}
        with patch('luvus_tasks.module_hub.open_console') as opened:
            open_hub(owner, context)
            opened.assert_called_once_with(owner.store, owner.host(), 'open', '', context)

    def test_open_payload_preserves_clicked_repository_for_existing_console(self):
        with tempfile.TemporaryDirectory() as folder:
            from luvus_tasks.core import Store
            store = Store(folder)
            host = Mock()
            host.call.return_value = {"server_generation": "g", "terminals": [{"terminal_id": "t", "pane_id": "4"}]}
            store.set_preference("console:" + ops.session_key(), {"pane": "4", "terminal": "t", "generation": "g"})
            context = {"invocation_source": "menu:pane", "pane": {"cwd": "/clicked"}, "workspace": {"cwd": "/other"}}
            ops.open_console(store, host, context=context)
            message = store.take_actions(ops.session_key())[0]
            self.assertEqual(json.loads(message["ticket"].removeprefix("dashboard:")), {"section": "issues", "repo": "/clicked"})
            self.assertEqual(message["context"], context)
            host.call.assert_called_with("pane.focus", pane="4")
            store.db.close()

    async def test_open_reveals_issues_while_busy_and_keeps_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Cockpit(folder, Host(), network=False)
            config = app.store.config()
            config["connections"] = [CONNECTION]
            app.store.save_config(config)
            ops.draft(app.store, TICKET)
            before = app.store.records('handovers')
            async with app.run_test() as pilot:
                await pilot.pause()
                app.show_tab('handovers')
                self.assertFalse(app.is_navigation("inbox", {"action": "open", "ticket": 'dashboard:{"action":"launch"}'}))
                app.busy = True
                app.dispatch('inbox', {'action': 'open', 'ticket': '', 'context': {}})
                await pilot.pause()
                self.assertEqual(app.query_one('#tabs', TabbedContent).active, 'issues')
                self.assertEqual(app.focused.id, 'issues-table')
                self.assertTrue(app.busy)
                self.assertEqual(app.store.records('handovers'), before)
                app.busy = False
