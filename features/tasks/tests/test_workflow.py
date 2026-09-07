import copy
import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from luvus_tasks import attention, forge, workflow, review, polling
from luvus_tasks.core import ticket_key, Store, TaskError, default_config
from luvus_tasks.handover import git, save_record, launch
from luvus_tasks.providers import ProviderError


class WorkflowTests(unittest.TestCase):
    def test_poller_skips_unconfigured_drafts(self):
        record = {**self.record, "target": None}
        save_record(self.store, record)
        self.assertEqual(workflow.poll_prs(self.store), 0)

    def test_background_reader_is_singleton_and_stops_before_repaint_when_disabled(self):
        app = Mock(store=self.store)
        host = app.host.return_value
        enabled = True
        def call(method, **params):
            if method == "terminal.backend.inventory":
                return {"server_generation": "test"}
            return {"enabled": enabled, "runnable": True, "root": str(Path(polling.__file__).resolve().parent.parent)}
        host.call.side_effect = call
        def poll(store, limit):
            nonlocal enabled
            self.assertEqual(limit, 1)
            # A second startup cannot enter the polling loop while the first owns the lock.
            self.assertEqual(polling.watch(app), 0)
            enabled = False
        with patch.dict(os.environ, {"LUVUS_SOCKET_PATH": "fixture", "LUVUS_BIN_PATH": "/fixture"}), \
             patch.object(polling, "poll_prs", side_effect=poll) as reads, patch.object(polling.time, "sleep") as sleep:
            self.assertEqual(polling.watch(app), 0)
            reads.assert_called_once()
            app.dock.assert_called_once_with(False)
            sleep.assert_not_called()
            # Closing the file released ownership; a subsequent startup can acquire it.
            enabled = True
            self.assertEqual(polling.watch(app), 0)
            self.assertEqual(reads.call_count, 2)

    def test_github_thread_resolution_paginates_and_applies_to_replies(self):
        p = forge.GitHubPR.__new__(forge.GitHubPR)
        p.repo = "example/repo"
        p.api = Mock(return_value=[{"id": 2, "node_id": "reply", "in_reply_to_id": 1},
                                  {"id": 1, "node_id": "root"}, {"id": 3, "node_id": "other"}])
        def page(node, resolved, more):
            return {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": [{"id": "thread:" + node, "isResolved": resolved, "isOutdated": True,
                           "comments": {"nodes": [{"id": node}]}}],
                "pageInfo": {"hasNextPage": more, "endCursor": node}}}}}}
        p.api_client = Mock()
        p.api_client.api.side_effect = [page("root", True, True), page("other", False, False)]
        comments = p.review_comments("5")
        self.assertEqual([x["state"] for x in comments], ["resolved", "resolved", "comment"])
        self.assertEqual(comments[0]["thread"], comments[1]["thread"])
        self.assertTrue(comments[2]["outdated"])  # Outdated is not resolved.
        self.assertEqual(p.api_client.api.call_args.kwargs["data"]["variables"]["after"], "root")
        p.api_client.api.side_effect = [{"errors": [{"message": "partial"}]}]
        with self.assertRaisesRegex(TaskError, "incomplete"):
            p.review_comments("5")
        p.api_client.api.side_effect = [page("missing", False, False)]
        with self.assertRaisesRegex(TaskError, "changed during refresh"):
            p.review_comments("5")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        git(self.repo, "config", "user.email", "test@example.test")
        git(self.repo, "config", "user.name", "Tests")
        (self.repo / "code.txt").write_text("original")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "remote", "add", "origin", "git@github.com:example/repo.git")
        self.store = Store(self.root / "state")
        config = default_config()
        config["connections"] = [{"id": "gh", "provider": "github", "repository": "example/repo"}]
        self.store.save_config(config)
        self.record = {"id": str(uuid.uuid4()), "name": "worker", "agent": "codex", "stage": "draft", "created": "2026-09-06T00:00:00+00:00",
            "ticket": {"connection": "gh", "provider": "GitHub", "id": "1", "key": "example/repo#1", "project": "example/repo", "title": "Task", "url": "https://github.com/example/repo/issues/1"},
            "target": {"repo": str(self.repo), "path": str(self.repo), "branch": "main", "commit": git(self.repo, "rev-parse", "HEAD"), "base": "main", "existing": True, "new": False},
            "forge": {"kind": "github", "repository": "example/repo", "connection": "gh"}, "context": [], "prompt": "Investigate", "preset": "Plan"}
        save_record(self.store, self.record)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_observations_are_transactional_and_polling_is_not_activity(self):
        item = {"kind": "validation", "title": "Test", "state": "failed"}
        self.store.observe("test", self.record["id"], {**item, "observed_at": "one"})
        before = len(self.store.timeline(self.record["id"]))
        self.store.observe("test", self.record["id"], {**item, "observed_at": "two"})
        self.assertEqual(len(self.store.timeline(self.record["id"])), before)
        self.store.observe("test", self.record["id"], {**item, "state": "passed"})
        self.assertEqual(len(self.store.timeline(self.record["id"])), before + 1)
        self.store.observe("newer", self.record["id"], item)
        self.store.observe("test", self.record["id"], item)
        self.assertEqual(self.store.evidence(self.record["id"])[0]["id"], "newer")

    def test_fingerprint_detects_same_status_content_and_untracked_edits(self):
        p = self.repo / "code.txt"
        p.write_text("change one")
        first = workflow.fingerprint(self.repo)
        p.write_text("change two")
        self.assertNotEqual(workflow.fingerprint(self.repo), first)
        first = workflow.fingerprint(self.repo)
        (self.repo / "untracked").write_text("new")
        self.assertNotEqual(workflow.fingerprint(self.repo), first)

    def test_artifact_states_and_history_deletion_preserve_original(self):
        original = self.root / "report.html"
        original.write_text("report")
        item = workflow.artifact(self.store, self.record, original)
        self.assertEqual(workflow.artifact_state(item), "available")
        original.write_text("changed report")
        self.assertEqual(workflow.artifact_state(item), "changed")
        self.store.delete_handover(self.record)
        self.assertTrue(original.exists())
        self.assertFalse(self.store.evidence(self.record["id"]))

    def test_comparison_only_prepares_independent_pinned_drafts(self):
        before = git(self.repo, "status", "--porcelain")
        children = workflow.alternatives(self.store, self.record, [{"agent": "codex", "prompt": "A"}, {"agent": "codex", "prompt": "B"}])
        self.assertNotEqual(children[0]["target"]["path"], children[1]["target"]["path"])
        for child in children:
            self.assertEqual(child["target"]["commit"], self.record["target"]["commit"])
            self.assertFalse(Path(child["target"]["path"]).exists())
            self.assertNotIn("pane", child)
        self.assertEqual(git(self.repo, "status", "--porcelain"), before)

    def test_invalid_connection_does_not_hide_valid_repository_choice(self):
        c = self.store.config()
        c["connections"].insert(0, {"id": "broken", "provider": "github", "repository": "owner-only"})
        self.store.save_config(c)
        self.assertEqual(workflow.summary(self.record)["decisions"], "")
        self.assertEqual(forge.choices(self.store, self.record), [self.record["forge"]])

    def test_uncertain_pr_publication_cannot_retry_with_changed_title(self):
        p = Mock()
        p.list.return_value = []
        p.branch.side_effect = lambda b: "h" if b == "main" else "b"
        p.create.side_effect = ProviderError("timeout", ambiguous=True)
        plan = {"destination": self.record["forge"], "head": "main", "base": "other", "head_sha": "h", "base_sha": "b", "title": "Task", "body": "Body", "draft": True}
        with patch.object(forge, "client", return_value=p):
            with self.assertRaises(ProviderError):
                forge.publish(self.store, self.record, plan)
            with self.assertRaisesRegex(TaskError, "uncertain"):
                forge.publish(self.store, self.record, {**plan, "title": "Changed"})
        self.assertEqual(p.create.call_count, 1)
        self.assertEqual(self.store.records("writes")[0]["state"], "pending")

    def test_pr_remote_change_prevents_publication(self):
        p = Mock()
        p.list.return_value = []
        p.branch.return_value = "changed"
        with patch.object(forge, "client", return_value=p), self.assertRaisesRegex(TaskError, "changed"):
            forge.publish(self.store, self.record, {"destination": self.record["forge"], "head": "main", "base": "other", "head_sha": "h", "base_sha": "b"})
        p.create.assert_not_called()
        self.assertFalse(self.store.records("writes"))

    def test_pr_readback_and_reconciliation_preserve_ticket_journal(self):
        p = Mock()
        p.list.return_value = []
        p.branch.side_effect = lambda b: "h" if b == "main" else "b"
        plan = {"destination": self.record["forge"], "head": "main", "base": "other", "head_sha": "h", "base_sha": "b", "title": "Task", "body": "Body", "draft": True}
        result = {"id": "5", "head_branch": "main", "base_branch": "other", "title": "Task", "body": "Body", "draft": True}
        p.create.return_value = result
        p.get.side_effect = [TaskError("read failed"), result]
        with patch.object(forge, "client", return_value=p):
            with self.assertRaises(TaskError):
                forge.publish(self.store, self.record, plan)
            self.assertEqual(forge.publish(self.store, self.record, plan), result)
        self.assertEqual(p.create.call_count, 1)
        self.assertEqual(self.store.records("writes", "gh:1")[0]["state"], "published")

    def agent(self, status):
        self.record.update(pane="4", generation="g", terminal_id="term", stage="delivered")
        save_record(self.store, self.record)
        return {"pane": "4", "generation": "g", "terminal_id": "term", "name": "worker", "agent": "codex", "cwd": str(self.repo), "status": status}

    def test_agent_attention_deduplicates_and_done_recurrence_reopens(self):
        a = self.agent("done")
        first = attention.project(self.store, [a])
        attention.acknowledge(self.store, first[0])
        self.assertFalse(attention.project(self.store, [a]))
        self.assertFalse(attention.project(self.store, [{**a, "status": "working"}]))
        self.assertEqual(len(attention.project(self.store, [a])), 1)
        count = len(self.store.timeline(self.record["id"]))
        attention.project(self.store, [a])
        self.assertEqual(len(self.store.timeline(self.record["id"])), count)

    def test_input_cannot_be_acknowledged_and_stale_pane_cannot_navigate(self):
        a = self.agent("blocked")
        item = attention.project(self.store, [a])[0]
        with self.assertRaises(TaskError):
            attention.acknowledge(self.store, item)
        host = Mock()
        host.agents.return_value = [{**a, "terminal_id": "replacement"}]
        with self.assertRaises(TaskError):
            attention.navigate(host, item)
        host.call.assert_not_called()
        self.assertFalse(attention.project(self.store, [{**a, "status": "idle"}]))

    def test_unlinked_agents_are_not_new_task_records_and_missing_source_is_unknown(self):
        a = {"pane": "8", "generation": "g", "terminal_id": "unlinked", "name": "other", "agent": "codex", "cwd": str(self.repo), "status": "blocked"}
        first = attention.project(self.store, [a])
        self.assertEqual(first[0]["handover"], "")
        self.assertEqual(len(self.store.records("handovers")), 1)
        missing = attention.project(self.store, [], host_available=False)
        self.assertEqual(missing[0]["state"], "unknown")

    def test_current_ci_failure_clears_when_latest_run_passes(self):
        p = {"head": "head", "state": "open", "checks": [], "feedback": [], "runs": [
             {"id": "1", "group": "build", "title": "Build", "state": "failed", "revision": "head"}]}
        self.store.observe("pr:" + self.record["id"], self.record["id"], {"kind": "pr", "title": "PR", "state": "open", "snapshot": p, "checkout": workflow.fingerprint(self.repo)})
        items = attention.project(self.store, [])
        self.assertEqual(items[0]["state"], "active")
        p["runs"].append({"id": "2", "group": "build", "title": "Build", "state": "passed", "revision": "head"})
        self.store.observe("pr:" + self.record["id"], self.record["id"], {"kind": "pr", "title": "PR", "state": "open", "snapshot": p, "checkout": workflow.fingerprint(self.repo)})
        self.assertFalse(attention.project(self.store, []))

    def test_old_head_ci_never_becomes_current_success(self):
        p = Mock()
        p.snapshot.return_value = {"id": "1", "head_branch": "main", "head": "old", "title": "PR", "state": "open", "checks": [], "feedback": [], "runs": []}
        self.record["pr_id"] = "1"
        with patch.object(forge, "client", return_value=p):
            data = workflow.refresh_pr(self.store, self.record)
        self.assertTrue(data["stale"])

    def test_command_retry_reuses_request_and_wrong_checkout_is_rejected(self):
        calls = []
        def bridge(s, owner, method, **params):
            calls.append(method)
            return {"request_id": params["request_id"], "run_id": "run", "worktree": params["worktree"], "state": "passed", "freshness": "current"}
        command = {"id": "test", "kind": "validation", "title": "Tests"}
        with patch.object(workflow, "bridge", side_effect=bridge):
            first = workflow.command_run(self.store, self.record, command)
            second = workflow.command_run(self.store, self.record, command)
            self.assertEqual(first["id"], second["id"])
            third = workflow.command_run(self.store, self.record, command, retry=True)
            self.assertNotEqual(first["id"], third["id"])
        self.assertEqual(calls, ["run", "result", "run"])
        with self.assertRaises(TaskError):
            workflow.accept_run(self.store, self.record, first, {"request_id": first["request_id"], "run_id": "run", "worktree": "/wrong"})

    def test_setup_failure_does_not_start_agent_and_retry_keeps_target(self):
        self.record.update(setup={"id": "setup", "kind": "setup"}, approved=True)
        save_record(self.store, self.record)
        host = Mock()
        host.agents.return_value = []
        with patch.object(workflow, "command_run", return_value={"state": "failed", "freshness": "current"}):
            with self.assertRaisesRegex(TaskError, "setup"):
                launch(self.store, host, self.record)
        host.call.assert_not_called()
        self.assertEqual(git(self.repo, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_bridge_unavailable_does_not_fall_back_to_shell(self):
        with patch.object(workflow.subprocess, "run") as run, self.assertRaisesRegex(TaskError, "unavailable"):
            workflow.bridge(self.store, "project-commands", "run")
        run.assert_not_called()

    def test_bundle_contract_exports_read_only_and_open_uses_launcher(self):
        bundle = workflow.save_bundle(self.store, "Bundle", [self.record])
        request = {"version": 1, "request_id": "read", "operation": "bundles.get", "id": bundle, "cwd": os.path.normcase(str(self.repo.resolve()))}
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", output):
            self.assertEqual(workflow.bundle_api(self.store.root), 0)
        snapshot = json.loads(output.getvalue())["result"]
        self.assertEqual(snapshot, workflow.bundle_snapshot(self.store.preference(bundle)))
        with patch.object(workflow, "bridge", return_value=[{"cwd": request["cwd"], "state": "succeeded"}]) as bridge:
            result = workflow.open_bundle(self.store, bundle)
        self.assertEqual(result["outcomes"][request["cwd"]]["state"], "succeeded")
        self.assertEqual(bridge.call_args.kwargs["bundle"], snapshot)
        self.assertFalse(bridge.call_args.kwargs["layouts"])
        with self.assertRaises(TaskError):
            workflow.save_bundle(self.store, "Duplicate", [self.record, self.record])

    def test_project_commands_actual_version_one_contract(self):
        definition = {"id": "test", "name": "Tests", "kind": "command", "category": "test", "argv": ["python", "-m", "unittest"]}
        responses = [{"projects": {str(self.repo): {"commands": [definition]}}},
                     {"projects": {str(self.repo): {"commands": [definition]}}},
                     {"socket": "fixture", "generation": "fixture"},
                     {"request": "request", "id": "run", "root": str(self.repo), "state": "starting"}]
        params = {"repository": str(self.repo), "worktree": str(self.repo)}
        with patch.object(workflow, "exchange", side_effect=responses) as exchange:
            commands = workflow.commands_bridge(["/python"], "commands", params)
            result = workflow.commands_bridge(["/python"], "run", {**params, "definition": commands[0]["producer_definition"], "command_id": "test", "request_id": "request"})
        self.assertEqual(result["state"], "pending")
        self.assertEqual(exchange.call_args.args[1], {"version": 1, "action": "run", "root": str(self.repo), "command": "test", "request_id": "request", "reviewed": definition, "session": {"socket": "fixture", "generation": "fixture"}})

    def test_validation_attention_rechecks_local_changes(self):
        self.store.observe("validation:test", self.record["id"], {"kind": "validation", "title": "Tests", "state": "failed", "freshness": "current", "checkout": workflow.fingerprint(self.repo)})
        self.assertEqual(attention.project(self.store, [])[0]["state"], "active")
        (self.repo / "code.txt").write_text("later changes")
        result = attention.project(self.store, [])[0]
        self.assertEqual(result["state"], "unknown")
        self.assertIn("stale", result["title"])

    def test_provider_failure_retains_prior_snapshot(self):
        p = Mock()
        self.record["pr_id"] = "1"
        p.snapshot.return_value = {"id": "1", "head_branch": "main", "head": self.record["target"]["commit"], "title": "PR", "state": "open"}
        with patch.object(forge, "client", return_value=p):
            prior = workflow.refresh_pr(self.store, self.record)
            p.snapshot.side_effect = TaskError("offline")
            with self.assertRaises(TaskError):
                workflow.refresh_pr(self.store, self.record)
        current = next(e for e in self.store.evidence(self.record["id"]) if e["kind"] == "pr")
        self.assertEqual(prior["snapshot"], current["snapshot"])
        self.assertTrue(current["stale"])
        p.snapshot.side_effect = None
        p.snapshot.return_value = {**prior["snapshot"], "errors": ["checks unavailable"]}
        with patch.object(forge, "client", return_value=p), self.assertRaisesRegex(TaskError, "incomplete"):
            workflow.refresh_pr(self.store, self.record)

    def test_automatic_pr_discovery_preserves_explicit_task_record(self):
        record = copy.deepcopy(self.record)
        record.pop("forge")
        p = Mock()
        p.list.return_value = [{"id": "7"}]
        p.snapshot.return_value = {"id": "7", "head_branch": "main", "head": record["target"]["commit"], "title": "Found", "state": "open"}
        with patch.object(forge, "client", return_value=p):
            item = workflow.refresh_pr(self.store, record)
        self.assertEqual(item["snapshot"]["id"], "7")
        self.assertNotIn("forge", record)
        self.assertNotIn("pr_id", self.store.records("handovers")[0])

    def test_ambiguous_pr_discovery_does_not_choose_a_pr(self):
        p = Mock()
        p.list.return_value = [{"id": "1"}, {"id": "2"}]
        with patch.object(forge, "client", return_value=p), self.assertRaisesRegex(TaskError, "Multiple PRs"):
            workflow.refresh_pr(self.store, self.record)
        p.snapshot.assert_not_called()

    def test_no_pr_still_checks_branch_ci(self):
        p = Mock()
        p.list.return_value = []
        p.snapshot.return_value = {"id": "", "head_branch": "main", "head": self.record["target"]["commit"], "title": "main", "state": "branch"}
        with patch.object(forge, "client", return_value=p):
            item = workflow.refresh_pr(self.store, self.record)
        p.snapshot.assert_called_once_with(branch="main")
        self.assertIn("No open PR", workflow.pr_status(item))

    def test_failure_shortcut_excludes_successful_runs_and_never_sends_on_cancel(self):
        import asyncio
        from unittest.mock import AsyncMock
        from luvus_tasks import workflow_ui, operations
        app = Mock(store=self.store)
        app.io = AsyncMock(side_effect=lambda fn: fn(self.store))
        snapshot = {"feedback": [{"id": "comment", "text": "comment"}], "checks": [], "runs": [
            {"id": "1", "state": "failed", "title": "Failed", "revision": "head"},
            {"id": "2", "state": "passed", "title": "Passed", "revision": "head"}]}
        with patch.object(workflow, "refresh_pr", return_value={"snapshot": snapshot}), patch.object(workflow_ui, "select", new=AsyncMock(return_value=None)) as select, patch.object(operations, "followup") as send:
            asyncio.run(workflow_ui.action(app, "work-pr-failures", self.record))
        self.assertEqual([r["id"] for r in select.call_args.args[2]], ["1"])
        send.assert_not_called()

    def test_offline_feedback_inspection_preserves_cache_without_sending(self):
        import asyncio
        from unittest.mock import AsyncMock
        from luvus_tasks import workflow_ui, operations
        app = Mock(store=self.store)
        app.io = AsyncMock(side_effect=lambda fn: fn(self.store))
        feedback = {"id": "comment:1", "text": "Please review", "state": "comment"}
        snapshot = {"url": "https://github.com/example/repo/pull/1", "repository": "example/repo", "head": "head", "feedback": [feedback], "checks": [], "runs": []}
        self.store.observe("pr:" + self.record["id"], self.record["id"], {"kind": "pr", "title": "PR", "state": "open", "snapshot": snapshot})
        with patch.object(workflow, "refresh_pr", side_effect=TaskError("offline")), patch.object(workflow_ui, "select", new=AsyncMock(return_value=feedback)), patch.object(workflow_ui, "show", new=AsyncMock()) as show, patch.object(operations, "followup") as send:
            asyncio.run(workflow_ui.action(app, "work-pr-comments", self.record))
        self.assertIn("Saved feedback", show.call_args.args[1])
        send.assert_not_called()

    def test_polling_coalesces_duplicate_handovers_and_backs_off_failures(self):
        other = copy.deepcopy(self.record)
        other["id"] = str(uuid.uuid4())
        save_record(self.store, other)
        p = Mock()
        p.list.return_value = [{"id": "7"}]
        p.snapshot.return_value = {"id": "7", "head_branch": "main", "head": self.record["target"]["commit"], "title": "Found", "state": "open"}
        with patch("luvus_tasks.pr_links.refresh"), patch.object(forge, "client", return_value=p), patch("time.time", return_value=1000):
            self.assertEqual(workflow.poll_prs(self.store), 1)
            self.assertEqual(workflow.poll_prs(self.store), 0)
        for record in (self.record, other):
            self.assertEqual(next(e for e in self.store.evidence(record["id"]) if e["kind"] == "pr")["snapshot"]["id"], "7")
        p.list.side_effect = TaskError("offline")
        with patch("luvus_tasks.pr_links.refresh"), patch.object(forge, "client", return_value=p), patch("time.time", return_value=1061):
            self.assertEqual(workflow.poll_prs(self.store), 1)
        with patch("luvus_tasks.pr_links.refresh"), patch.object(forge, "client", return_value=p), patch("time.time", return_value=1122):
            self.assertEqual(workflow.poll_prs(self.store), 0)
        self.assertEqual(p.snapshot.call_count, 1)

    def test_sidebar_pr_status_uses_current_revision_and_exact_shortcuts(self):
        from luvus_tasks.operations import dashboard
        item = self.store.observe("pr:" + self.record["id"], self.record["id"], {"kind": "pr", "title": "PR", "state": "open", "snapshot": {
            "id": "7", "state": "open", "head": "current", "runs": [{"id": "1", "title": "Build", "state": "failed", "revision": "old"},
                {"id": "2", "title": "Build", "state": "passed", "revision": "current"}]}})
        self.assertIn("head CI passed", workflow.pr_status(item))
        self.assertIn("prior/unknown", workflow.pr_status(item))
        rows, _, _ = dashboard(self.store, [], [], {"mode": "all"}, attention_items=[])
        row = next(r for r in rows if "CI passed" in r["text"])
        self.assertEqual(json.loads(row["value"].removeprefix("dashboard:"))["record"], self.record["id"])
        self.assertEqual(json.loads(row["value"][10:])["action"], "work-linked-prs")
        self.assertNotIn("menu", row)

    def test_sidebar_missing_checkout_and_empty_sections_have_no_dead_pr_actions(self):
        from luvus_tasks.operations import dashboard
        self.record["stage"] = "delivered"
        self.record["target"]["path"] = str(self.root / "missing")
        save_record(self.store, self.record)
        rows, _, _ = dashboard(self.store, [], [], {"mode": "all"}, attention_items=[])
        self.assertFalse(any(r["text"] in ("Needs attention · 0", "Working · 0", "Drafts · 0") for r in rows))
        self.assertFalse(any("Checkout unavailable" in r["text"] for r in rows))
        self.assertTrue(any(r.get("value") == ticket_key(self.record["ticket"]) for r in rows))

    def test_large_draft_history_has_bounded_rows_and_keeps_unprepared_drafts_in_scope(self):
        from luvus_tasks.operations import dashboard
        records = [{**self.record, "id": str(i), "target": None, "inputs": {"repo": str(self.repo)}} for i in range(250)]
        with patch.object(self.store, "records", return_value=records):
            rows, _, _ = dashboard(self.store, [], [], {"mode": "workspace", "repo": str(self.repo)}, attention_items=[])
        self.assertLess(len(rows), 20)
        self.assertTrue(any(m["title"] == "Browse all 250 handovers" for r in rows for m in r.get("menu", [])))
        self.assertEqual(sum(r.get("value", "").startswith('dashboard:') and set(json.loads(r["value"][10:])) == {"record"} for r in rows), 1)
        self.assertIsNone(records[0]["target"])

    def test_sidebar_prioritizes_current_input_and_does_not_duplicate_handover_cards(self):
        from luvus_tasks.operations import dashboard
        other = copy.deepcopy(self.record)
        other.update(id=str(uuid.uuid4()), stage="delivered")
        save_record(self.store, other)
        conditions = [{"id": "urgent", "handover": self.record["id"], "state": "active", "source": "agent-input", "title": "Needs input"},
                      {"id": "old", "handover": other["id"], "state": "stale", "source": "ci", "title": "Old failure"}]
        rows, _, _ = dashboard(self.store, [], [], {"mode": "all"}, attention_items=conditions)
        main = [json.loads(r["value"].removeprefix("dashboard:")) for r in rows if r.get("value", "").startswith("dashboard:")]
        cards = [r["record"] for r in main if set(r) == {"record"}]
        self.assertEqual(cards, [self.record["id"]])  # one branch row, current input wins

    def test_setup_launch_review_defaults_to_no_setup_and_cancel_preserves_draft(self):
        import asyncio
        from luvus_tasks.console import Cockpit
        from luvus_tasks import operations
        from unittest.mock import AsyncMock
        config = self.store.config()
        config["integrations"] = {"project-commands": ["/python"]}
        self.store.save_config(config)
        app = Mock(store=self.store)
        app.io = AsyncMock(side_effect=lambda fn: fn(self.store))
        app.form = AsyncMock(return_value=None)
        result = {"record": self.record, "dirty": "", "images": False}
        with patch.object(operations, "preflight", return_value=result), patch("luvus_tasks.console.default_base", return_value="main"), patch.object(workflow, "commands", return_value=[{"id": "setup", "kind": "setup", "title": "Setup"}]):
            asyncio.run(Cockpit.review_launch(app, self.record))
        setup = next(f for f in app.form.call_args.args[1] if f[0] == "setup")
        self.assertEqual(setup[2], "")
        self.assertFalse(self.record.get("approved"))

    def test_declared_overlap_is_separate_from_disjoint_scopes(self):
        data = workflow.overlaps([{"id": "a", "paths": ["src/a/**"]}, {"id": "b", "paths": ["src/b/**"]}, {"id": "c", "paths": ["src/a/file.py"]}])
        self.assertEqual(data[0]["state"], "no overlap found in declared scopes")
        self.assertEqual(data[1]["matches"][0]["kind"], "declared overlap")

    def test_review_packet_is_frozen_and_launch_is_fail_closed(self):
        item = review.prepare(self.store, self.record, self.record["target"]["commit"])
        (self.repo / "code.txt").write_text("later")
        self.assertEqual((Path(item["folder"]) / "source/code.txt").read_text(), "original")
        host = Mock()
        with patch.object(review, "capability", side_effect=TaskError("unsupported")), self.assertRaises(TaskError):
            review.start(self.store, host, item)
        host.call.assert_not_called()

    def test_review_probe_uses_explicit_profile_and_rejects_failed_enforcement(self):
        help_text = "--ignore-user-config --ignore-rules read-only --ephemeral --permission-profile"
        with patch.object(review.shutil, "which", return_value="/codex"), patch.object(review.subprocess, "run") as run:
            run.side_effect = [Mock(stdout=help_text), Mock(stdout=help_text), Mock(returncode=0)]
            self.assertEqual(review.capability(), "/codex")
            argv = run.call_args.args[0]
            self.assertIn("--permission-profile", argv)
            self.assertIn('permissions.tasks-review={filesystem={"/"="read"},network={enabled=false}}', argv)
            run.side_effect = [Mock(stdout=help_text), Mock(stdout=help_text), Mock(returncode=1)]
            with self.assertRaisesRegex(TaskError, "verification failed"):
                review.capability()

    def test_notifications_only_once_and_not_for_native_agents(self):
        self.store.set_preference("attention-notifications", True)
        item = {"id": "ci", "source": "ci", "state": "active", "signature": "same", "task": "T", "title": "failed"}
        host = Mock()
        attention.notifications(self.store, host, [item, {**item, "id": "agent", "source": "agent-input"}])
        attention.notifications(self.store, host, [item])
        self.assertEqual(host.call.call_count, 1)


class ProviderShapeTests(unittest.TestCase):
    def test_github_ci_paths_are_revision_scoped_and_neutral_is_unknown(self):
        p = forge.GitHubPR.__new__(forge.GitHubPR)
        p.get = Mock(return_value={"head": "sha", "id": "1", "state": "open"})
        p.api = Mock(return_value=[])
        def pages(path, key):
            if key == "check_runs":
                self.assertIn("/commits/sha/", path)
                return [{"id": 1, "name": "Lint", "conclusion": "neutral", "status": "completed", "head_sha": "sha", "output": {}}]
            return []
        p.pages = pages
        self.assertEqual(p.snapshot("1")["checks"][0]["state"], "unknown")

    def test_azure_merge_validation_is_not_source_head(self):
        p = forge.AzurePR.__new__(forge.AzurePR)
        p.repo, p.git = "repo", "git/repositories/repo"
        p.repository = {"project": {"id": "project"}}
        p.get = Mock(return_value={"head": "head", "merge": "merge", "head_branch": "feature", "reviewers": [], "url": "https://dev.azure.com/o/p/pr/1"})
        def pages(path, query=None):
            if path == "build/builds":
                return [{"id": 1, "definition": {"id": 1}, "sourceVersion": "merge", "result": "succeeded", "status": "completed"}] if query["branchName"].startswith("refs/pull/") else []
            return []
        p.pages = pages
        result = p.snapshot("1")
        self.assertEqual(result["runs"][0]["association"], "merge")
        self.assertEqual(result["runs"][0]["revision"], "merge")


if __name__ == "__main__":
    unittest.main()
