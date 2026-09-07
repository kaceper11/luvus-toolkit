import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from luvus_tasks.core import Store, TaskError, clean, compose, default_config, ticket_key
from luvus_tasks.handover import Luvus, attach, file_context, git, launch, resume, save_record, target_plan, worktrees
from luvus_tasks.providers import Azure, GitHub, HTTP, Jira, NoRedirect, ProviderError, lookup_candidates, plain
from luvus_tasks.ui import App


JIRA = {"id": "jira", "provider": "jira", "url": "https://example.atlassian.net", "email": "person@example.test"}
AZURE = {"id": "azure", "provider": "azure", "organization": "example", "project": "Project"}
GITHUB = {"id": "github", "provider": "github", "repository": "example/repo"}


def github_issue(number=1):
    return {"number": number, "title": "Fix issue", "body": "Acceptance: keep Markdown `code` intact.",
            "state": "open", "updated_at": "2026-09-05T12:00:00Z",
            "repository_url": "https://api.github.com/repos/example/repo"}


def jira_issue(ident="1"):
    return {"id": ident, "key": "TEST-" + ident, "fields": {"summary": "Fix it", "status": {"name": "Open"},
            "project": {"key": "TEST"}, "description": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Expected behavior"}]}]}}}


def azure_item(ident=1):
    return {"id": ident, "rev": 2, "fields": {"System.Title": "Task", "System.State": "Active",
            "System.WorkItemType": "Task", "System.Description": "<p>Hello</p>"}}


class CoreTests(unittest.TestCase):
    def test_agent_shell_strips_tracker_tokens(self):
        import launcher
        with patch.dict(os.environ, {"LUVUS_TASKS_TOKEN_TEST": "secret", "GH_TOKEN": "gh-secret", "GITHUB_TOKEN": "github-secret"}), \
             patch("sys.argv", ["launcher.py", "shell"]), patch("os.execvpe", side_effect=RuntimeError("exec")) as execute:
            with self.assertRaisesRegex(RuntimeError, "exec"):
                launcher.main()
        env = execute.call_args.args[2]
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("LUVUS_TASKS_TOKEN_TEST", env)

    def test_prompt_precedence_and_empty_override(self):
        config = default_config()
        task = Jira(JIRA, Mock()).normalize(jira_issue())
        text = compose(config, {"instructions": "Repository rules", "validation": "Run unit tests"}, "Plan", task, "Extra note", [
            {"label": "Screenshot", "mode": "attachment", "target": "/tmp/screen.png"}])
        self.assertTrue(text.startswith("Repository rules"))
        self.assertNotIn(config["instructions"], text)
        self.assertIn("Do not edit files", text)
        self.assertIn("Run unit tests", text)
        self.assertIn("/tmp/screen.png", text)
        self.assertNotIn(config["instructions"], compose(config, {"instructions": ""}, "Plan", task, "", []))

    def test_terminal_sanitization(self):
        self.assertEqual(clean("a\x1b[31mred\x1b[0m\x1b]52;c;secret\x07\x00\u202eb"), "aredb")

    def test_rich_text(self):
        self.assertEqual(plain("<p>A &amp; B</p><script>bad()</script>"), "A & B")
        self.assertIn("Expected behavior", plain(jira_issue()["fields"]["description"]))

    def test_store_preserves_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            record = {"id": "one", "prompt": "original"}
            store.put("handovers", "one", record, "jira:1")
            config = default_config()
            config["instructions"] = "changed"
            store.save_config(config)
            self.assertEqual(store.records("handovers", "jira:1")[0]["prompt"], "original")
            store.db.close()

    def test_lock_prevents_duplicate_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            with store.lock("launch"):
                with self.assertRaises(TaskError):
                    with store.lock("launch"):
                        pass
            self.assertFalse((Path(tmp) / "launch.lock").exists())
            store.db.close()


class ProviderTests(unittest.TestCase):
    def test_jira_pagination_uses_enhanced_search(self):
        http = Mock()
        http.request.side_effect = [{"issues": [jira_issue()], "nextPageToken": "next"},
                                    {"issues": [jira_issue("2")], "isLast": True}]
        tasks = Jira(JIRA, http).query()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(http.request.call_args_list[0].args[0], "/rest/api/3/search/jql")
        self.assertEqual(http.request.call_args_list[1].args[2]["nextPageToken"], "next")

    def test_repeated_jira_token_is_error(self):
        http = Mock()
        http.request.return_value = {"issues": [], "nextPageToken": "same"}
        with self.assertRaises(ProviderError):
            Jira(JIRA, http).query()

    def test_azure_batch_limit_and_order(self):
        http = Mock()
        ids = list(range(1, 202))
        http.request.side_effect = [{"queryType": "flat", "workItems": [{"id": i} for i in ids]},
                                    {"value": [azure_item(i) for i in reversed(ids[:200])]}, {"value": [azure_item(201)]}]
        tasks = Azure(AZURE, http).query()
        self.assertEqual([int(t["id"]) for t in tasks], ids)
        self.assertEqual(len(http.request.call_args_list[1].args[2]["ids"]), 200)

    def test_azure_rejects_nonflat_query(self):
        http = Mock()
        http.request.return_value = {"queryType": "tree"}
        with self.assertRaises(TaskError):
            Azure(AZURE, http).query("SELECT ...")

    def test_azure_revision_check(self):
        http = Mock()
        updated = azure_item()
        updated["fields"]["System.State"] = "Closed"
        http.request.side_effect = [{}, updated]
        p = Azure(AZURE, http)
        p.transition(p.normalize(azure_item()), {"state": "Closed"}, {})
        request = http.request.call_args_list[0]
        self.assertEqual(request.args[2][0], {"op": "test", "path": "/rev", "value": 2})
        self.assertEqual(request.args[3], "application/json-patch+json")

    def test_transition_required_fields_passed(self):
        http = Mock()
        updated = jira_issue()
        updated["fields"]["status"]["name"] = "Done"
        http.request.side_effect = [{}, updated]
        p = Jira(JIRA, http)
        p.transition(p.normalize(jira_issue()), {"id": "10", "state": "Done"}, {"resolution": {"id": "1"}})
        self.assertEqual(http.request.call_args_list[0].args[2]["fields"], {"resolution": {"id": "1"}})

    def test_lookup_does_not_accept_unconfigured_host(self):
        config = {"connections": [JIRA, AZURE]}
        self.assertEqual(lookup_candidates(config, "https://evil.test/browse/TEST-1"), [])
        self.assertEqual(lookup_candidates(config, "https://example.atlassian.net.evil.test/browse/TEST-1"), [])
        self.assertEqual(lookup_candidates(config, "https://example.atlassian.net/browse/TEST-1")[0][1], "TEST-1")
        self.assertEqual(lookup_candidates(config, "https://dev.azure.com/example/Project/_workitems/edit/1")[0][1], "1")
        self.assertEqual(len(lookup_candidates({"connections": [JIRA, {**JIRA, "id": "other"}]}, "TEST-1")), 2)

    def test_redirects_disabled(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_transport_marks_uncertain_writes(self):
        client = HTTP("https://example.test", "", "secret")
        client.opener = Mock()
        client.opener.open.side_effect = TimeoutError()
        with self.assertRaises(ProviderError) as error:
            client.request("/path", "POST", {}, write=True)
        self.assertTrue(error.exception.ambiguous)

    def test_http_errors_redact_token(self):
        import io
        client = HTTP("https://example.test", "", "secret")
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError("https://example.test", 401, "Denied", {}, io.BytesIO(b"secret denied"))
        with self.assertRaises(ProviderError) as error:
            client.request("/path")
        self.assertNotIn("secret", str(error.exception))

    def test_cached_tasks_survive_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            config = default_config()
            config["connections"] = [JIRA]
            store.save_config(config)
            app = App(store, Mock())
            with patch("luvus_tasks.ui.provider") as factory:
                factory.return_value.query.return_value = [Jira(JIRA, Mock()).normalize(jira_issue())]
                app.refresh()
                factory.return_value.query.side_effect = ProviderError("offline")
                with patch("builtins.print"):
                    tasks = app.refresh()
                self.assertEqual(len(tasks), 1)
                self.assertTrue(tasks[0]["stale"])
            store.db.close()


class GitTests(unittest.TestCase):
    def setUp(self):
        naming = patch("luvus_tasks.tab_titles.remember", return_value=True)
        naming.start()
        self.addCleanup(naming.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], check=True, capture_output=True)
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.test")
        (self.repo / "file.txt").write_text("first\nsecond\nthird\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "initial")
        self.store = Store(self.root / "state")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_default_base_without_origin_head_and_blank_base_validation(self):
        from luvus_tasks.handover import default_base
        self.assertEqual(default_base(self.repo), 'main')
        with self.assertRaisesRegex(TaskError, 'Choose a base branch'):
            target_plan(self.repo, 'feature/new', '', True, self.store.root)
        self.assertFalse((self.store.root / 'worktrees').exists())
        git(self.repo, 'branch', 'master')
        self.assertEqual(default_base(self.repo), '')
        self.assertEqual(default_base(self.repo, 'main'), 'main')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        git(self.repo, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        self.assertEqual(default_base(self.repo), 'origin/main')

    def record(self):
        import uuid
        ident = str(uuid.uuid4())
        return {"id": ident, "name": "task-" + ident[:16], "ticket": Jira(JIRA, Mock()).normalize(jira_issue()),
                "target": target_plan(self.repo, "feature/test", "main", True, self.store.root),
                "agent": "codex", "stage": "draft", "prompt": "Implement safely", "context": []}

    def host(self):
        host = Mock()
        host.agents.return_value = []
        host.call.side_effect = [{"pane_id": "4", "terminal_id": "terminal-unique", "server_generation": "generation"},
                                 {"pane": "4", "ready": True}, {"submitted": True}]
        return host

    def test_new_worktree_and_duplicate_launch(self):
        record, host = self.record(), self.host()
        launch(self.store, host, record)
        self.assertEqual(git(record["target"]["path"], "branch", "--show-current"), "feature/test")
        self.assertEqual(record["stage"], "delivered")
        launch(self.store, host, record)
        self.assertEqual(host.call.call_count, 3)
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")

    def test_github_issue_launch_uses_shared_flow(self):
        record, host = self.record(), self.host()
        git(self.repo, "remote", "add", "origin", "git@github.com:example/repo.git")
        with patch("luvus_tasks.providers.shutil.which", return_value="gh"):
            record["ticket"] = GitHub(GITHUB).normalize(github_issue())
        record["prompt"] = compose(default_config(), {}, "Implement", record["ticket"], "", [])
        launch(self.store, host, record)
        self.assertIn("github.com/example/repo/issues/1", host.call.call_args.kwargs["text"])
        self.assertEqual(record["stage"], "delivered")

    def test_existing_unchecked_branch_gets_worktree(self):
        git(self.repo, "branch", "feature/existing")
        plan = target_plan(self.repo, "feature/existing", "main", False, self.store.root)
        self.assertFalse(plan["existing"])
        record, host = self.record(), self.host()
        record["target"] = plan
        launch(self.store, host, record)
        self.assertEqual(git(plan["path"], "branch", "--show-current"), "feature/existing")

    def test_startup_timeout_reuses_exact_agent(self):
        record, host = self.record(), self.host()
        host.call.side_effect = [{"pane_id": "4", "terminal_id": "t", "server_generation": "g"}, {"ready": False}]
        with self.assertRaises(TaskError):
            launch(self.store, host, record)
        self.assertEqual(record["stage"], "agent_pending")
        host.agents.return_value = [{"name": record["name"], "terminal_id": "t", "generation": "g", "pane": "4", "agent": "codex", "cwd": record["target"]["path"]}]
        host.call.side_effect = [{"submitted": True}]
        launch(self.store, host, record)
        self.assertEqual(host.call.call_count, 3)
        self.assertEqual(record["stage"], "delivered")

    def test_oversized_prompt_never_creates_worktree(self):
        record, host = self.record(), self.host()
        record["prompt"] = "x" * 262145
        with self.assertRaises(TaskError):
            launch(self.store, host, record)
        self.assertEqual(len(worktrees(self.repo)), 1)
        host.call.assert_not_called()

    def test_resume_focuses_exact_identity(self):
        record, host = self.record(), Mock()
        record.update(pane="4", terminal_id="t", generation="g")
        host.agents.return_value = [{"name": record["name"], "agent": "codex", "pane": "4", "terminal_id": "t", "generation": "g",
                                     "cwd": record["target"]["path"], "session": "known"}]
        resume(self.store, host, record)
        host.call.assert_called_once_with("pane.focus", pane="4")
        self.assertEqual(record["session"], "known")

    def test_resume_unknown_session_does_not_launch(self):
        record, host = self.record(), Mock()
        host.agents.return_value = []
        with self.assertRaises(TaskError):
            resume(self.store, host, record)
        host.call.assert_not_called()

    def test_terminal_generation_guards_reused_pane(self):
        record = self.record()
        record.update(generation="old", terminal_id="t", pane="4")
        host = object.__new__(Luvus)
        host.call = Mock(return_value={"server_generation": "new", "terminals": []})
        with self.assertRaises(TaskError):
            host.validate_terminal(record)

    def test_binary_snapshot_rejected(self):
        path = self.repo / "binary.bin"
        path.write_bytes(b"\0\xff")
        plan = target_plan(self.repo, "main", "main", False, self.store.root)
        with self.assertRaises(TaskError):
            file_context(plan, "binary.bin", True)
        self.assertEqual(file_context(plan, "binary.bin")["mode"], "reference")

    def test_prompt_timeout_never_automatically_resends(self):
        record, host = self.record(), self.host()
        host.call.side_effect = [{"pane_id": "4", "terminal_id": "t", "server_generation": "g"}, {"ready": True}, TaskError("timeout")]
        with self.assertRaises(TaskError):
            launch(self.store, host, record)
        self.assertEqual(record["stage"], "prompt_pending")
        with self.assertRaises(TaskError):
            launch(self.store, host, record)
        self.assertEqual(host.call.call_count, 3)

    def test_existing_branch_and_dirty_checkout(self):
        plan = target_plan(self.repo, "main", "main", False, self.store.root)
        self.assertEqual(Path(plan["path"]).resolve(), self.repo.resolve())
        (self.repo / "file.txt").write_text("changed", encoding="utf-8")
        record = self.record()
        record["target"] = plan
        host = self.host()
        with self.assertRaises(TaskError):
            launch(self.store, host, record)
        host.call.assert_not_called()
        self.assertEqual((self.repo / "file.txt").read_text(), "changed")

    def test_reference_uses_selected_commit(self):
        plan = self.record()["target"]
        (self.repo / "file.txt").write_text("uncommitted", encoding="utf-8")
        item = file_context(plan, "file.txt", True, 2, 3)
        self.assertIn("second\nthird", item["text"])
        self.assertNotIn("uncommitted", item["text"])
        self.assertEqual(item["mode"], "snapshot")

    def test_missing_files_and_bad_ranges(self):
        plan = self.record()["target"]
        for path in ("../secret", "missing.txt"):
            with self.assertRaises(TaskError):
                file_context(plan, path)
        with self.assertRaises(TaskError):
            file_context(plan, "file.txt", True, 100, 101)

    def test_attachment_copy_and_scoped_removal(self):
        record = self.record()
        source = self.root / "image.png"
        source.write_bytes(b"test image")
        item = attach(self.store, record["id"], source)
        self.assertTrue(item["image"])
        record["context"] = [item]
        save_record(self.store, record)
        self.store.delete_handover(record)
        self.assertTrue(source.exists())
        self.assertTrue(self.repo.exists())
        self.assertFalse(Path(item["target"]).exists())


class UITests(unittest.TestCase):
    def test_writeback_duplicate_is_not_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            config = default_config()
            config["connections"] = [JIRA]
            store.save_config(config)
            app = App(store, Mock())
            task = Jira(JIRA, Mock()).normalize(jira_issue())
            with patch("luvus_tasks.ui.provider") as factory, patch("luvus_tasks.ui.multiline", return_value="Progress"), \
                 patch("luvus_tasks.ui.ask", return_value=""), patch("luvus_tasks.ui.confirm", return_value=True), patch("builtins.print"):
                def comments(_):
                    writes = store.records("writes", ticket_key(task))
                    return [{"text": writes[0]["text"]}] if writes else []
                factory.return_value.comments.side_effect = comments
                app.writeback(task)
                app.writeback(task)
                factory.return_value.comment.assert_called_once()
            store.db.close()

    def test_manifest_platform_actions_are_declared(self):
        import tomllib
        manifest = tomllib.loads((Path(__file__).parents[1] / "luvus-module.toml").read_text())
        self.assertEqual(manifest["min_luvus_version"], "0.13.4")
        actions = {a["id"] for a in manifest["actions"]}
        self.assertTrue({"open", "open-windows", "refresh", "refresh-windows"} <= actions)


class GitHubTests(unittest.TestCase):
    def setUp(self):
        with patch("luvus_tasks.providers.shutil.which", return_value="gh"):
            self.p = GitHub(GITHUB)

    def test_queries_all_open_issues_and_preserves_markdown(self):
        self.p.api = Mock(return_value={"total_count": 1, "items": [{**github_issue(), "assignees": []}]})
        results = self.p.query()
        self.assertIn("is%3Aopen", self.p.api.call_args.args[0])
        self.assertNotIn("assignee", self.p.api.call_args.args[0])
        self.assertEqual(results[0]["key"], "example/repo#1")
        self.assertIn("`code`", results[0]["description"])

    def test_explicit_closed_and_personal_queries_are_preserved(self):
        self.p.api = Mock(return_value={"total_count": 0, "items": []})
        self.p.query("is:closed label:bug")
        self.assertIn("is%3Aclosed+label%3Abug", self.p.api.call_args.args[0])
        self.assertNotIn("is%3Aopen", self.p.api.call_args.args[0])
        self.p.api = Mock(side_effect=[{"login": "person"}, {"total_count": 0, "items": []}])
        self.p.query("is:open assignee:@me")
        self.assertIn("is%3Aopen+assignee%3Aperson", self.p.api.call_args.args[0])

    def test_search_cap_and_incomplete_results_are_explicit(self):
        for data in ({"total_count": 1001}, {"total_count": 1, "incomplete_results": True}):
            self.p.api = Mock(return_value=data)
            with self.assertRaises(ProviderError):
                self.p.query("is:open")

    def test_cross_repo_search_results_rejected(self):
        issue = github_issue()
        issue["repository_url"] = "https://api.github.com/repos/other/repo"
        self.p.api = Mock(return_value={"total_count": 1, "items": [issue]})
        with self.assertRaises(ProviderError):
            self.p.query("is:open")

    def test_pr_is_not_issue(self):
        with self.assertRaises(TaskError):
            self.p.normalize({**github_issue(), "pull_request": {}})

    def test_lookup_number_ambiguity_and_url(self):
        config = {"connections": [GITHUB, AZURE]}
        self.assertEqual(len(lookup_candidates(config, "1")), 2)
        self.assertEqual(lookup_candidates(config, "https://github.com/example/repo/issues/1"), [(GITHUB, "1")])
        self.assertEqual(lookup_candidates(config, "https://github.com/example/other/issues/1"), [])
        self.assertEqual(lookup_candidates(config, "example/repo#1"), [(GITHUB, "example/repo#1")])

    def test_status_actions_and_stale_guard(self):
        task = self.p.normalize(github_issue())
        actions = self.p.actions(task)
        self.assertEqual([a["reason"] for a in actions], ["completed", "not_planned"])
        self.p.get = Mock(return_value={**task, "updated_at": "later"})
        self.p.api = Mock()
        with self.assertRaises(ProviderError):
            self.p.transition(task, actions[0], {})
        self.p.api.assert_not_called()

    def test_gh_uses_stdin_for_comment_and_explicit_host(self):
        response = subprocess.CompletedProcess([], 0, '{"id": 5}', '')
        with patch("luvus_tasks.providers.subprocess.run", return_value=response) as run:
            self.p.comment(self.p.normalize(github_issue()), "literal $(no) `shell`\nsecond line")
        args = run.call_args.args[0]
        self.assertIn("github.com", args)
        self.assertIn("--input", args)
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["body"], "literal $(no) `shell`\nsecond line")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_github_task_uses_same_handover_prompt(self):
        task = self.p.normalize(github_issue())
        prompt = compose(default_config(), {}, "Implement", task, "More context", [])
        self.assertIn("https://github.com/example/repo/issues/1", prompt)
        self.assertIn("More context", prompt)


if __name__ == "__main__":
    unittest.main()
