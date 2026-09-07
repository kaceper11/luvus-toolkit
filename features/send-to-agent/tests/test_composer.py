import asyncio
from pathlib import Path
import tempfile
import unittest

from textual.widgets import Button, Input, Select, TextArea, Static
from composer import Composer
from send_agent import Store
from test_send import FakeHost
from unittest.mock import patch


class ComposerTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_send_and_cancel_at_narrow_width(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            record = store.create({"pane": {"cwd": root}, "selection": "exact\nzażółć 🌍"}, {})
            host = FakeHost(root)
            app = Composer(store.root, record["id"], host)
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause()
                self.assertFalse(app._exception)
                app.query_one("#instruction", TextArea).load_text("Explain this")
                app.query_one("#target", Select).value = "0"
                await pilot.pause()
                self.assertFalse(app.query_one("#send", Button).disabled)
                app.send()
                app.send()  # A second event must not cancel or duplicate an in-flight send.
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(store.get(record["id"])["state"], "delivered")
                self.assertEqual(len(host.calls), 1)
                self.assertTrue(app.query_one("#send", Button).disabled)
            record = store.create({"pane": {"cwd": root}, "selection": "do not send"}, {})
            app = Composer(store.root, record["id"], host)
            async with app.run_test() as pilot:
                app.query_one("#instruction", TextArea).load_text("saved instruction")
                await pilot.pause()
                await app.action_quit()
            self.assertEqual(len(host.calls), 1)
            self.assertEqual(store.get(record["id"])["instruction"], "saved instruction")
            store.db.close()

    async def test_pasted_text_is_included_without_review(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            record = store.create({"pane": {"cwd": root}, "selection": "quote"}, {})
            app = Composer(store.root, record["id"], FakeHost(root))
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#instruction", TextArea).load_text("Explain")
                await pilot.pause()
                app.query_one("#instruction", TextArea).load_text("Different")
                app.query_one("#quote", TextArea).load_text("additional context")
                await pilot.pause()
                self.assertFalse(app.query_one("#send", Button).disabled)
                app.send()
                await app.workers.wait_for_complete()
                self.assertEqual(store.get(record["id"])["state"], "delivered")
                self.assertIn("additional context", store.get(record["id"])["prompt"])
            store.db.close()

    async def test_discovery_failure_is_retryable_and_send_explains_missing_fields(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            record = store.create({"pane": {"cwd": root}, "selection": "quote"}, {})
            host = FakeHost(root)
            app = Composer(store.root, record["id"], host)
            with patch.object(host, 'agents', side_effect=ValueError('Discovery unavailable')):
                async with app.run_test() as pilot:
                    await app.workers.wait_for_complete()
                    self.assertFalse(app.query_one('#send', Button).disabled)
                    app.send()
                    await app.workers.wait_for_complete()
                    self.assertIn('Enter what', str(app.query_one('#instruction-error', Static).render()))
                    self.assertIn('Choose a recipient', str(app.query_one('#target-error', Static).render()))
                    self.assertEqual(host.calls, [])
            store.db.close()


if __name__ == "__main__":
    unittest.main()
