import asyncio
import copy
import json
import sqlite3
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from textual.widgets import Input, Static, TabbedContent, TextArea

from luvus_tasks import operations as ops
from luvus_tasks.console import Cockpit
from luvus_tasks.core import Store, TaskError, ticket_key
from luvus_tasks.editor import HandoverEditor
from luvus_tasks.handover import Luvus, file_context, git, launch, matching_agent, save_record, validate_issue_repository

TICKET = {"provider": "GitHub", "connection": "github", "project": "owner/repo", "id": "1", "key": "#1", "status": "OPEN",
          "title": "Improve handover", "url": "https://github.com/owner/repo/issues/1", "description": "Details", "acceptance": ""}


class Host:
    def agents(self):
        return []

    def available_agents(self):
        return {"Codex": "codex"}

    def call(self, method, **params):
        if method == "workspace.list":
            return {"workspaces": []}
        if method == "terminal.backend.inventory":
            return {"server_generation": "g", "terminals": []}
        raise TaskError(method)


class HandoverUXTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], capture_output=True, check=True)
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "initial")
        git(self.repo, "remote", "add", "origin", "git@github.com:owner/repo.git")
        self.store = Store(self.root / "state")
        config = self.store.config()
        config["connections"] = [{"id": "github", "provider": "github", "repository": "owner/repo", "repositories": {"owner/repo": str(self.repo)}}]
        self.store.save_config(config)
        self.record = ops.draft(self.store, TICKET)
        self.record["inputs"]["base"] = "main"

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_custom_and_legacy_prompts_survive_target_and_context_changes(self):
        r = self.record
        ops.prepare(self.store, r)
        r.update(prompt="My exact instructions", prompt_mode="custom")
        r["context"].append({"label": "Context", "mode": "text", "text": "New text"})
        r["inputs"]["branch"] = "feature/another"
        ops.prepare(self.store, r)
        self.assertEqual(r["prompt"], "My exact instructions")
        self.assertTrue(r["prompt_outdated"])
        r.pop("prompt_mode")
        ops.prepare(self.store, r)
        self.assertEqual(r["prompt"], "My exact instructions")
        ops.update_prompt(self.store, r, True)
        self.assertIn("New text", r["prompt"])

    def test_wrong_repository_blocked_before_prepare_and_launch(self):
        ops.prepare(self.store, self.record)
        git(self.repo, "remote", "set-url", "origin", "https://github.com/owner/wrong.git")
        host = Mock()
        for operation in (lambda: ops.prepare(self.store, self.record), lambda: launch(self.store, host, self.record)):
            with self.assertRaisesRegex(TaskError, "belongs to owner/repo"):
                operation()
        host.call.assert_not_called()
        self.assertFalse(Path(self.record["target"]["path"]).exists())
        self.assertFalse(ops.ticket_matches(self.store.config(), TICKET, self.repo))

    def test_repository_remote_formats_and_missing_origin(self):
        for remote in ("https://github.com/owner/repo.git", "ssh://git@github.com/owner/repo.git", "git@GitHub.com:owner/repo.git"):
            git(self.repo, "remote", "set-url", "origin", remote)
            validate_issue_repository(TICKET, self.repo)
        git(self.repo, "remote", "remove", "origin")
        with self.assertRaises(TaskError):
            validate_issue_repository(TICKET, self.repo)

    def test_invalid_reference_preserves_other_context(self):
        r = self.record
        ops.prepare(self.store, r)
        r["context"] = [{"label": "Keep", "mode": "text", "text": "user text"},
                        {"label": "missing", "mode": "reference", "relative": "missing.txt", "target": "old"}]
        ops.prepare(self.store, r)
        self.assertEqual(r["context"][0]["text"], "user text")
        self.assertIn("error", r["context"][1])

    def test_agent_inventory_join_and_recycled_identity(self):
        host = Luvus.__new__(Luvus)
        raw = {"pane": "4", "name": "worker", "agent": "codex", "cwd": str(self.repo), "status": "working"}
        host.call = Mock(side_effect=[{"agents": [raw]}, {"server_generation": "g", "terminals": [{"pane_id": 4, "terminal_id": "t", "cwd": str(self.repo)}]}])
        agents = host.agents()
        record = {"pane": "4", "name": "worker", "agent": "codex", "terminal_id": "t", "generation": "g", "target": {"path": str(self.repo)}}
        self.assertIsNotNone(matching_agent(agents, record))
        for field, value in [("generation", "old"), ("terminal_id", "recycled"), ("pane", "8")]:
            self.assertIsNone(matching_agent(agents, {**record, field: value}))

    def test_dashboard_worktree_scope_groups_counts_and_distinct_ids(self):
        checkout = self.root / "checkout"
        git(self.repo, "worktree", "add", "-b", "feature/existing", str(checkout))
        self.assertEqual(ops.repository_identity(self.repo), ops.repository_identity(checkout))
        save_record(self.store, self.record)
        other = {**TICKET, "id": "owner/other#1", "project": "owner/other"}
        rows, active, attention = ops.dashboard(self.store, [TICKET, other], [], {"mode": "workspace", "repo": str(checkout)})
        self.assertTrue(any(r["text"].endswith(" · Draft") for r in rows))
        self.assertEqual(active, 0)
        self.assertNotIn(ticket_key(other), [r.get("value") for r in rows])
        values = [r.get("value", "") for r in rows]
        self.assertTrue(any(self.record["id"] in v for v in values))
        self.assertIn(ticket_key(TICKET), values)  # title opens the issue even when it has a draft
        self.assertTrue(any(r.get("menu") for r in rows))
        rows, _, _ = ops.dashboard(self.store, [other], [], {"mode": "all"}, ["offline"])
        self.assertTrue(any("cached results retained" in r["text"] for r in rows))
        self.assertIn(ticket_key(other), [r.get("value") for r in rows])

    def test_workspace_menu_has_one_hub_per_platform(self):
        config = tomllib.loads((Path(__file__).parents[1] / "luvus-module.toml").read_text())
        for platform in ("macos", "linux", "windows"):
            titles = [a["title"] for a in config["actions"] if "workspace" in a.get("contexts", []) and platform in a["platforms"]]
            self.assertEqual(sorted(titles), ["Tasks"])


class EditorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Cockpit(self.tmp.name, Host(), network=False)
        cfg = self.app.store.config()
        cfg["connections"] = [{"id": "github", "provider": "github", "repository": "owner/repo"}]
        self.app.store.save_config(cfg)
        self.record = ops.draft(self.app.store, TICKET)

    async def asyncTearDown(self):
        self.app.store.db.close()
        self.tmp.cleanup()

    async def test_editor_keeps_text_custom_prompt_and_tab_state_at_narrow_width(self):
        async with self.app.run_test(size=(85, 34)) as pilot:
            await pilot.pause()
            await self.app.wizard(self.record)
            await pilot.pause()
            editor = self.app.query_one(HandoverEditor)
            await pilot.click("#--content-tab-editor-context")
            await pilot.pause()
            await pilot.click("#context-text")
            await pilot.pause()
            editor.query_one("#context-body", TextArea).load_text("user context")
            await pilot.pause()
            await pilot.click("#--content-tab-editor-prompt")
            await pilot.pause()
            editor.query_one("#opening-prompt", TextArea).load_text("custom exact prompt")
            await pilot.pause()
            editor.query_one("#prompt-notes", TextArea).load_text("new notes")
            await pilot.pause()
            self.assertEqual(editor.record["prompt"], "custom exact prompt")
            self.assertTrue(editor.record["prompt_outdated"])
            self.app.query_one("#tabs", TabbedContent).active = "issues"
            self.app.query_one("#tabs", TabbedContent).active = "handover"
            await pilot.pause()
            self.assertIs(editor, self.app.query_one(HandoverEditor))
            self.assertTrue(editor.flush())
            saved = self.app.store.records("handovers")[0]
            self.assertEqual(saved["context"][0]["text"], "user context")
            self.assertEqual(saved["prompt"], "custom exact prompt")

    async def test_save_failure_blocks_review_but_preserves_buffer_while_switching(self):
        async with self.app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            await self.app.wizard(self.record)
            await pilot.pause()
            editor = self.app.query_one(HandoverEditor)
            other = ops.draft(self.app.store, TICKET)
            with patch("luvus_tasks.editor.save_record", side_effect=sqlite3.OperationalError("disk full")), patch.object(self.app, "review_launch", new_callable=AsyncMock) as launch:
                await pilot.click("#editor-review")
                await pilot.pause()
                launch.assert_not_called()
                await self.app.wizard(other)
                self.assertEqual(self.app.active_editor().record["id"], other["id"])
                self.assertFalse(editor.display)
                await self.app.wizard(self.record)
                self.assertIs(self.app.active_editor(), editor)
                self.assertIn("Save failed", str(editor.query_one("#editor-state", Static).render()))

    async def test_workspace_shortcut_offers_followup_without_starting_worker(self):
        r = self.record
        r.update(stage="delivered", name="worker", pane="4", terminal_id="t", generation="g", target={"path": "/clicked", "repo": "/clicked"})
        save_record(self.app.store, r)
        agent = {"name": "worker", "agent": "codex", "pane": "4", "terminal_id": "t", "generation": "g", "cwd": "/clicked"}
        async with self.app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            with patch("luvus_tasks.console.repository", return_value="/clicked"), patch("luvus_tasks.console.git", return_value="feature/existing"), patch.object(self.app.host, "agents", return_value=[agent]), patch.object(self.app, "choice", new_callable=AsyncMock, return_value="follow-up") as choose, patch.object(self.app, "history_action", new_callable=AsyncMock) as action:
                await self.app.workspace_handover({"workspace": {"cwd": "/clicked"}})
                self.assertIn("feature/existing", choose.call_args.args[0])
                action.assert_awaited_once_with("follow-up")
                self.assertEqual(len(self.app.store.records("handovers")), 1)


if __name__ == "__main__":
    unittest.main()
