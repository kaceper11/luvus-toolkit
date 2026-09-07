import asyncio
import copy
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from textual.widgets import SelectionList, Input
from textual.worker import WorkerCancelled

from luvus_tasks import operations as ops
from luvus_tasks.console import Cockpit, RepositorySelection
from luvus_tasks.core import Store, TaskError, default_config, default_filters, ticket_key
from luvus_tasks.handover import branch_name, target_plan, git, file_context
from luvus_tasks.providers import GitHub, github_repositories, lookup_candidates
from test_tasks import GITHUB, github_issue
from test_console import Host, TICKET, CONNECTION


class MultiRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = {**GITHUB, "selected_repositories": ["example/repo", "example/other"]}
        with patch("luvus_tasks.providers.shutil.which", return_value="gh"):
            self.client = GitHub(self.connection)

    def test_same_numbers_have_distinct_identity_and_exact_write_target(self):
        first = self.client.normalize(github_issue())
        second = self.client.scoped("example/other").normalize(github_issue())
        self.assertEqual(first["id"], "1")  # Existing history and write markers stay valid.
        self.assertNotEqual(ticket_key(first), ticket_key(second))
        self.client.api = Mock(return_value=[])
        self.client.comment(second, "hello")
        self.client.api.assert_called_once_with("repos/example/other/issues/1/comments", "POST", {"body": "hello"})
        self.client.api.reset_mock()
        with self.assertRaises(TaskError):
            self.client.get("outside/repo#1")
        self.client.api.assert_not_called()

    def test_lookup_expands_bare_number_and_preserves_exact_repository(self):
        config = {"connections": [self.connection]}
        self.assertEqual([x[1] for x in lookup_candidates(config, "1")], ["1", "example/other#1"])
        self.assertEqual(lookup_candidates(config, "https://github.com/example/other/issues/1")[0][1], "example/other#1")

    def test_default_label_migration_and_cache_do_not_reuse_closed_results(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            config = default_config()
            custom = {"name": "Assigned to me", "query": "is:closed assignee:@me"}
            config["connections"] = [{**GITHUB, "filters": [{"name": "Assigned to me", "query": ""}, custom]},
                                     {**CONNECTION, "filters": [{"name": "Assigned to me", "query": ""}]}]
            store.save_config(config)
            loaded = store.config()
            self.assertEqual(loaded["connections"][0]["filters"], [*default_filters("github"), custom])
            self.assertEqual(loaded["connections"][1], config["connections"][1])
            loaded["connections"] = [loaded["connections"][0]]
            loaded["connections"][0]["filters"] = default_filters("github")
            store.save_config(loaded)
            old_key = GITHUB["id"] + ":" + hashlib.sha256(b"").hexdigest()
            old_issue = self.client.normalize({**github_issue(), "state": "closed"})
            store.put("cache", old_key, [old_issue])
            self.assertEqual(ops.refresh(store, False), ([], []))
            factory = Mock()
            factory.return_value.query.side_effect = TaskError("offline")
            tasks, errors = ops.refresh(store, True, factory)
            self.assertEqual(tasks, [])
            self.assertTrue(errors)
            factory.return_value.query.side_effect = None
            factory.return_value.query.return_value = [self.client.normalize(github_issue())]
            tasks, errors = ops.refresh(store, True, factory)
            factory.return_value.query.assert_called_with("is:open")
            self.assertEqual(len(tasks), 1)
            new_key = GITHUB["id"] + ":" + hashlib.sha256(b"is:open").hexdigest()
            self.assertEqual(len(store.cached(new_key)[0]), 1)
            self.assertEqual(store.cached(old_key)[0], [old_issue])
            store.db.close()

    def test_failed_repository_keeps_other_results_and_cached_failed_scope(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            config = default_config()
            config["connections"] = [self.connection]
            store.save_config(config)
            def factory(c):
                p = Mock()
                p.query.return_value = [self.client.scoped(c["_scope_repository"]).normalize(github_issue())]
                return p
            tasks, errors = ops.refresh(store, True, factory)
            self.assertEqual(len(tasks), 2)
            def failing(c):
                p = factory(c)
                if c["_scope_repository"] == "example/other":
                    p.query.side_effect = TaskError("offline")
                return p
            tasks, errors = ops.refresh(store, True, failing)
            self.assertEqual(len(tasks), 2)
            self.assertEqual(len(errors), 1)
            self.assertTrue(next(t for t in tasks if t["project"] == "example/other")["stale"])
            store.db.close()

    def test_scope_validation_and_account_change(self):
        for repos in ([], ["bad"], ["one/repo", "two/repo"], ["../repo"]):
            with self.assertRaises(TaskError):
                github_repositories({"selected_repositories": repos})
        self.client.c["account"] = "original"
        self.client.api = Mock(return_value={"login": "different"})
        with self.assertRaisesRegex(TaskError, "account changed"):
            self.client.scoped("example/repo").query("is:open")


class TargetTests(unittest.TestCase):
    def test_empty_checkout_has_actionable_error_and_no_worktree(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            with self.assertRaisesRegex(TaskError, "no available commits"):
                target_plan(repo, "feature/test", "main", True, root)
            self.assertFalse((Path(root) / "worktrees").exists())

    def test_branch_template_and_no_implicit_module_checkout(self):
        ticket = {**TICKET, "key": "owner/repo#12", "project": "owner/repo"}
        self.assertEqual(branch_name("{type}/{repo}-{number}-{slug}", ticket), "feature/repo-12-fix-red-literal-markup-red")
        for pattern in ("{missing}", "{key.__class__}", "{key!r}"):
            with self.assertRaises(TaskError):
                branch_name(pattern, ticket)
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            config = default_config()
            config["connections"] = [CONNECTION]
            store.save_config(config)
            with patch.dict("os.environ", {"LUVUS_WORKSPACE_CWD": "/wrong/module"}):
                self.assertEqual(ops.draft(store, TICKET)["inputs"]["repo"], "")
            store.db.close()

    def test_prepare_pins_commit_and_revalidates_references_preserving_other_context(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "initial")
            store = Store(Path(root) / "state")
            config = default_config()
            config["connections"] = [CONNECTION]
            store.save_config(config)
            record = ops.draft(store, TICKET, str(repo))
            record["inputs"]["base"] = "main"
            record["inputs"]["worktree_parent"] = str(Path(root) / "custom")
            ops.prepare(store, record)
            commit = record["target"]["commit"]
            self.assertEqual(Path(record["target"]["path"]).parent, Path(root) / "custom")
            git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "later")
            record["prompt"] = ""
            ops.prepare(store, record)
            self.assertEqual(record["target"]["commit"], commit)
            self.assertTrue(record["prompt"])
            record["context"] = [{"label": "notes", "mode": "text", "text": "keep"}, {"label": "image", "mode": "attachment", "target": "/image"}]
            record["inputs"]["branch"] = "feature/other"
            ops.prepare(store, record)
            self.assertEqual(len(record["context"]), 2)
            store.db.close()


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_only_repair_reuses_login_and_refreshes_only_after_save(self):
        with tempfile.TemporaryDirectory() as root:
            app = Cockpit(root, Host(), network=False)
            old = {"id": "gh", "provider": "github", "repository": "example"}
            config = default_config()
            config["connections"] = [old]
            app.store.save_config(config)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.workers.wait_for_complete()
                app.io = AsyncMock(side_effect=[("example", ["example"]), ["example/repo"]])
                app.inline = AsyncMock(return_value=("save-repos", ["example/repo"]))
                app.form = AsyncMock(return_value=None)
                app.choice = AsyncMock(side_effect=AssertionError("Unnecessary repair question"))
                app.load_filters = Mock()
                app.refresh_attention = Mock()
                app.status = Mock()
                with patch("luvus_tasks.console.GitHub"):
                    await app.configure_github(config, old)
                    self.assertEqual(app.store.config()["connections"], [old])
                    app.status.assert_not_called()
                    app.io.side_effect = [("example", ["example"]), ["example/repo"], ([], [])]
                    app.form.return_value = {"ident": "gh"}
                    async def perform(action, payload):
                        await app.configure_github(config, old)
                    app.perform = perform
                    await app.dispatch("connections").wait()
                    await app.workers.wait_for_complete()
                saved = app.store.config()["connections"][0]
                self.assertEqual(saved["selected_repositories"], ["example/repo"])
                self.assertEqual(saved["repository"], "example/repo")
                app.status.assert_any_call("Selection saved · refreshing issues…")
                app.status.assert_called_with("Issue refresh complete · 0 issues")
                self.assertFalse(app.issues_refreshing)
                self.assertFalse(app.busy)
            app.store.db.close()

    async def test_refresh_failure_and_cancellation_clear_progress_and_retain_cache(self):
        with tempfile.TemporaryDirectory() as root:
            app = Cockpit(root, Host(), network=False)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.workers.wait_for_complete()
                app.status = Mock()
                app.refresh_attention = Mock()
                cached = [{**TICKET, "stale": True}]
                app.tasks = cached
                app.io = AsyncMock(return_value=(cached, ["gh unavailable"]))
                await app.refresh_issues().wait()
                app.status.assert_called_with("Issue refresh completed with errors · cached issues retained")
                self.assertEqual(app.tasks, cached)
                for error in (TaskError("offline"), RuntimeError("unexpected")):
                    app.io = AsyncMock(side_effect=error)
                    await app.refresh_issues().wait()
                    app.status.assert_called_with("Issue refresh failed: " + str(error))
                    self.assertFalse(app.issues_refreshing)
                    self.assertEqual(app.tasks, cached)
                started = asyncio.Event()
                async def pending(fn):
                    started.set()
                    await asyncio.Event().wait()
                app.io = pending
                worker = app.refresh_issues()
                await started.wait()
                worker.cancel()
                with self.assertRaises(WorkerCancelled):
                    await worker.wait()
                app.status.assert_called_with("Issue refresh cancelled · cached issues retained")
                self.assertFalse(app.issues_refreshing)
                self.assertEqual(app.tasks, cached)
            app.store.db.close()

    async def test_repo_search_preserves_hidden_selection_and_refresh_request(self):
        with tempfile.TemporaryDirectory() as root:
            app = Cockpit(root, Host(), network=False)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                editor = RepositorySelection(["owner/one", "owner/two"], ["owner/one"])
                task = asyncio.create_task(app.inline(editor))
                await pilot.pause()
                editor.query_one(Input).value = "two"
                await pilot.pause()
                await pilot.click("#select-visible")
                await pilot.pause()
                self.assertEqual(editor.selected, {"owner/one", "owner/two"})
                await pilot.click("#refresh-repos")
                self.assertEqual(await task, ("refresh-repos", ["owner/one", "owner/two"]))
            app.store.db.close()


if __name__ == "__main__":
    unittest.main()
