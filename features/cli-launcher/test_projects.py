"""Project behavior checks: temporary files and fake UHP, never production Luvus."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import launcher
import project_launcher as project


class Native:
    def __init__(self):
        self.calls = []
        self.terminals = []
        self.workspaces = []
        self.generation = "a" * 32
        self.next_id = 1
        self.fail_create = None
        self.lose_reply = False

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if method == "workspace.list":
            return {"workspaces": self.workspaces}
        if method == "terminal.backend.inventory":
            return {"server_generation": self.generation, "terminals": deepcopy(self.terminals), "truncated": False}
        if method == "terminal.backend.validate":
            return {"state": "alive"}
        if method == "workspace.open":
            if not any(w["cwd"] == params["path"] for w in self.workspaces):
                self.workspaces.append({"cwd": params["path"], "workspace_id": "ws" + str(len(self.workspaces))})
            return {}
        if method == "terminal.backend.create":
            if self.fail_create == self.next_id:
                raise ValueError("simulated lost response before completion")
            number = str(self.next_id)
            self.next_id += 1
            cwd = params["cwd"]
            if params["placement"]["kind"] == "workspace":
                self("workspace.open", path=cwd)
                tab = "tab" + number
            else:
                anchor = next(t for t in self.terminals if t["terminal_id"] == params["placement"]["of_terminal"]["terminal_id"])
                tab = anchor["tab_id"]
            ws = next(w for w in self.workspaces if w["cwd"] == cwd)
            terminal = {"server_generation": self.generation, "pane_id": number, "terminal_id": number.zfill(32),
                        "workspace": {"root": cwd}, "workspace_id": ws["workspace_id"], "tab_id": tab, "label": params["label"]}
            self.terminals.append(terminal)
            if self.lose_reply:
                self.lose_reply = False
                raise ValueError("reply lost after creating")
            return terminal
        if method == "pane.get":
            return next(t for t in self.terminals if t["pane_id"] == str(params["pane"]))
        if method == "layout.apply":
            leaves = []
            project.map_tree(params["tree"], lambda leaf: leaves.append(str(leaf)) or leaf)
            actual = [t["pane_id"] for t in self.terminals if t["tab_id"] == params["tab_id"]]
            if set(leaves) != set(actual):
                raise ValueError("layout must include every tab pane")
        return {}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="project space ✓ ")
        self.addCleanup(self.temp.cleanup)
        self.cwd = project.canonical(self.temp.name)
        self.path = Path(self.cwd) / "config" / "projects.json"
        self.identity = project.identity(self.cwd)
        self.native = Native()
        self.item = {"name": "Dev", "roles": {"one": {"shell": [sys.executable, "-i"]},
                                              "two": {"shell": [sys.executable, "-i"]}},
                     "tree": {"Split": {"axis": 0, "ratio": .5, "a": {"Leaf": "one"}, "b": {"Leaf": "two"}}}, "commands": []}
        self.config = deepcopy(project.EMPTY)
        self.config["repositories"][self.identity["repository"]] = {"arrangements": {"dev": self.item},
                                                                  "links": {"docs": {"name": "Docs", "destination": "https://example.com/docs"}}}
        project.save_config(self.path, self.config, deepcopy(project.EMPTY))

    def run_arrangement(self, **kwargs):
        return project.run_arrangement(self.path, self.identity, "dev", self.item, rpc=self.native, **kwargs)

    def test_scope_overrides_and_conflicting_writes(self):
        changed = deepcopy(self.config)
        changed["worktrees"][self.cwd] = {"links": {"docs": None, "local": {"name": "App", "destination": "http://localhost:3000"}}}
        project.save_config(self.path, changed, self.config)
        self.assertEqual(list(project.effective(project.load_config(self.path), self.identity, "links")), ["local"])
        with self.assertRaisesRegex(ValueError, "changed"):
            project.save_config(self.path, self.config, self.config)
        self.assertEqual(project.load_config(self.path), changed)
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            project.save_config(self.path, self.config, changed)
        self.assertEqual(self.path.read_text(), "broken")

    def test_git_worktrees_share_repository_but_not_checkout(self):
        repo = Path(self.cwd) / "repo"
        repo.mkdir()
        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        git("init", "-q")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "initial")
        other = Path(self.cwd) / "other"
        git("worktree", "add", "-qb", "other", str(other))
        first, second = project.identity(str(repo)), project.identity(str(other))
        self.assertEqual(first["repository"], second["repository"])
        self.assertNotEqual(first["worktree"], second["worktree"])

    def test_links_reject_code_and_stale_selections(self):
        for value in ("javascript:alert(1)", "file:///etc/passwd", "https://u:p@example.com", "https://example.com:bad", "https://example.com/\nrun"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                project.destination(value, self.cwd)
        script = Path(self.cwd) / "evil.sh"
        script.write_text("echo no")
        with self.assertRaises(ValueError):
            project.destination(str(script), self.cwd)
        doc = Path(self.cwd) / "quotes $ ` ✓.md"
        doc.write_text("doc")
        self.assertEqual(project.destination(doc.name, self.cwd), doc.resolve().as_uri())
        link = self.config["repositories"][self.cwd]["links"]["docs"]
        opened = []
        project.open_link(self.path, self.identity, "docs", link, opener=opened.append)
        self.assertEqual(opened, [link["destination"]])
        with self.assertRaisesRegex(ValueError, "changed"):
            project.open_link(self.path, self.identity, "docs", {**link, "destination": "https://evil.invalid"}, opener=opened.append)
        self.assertEqual(len(opened), 1)

    def test_service_url_is_read_only_and_worktree_scoped(self):
        link = {"name": "App", "service": "web"}
        self.config["worktrees"][self.cwd] = {"links": {"app": link}}
        project.save_config(self.path, self.config, project.load_config(self.path))
        requests = []
        def owner(config, module, operation, cwd, **params):
            requests.append((module, operation, cwd, params))
            return {"state": "running", "fresh": True, "urls": ["http://localhost:3210"]}
        opened = []
        project.open_link(self.path, self.identity, "app", link, rpc=owner, opener=opened.append)
        self.assertEqual(requests, [("commands", "services.urls", self.cwd, {"id": "web"})])
        self.assertEqual(opened, ["http://localhost:3210"])
        with self.assertRaisesRegex(ValueError, "stale"):
            project.open_link(self.path, self.identity, "app", link, rpc=lambda *a, **k: {"fresh": False}, opener=opened.append)

    def test_launch_focus_repeat_and_explicit_copy(self):
        first = self.run_arrangement()
        second = self.run_arrangement()
        self.assertEqual(first, second)
        self.assertEqual(len(self.native.terminals), 2)
        self.assertEqual(sum(m == "layout.apply" for m, _ in self.native.calls), 1)
        self.run_arrangement(new_copy=True)
        self.assertEqual(len(self.native.terminals), 4)
        self.assertTrue(all(not p["focus"] for m, p in self.native.calls if m == "terminal.backend.create"))

    def test_lost_reply_reconciles_without_duplicate(self):
        self.native.lose_reply = True
        with self.assertRaisesRegex(ValueError, "reply lost"):
            self.run_arrangement()
        self.run_arrangement()
        self.assertEqual(len(self.native.terminals), 2)

    def test_uncertain_step_never_replays_or_repeats_success(self):
        self.native.fail_create = 2
        with self.assertRaises(ValueError):
            self.run_arrangement()
        self.native.fail_create = None
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.run_arrangement(recover=True)
        self.assertEqual(len(self.native.terminals), 1)

    def test_missing_first_pane_recovers_into_surviving_tab(self):
        self.run_arrangement()
        self.native.terminals.pop(0)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.run_arrangement()
        self.run_arrangement(recover=True)
        self.assertEqual(len(self.native.terminals), 2)
        self.assertEqual(len({t["tab_id"] for t in self.native.terminals}), 1)

    def test_server_restart_stale_config_and_concurrency_stop_writes(self):
        self.run_arrangement()
        self.native.generation = "b" * 32
        with self.assertRaisesRegex(ValueError, "restarted"):
            self.run_arrangement()
        changed = deepcopy(self.item)
        changed["name"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed"):
            project.run_arrangement(self.path, self.identity, "dev", changed, rpc=self.native)
        with project.locked(self.path.with_name("arrangement-runs.lock")):
            with self.assertRaisesRegex(ValueError, "active"):
                self.run_arrangement()
        self.assertEqual(len(self.native.terminals), 2)

    def test_layout_failure_keeps_panes_and_retries_only_layout(self):
        native = self.native
        def failed(method, **params):
            if method == "layout.apply":
                raise ValueError("layout conflict")
            return native(method, **params)
        with self.assertRaisesRegex(ValueError, "conflict"):
            project.run_arrangement(self.path, self.identity, "dev", self.item, rpc=failed)
        self.run_arrangement()
        self.assertEqual(len(native.terminals), 2)

    def test_owner_steps_are_not_repeated(self):
        self.item["commands"] = [{"id": "web", "revision": "1", "kind": "service"}, {"id": "tests", "revision": "2", "kind": "command"}]
        project.save_config(self.path, self.config, project.load_config(self.path))
        calls = []
        def owner(config, module, operation, cwd, **params):
            calls.append((operation, cwd))
            if operation == "commands.describe":
                return next(x for x in self.item["commands"] if x["id"] == params["id"])
            return {"state": "running", "run_id": "run"}
        self.run_arrangement(owner=owner)
        self.run_arrangement(owner=owner)
        self.assertEqual(calls.count(("services.ensure", self.cwd)), 1)
        self.assertEqual(calls.count(("commands.run", self.cwd)), 1)

    def test_bundle_partial_recovery_has_no_branch_or_service_mutation(self):
        missing = str(Path(self.cwd) / "missing")
        bundle = {"id": "bundle", "revision": "1", "name": "Example", "members": [
            {"cwd": self.cwd, "branch": {"kind": "none", "value": ""}},
            {"cwd": missing, "branch": {"kind": "none", "value": ""}}]}
        owner = lambda *a, **k: deepcopy(bundle)
        results = project.open_bundle(self.path, self.identity, bundle, rpc=self.native, owner=owner)
        self.assertEqual([x["state"] for x in results], ["succeeded", "failed"])
        Path(missing).mkdir()
        results = project.open_bundle(self.path, self.identity, bundle, rpc=self.native, owner=owner)
        self.assertEqual([x["state"] for x in results], ["succeeded", "succeeded"])
        self.assertEqual(sum(m == "workspace.open" for m, _ in self.native.calls), 2)
        self.assertEqual(len(self.native.terminals), 0)

    def test_provider_json_roundtrip_and_identity_validation(self):
        program = "import sys,json; r=json.load(sys.stdin); print(json.dumps({**r, 'result': {'ok':True}}))"
        config = deepcopy(project.EMPTY)
        config["providers"]["tasks"] = [sys.executable, "-c", program]
        self.assertEqual(project.provider(config, "tasks", "bundles.list", self.cwd), {"ok": True})
        config["providers"]["tasks"][-1] = "print('{}')"
        with self.assertRaisesRegex(ValueError, "mismatch"):
            project.provider(config, "tasks", "bundles.list", self.cwd)

    def test_missing_owner_prevents_partial_arrangement_launch(self):
        self.item["commands"] = [{"id": "web", "revision": "1", "kind": "service"}]
        project.save_config(self.path, self.config, project.load_config(self.path))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.run_arrangement()
        self.assertEqual(self.native.terminals, [])

    def test_known_creation_rejection_can_retry_only_failed_step(self):
        def rpc(method, **params):
            if method == "terminal.backend.create" and self.native.next_id == 2:
                raise launcher.RpcError({"dispatch": "rejected", "message": "spawn rejected"})
            return self.native(method, **params)
        with self.assertRaisesRegex(ValueError, "rejected"):
            project.run_arrangement(self.path, self.identity, "dev", self.item, rpc=rpc)
        self.run_arrangement(recover=True)
        self.assertEqual(len(self.native.terminals), 2)
        self.assertEqual(self.native.terminals[0]["pane_id"], "1")

    def test_failed_owner_retry_retains_success_and_unknown_does_not_retry(self):
        self.item["commands"] = [{"id": "web", "revision": "1", "kind": "service"}, {"id": "tests", "revision": "1", "kind": "command"}]
        project.save_config(self.path, self.config, project.load_config(self.path))
        calls = []
        fail = True
        def owner(config, module, operation, cwd, **params):
            if operation == "commands.describe":
                return next(x for x in self.item["commands"] if x["id"] == params["id"])
            calls.append((operation, params["operation_id"]))
            if fail and operation == "commands.run":
                return {"state": "failed", "dispatch": "executed", "run_id": "failed-test"}
            return {"state": "running", "run_id": "run"}
        with self.assertRaisesRegex(ValueError, "failed"):
            self.run_arrangement(owner=owner)
        fail = False
        self.run_arrangement(owner=owner, recover=True)
        self.assertEqual([name for name, _ in calls], ["services.ensure", "commands.run", "commands.run"])
        self.assertNotEqual(calls[1][1], calls[2][1])
        self.assertEqual(len(self.native.terminals), 2)

    def test_completed_arrangement_focus_does_not_require_owner_or_current_tool(self):
        self.run_arrangement()
        self.item["commands"] = [{"id": "web", "revision": "changed", "kind": "service"}]
        project.save_config(self.path, self.config, project.load_config(self.path))
        result = self.run_arrangement(owner=lambda *a, **kw: self.fail("must not invoke provider"))
        self.assertIn("previous", result["notice"])
        self.assertEqual(len(self.native.terminals), 2)

    def test_bundle_revision_branch_and_arrangement_checks(self):
        bundle = {"id": "b", "revision": "1", "name": "Bundle", "members": [
            {"cwd": self.cwd, "branch": {"kind": "branch", "value": "wrong"}, "arrangement": "dev"}]}
        with self.assertRaisesRegex(ValueError, "changed"):
            project.open_bundle(self.path, self.identity, bundle, rpc=self.native, owner=lambda *a, **k: {**bundle, "revision": "2"})
        owner = lambda *a, **k: bundle
        result = project.open_bundle(self.path, self.identity, bundle, layouts=True, rpc=self.native, owner=owner)
        self.assertEqual(result[0]["state"], "failed")
        result = project.open_bundle(self.path, self.identity, bundle, rpc=self.native, owner=owner)
        self.assertEqual(result[0]["state"], "failed")
        self.assertFalse(any(name == "workspace.open" for name, _ in self.native.calls))

    def test_bundle_missing_layout_member_does_not_block_valid_member(self):
        bundle = {"id": "b", "revision": "1", "name": "Bundle", "members": [
            {"cwd": self.cwd + "/missing", "branch": {"kind": "none", "value": ""}, "arrangement": "dev"},
            {"cwd": self.cwd, "branch": {"kind": "none", "value": ""}, "arrangement": "dev"}]}
        result = project.open_bundle(self.path, self.identity, bundle, layouts=True, rpc=self.native,
                                     owner=lambda *a, **k: bundle, arrangement_revisions={self.cwd: project.digest(self.item)})
        self.assertEqual([r["state"] for r in result], ["failed", "succeeded"])
        self.assertEqual(len(self.native.terminals), 2)
        stale = project.open_bundle(self.path, self.identity, bundle, layouts=True, rpc=self.native,
                                    owner=lambda *a, **k: bundle, arrangement_revisions={self.cwd: "stale"})
        self.assertEqual(stale[1]["state"], "failed")
        self.assertEqual(len(self.native.terminals), 2)

    def test_invalid_config_and_layout_never_overwrite(self):
        for item in (["wrong"], {**self.item, "tree": {"Leaf": "one"}},
                     {**self.item, "tree": {"Split": {"axis": 0, "ratio": float("nan"), "a": {}, "b": {}}}}):
            with self.assertRaises(ValueError):
                project.validate_arrangement(item)
        previous = self.path.read_bytes()
        broken = {**self.config, "providers": []}
        with self.assertRaises(ValueError):
            project.save_config(self.path, broken, self.config)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_keyboard_interrupt_releases_lock_and_does_not_replay(self):
        def rpc(method, **params):
            result = self.native(method, **params)
            if method == "terminal.backend.create":
                raise KeyboardInterrupt
            return result
        with self.assertRaises(KeyboardInterrupt):
            project.run_arrangement(self.path, self.identity, "dev", self.item, rpc=rpc)
        self.assertFalse(self.path.with_name("arrangement-runs.lock").exists())
        self.run_arrangement()
        self.assertEqual(len(self.native.terminals), 2)

    def test_cancel_leaves_configuration_unchanged(self):
        before = self.path.read_bytes()
        with patch("builtins.input", side_effect=["r", KeyboardInterrupt]), self.assertRaises(KeyboardInterrupt):
            project.edit_entry(self.path, self.identity, "links", "new", {"name": "New", "destination": "https://example.com"})
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
