import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent_launch as agent
import launcher
import project_launcher as project


class AgentLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="agent-launch-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo space ✓"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "initial")
        self.git("branch", "feature")
        self.config = self.root / "config" / "presets.json"
        self.config.parent.mkdir()
        self.preset = {"name": "Test", "command": "python3 -i"}
        self.config.write_text(json.dumps([self.preset]))
        self.calls = []

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.STDOUT, text=True).strip()

    def rpc(self, method, **params):
        self.calls.append((method, params))
        if method == "workspace.list":
            return {"workspaces": []}
        return {"pane_id": "1"}

    def test_new_pinned_worktree_and_exact_tab_directory(self):
        dest = self.root / "new tree $ ✓"
        plan = agent.prepare(str(self.repo), "new", "main", "work/new", str(dest))
        agent.execute(self.config, plan, self.preset, "new", self.rpc)
        self.assertEqual(project.git(dest, "branch", "--show-current"), "work/new")
        self.assertEqual(self.git("branch", "--show-current"), "main")
        params = next(p for m, p in reversed(self.calls) if m == "terminal.backend.create")
        self.assertEqual(params["cwd"], project.canonical(dest))
        self.assertEqual(params["command"][params["command"].index("--cwd") + 1], project.canonical(dest))
        with self.assertRaisesRegex(ValueError, "already dispatched"):
            agent.execute(self.config, plan, self.preset, "new", self.rpc)
        self.assertEqual(sum(m == "terminal.backend.create" for m, _ in self.calls), 1)

    def test_existing_branch_create_and_reuse(self):
        dest = self.root / "existing"
        plan = agent.prepare(str(self.repo), "existing", branch="feature", destination=str(dest))
        agent.execute(self.config, plan, self.preset, "first", self.rpc)
        before = self.git("worktree", "list", "--porcelain")
        reused = agent.prepare(str(self.repo), "existing", branch="feature")
        self.assertTrue(reused["reuse"])
        agent.execute(self.config, reused, self.preset, "second", self.rpc)
        self.assertEqual(self.git("worktree", "list", "--porcelain"), before)
        with self.assertRaisesRegex(ValueError, "elsewhere"):
            agent.prepare(str(self.repo), "switch", branch="feature")

    def test_switch_dirty_checkout_and_stale_ref(self):
        (self.repo / "untracked").write_text("keep")
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            agent.prepare(str(self.repo), "switch", branch="feature")
        (self.repo / "untracked").unlink()
        plan = agent.prepare(str(self.repo), "switch", branch="feature")
        agent.execute(self.config, plan, self.preset, "switch", self.rpc)
        self.assertEqual(self.git("branch", "--show-current"), "feature")
        plan = agent.prepare(str(self.repo), "new", branch="next", destination=str(self.root / "next"))
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            agent.execute(self.config, plan, self.preset, "stale", self.rpc)
        self.assertFalse((self.root / "next").exists())

    def test_known_rejection_retries_only_terminal_and_uncertain_does_not(self):
        dest = self.root / "retry"
        plan = agent.prepare(str(self.repo), "new", branch="retry", destination=str(dest))
        def reject(method, **params):
            if method == "terminal.backend.create":
                raise launcher.RpcError({"message": "rejected", "dispatch": "not_started"})
            return self.rpc(method, **params)
        with self.assertRaises(launcher.RpcError):
            agent.execute(self.config, plan, self.preset, "retry", reject)
        before = self.git("worktree", "list", "--porcelain")
        agent.execute(self.config, plan, self.preset, "retry", self.rpc)
        self.assertEqual(before, self.git("worktree", "list", "--porcelain"))
        plan = agent.prepare(str(self.repo), "current")
        def timeout(method, **params):
            if method == "terminal.backend.create":
                raise ValueError("timed out")
            return self.rpc(method, **params)
        with self.assertRaisesRegex(ValueError, "timed out"):
            agent.execute(self.config, plan, self.preset, "uncertain", timeout)
        with self.assertRaisesRegex(ValueError, "already dispatched"):
            agent.execute(self.config, plan, self.preset, "uncertain", self.rpc)

    def test_invalid_paths_refs_deleted_preset_and_cancel_are_read_only(self):
        for branch, base, destination in [("-bad", "HEAD", str(self.root / "x")), ("bad name", "HEAD", str(self.root / "x")),
                                           ("new", "missing", str(self.root / "x")), ("new", "HEAD", str(self.repo)),
                                           ("new", "HEAD", str(self.repo / "nested")), ("new", "HEAD", "relative")]:
            with self.assertRaises(ValueError):
                agent.prepare(str(self.repo), "new", base, branch, destination)
        plan = agent.prepare(str(self.repo), "new", branch="cancel", destination=str(self.root / "cancel"))
        self.assertFalse((self.root / "cancel").exists())
        self.config.write_text("[]")
        with self.assertRaisesRegex(ValueError, "deleted"):
            agent.execute(self.config, plan, self.preset, "deleted", self.rpc)
        self.assertFalse((self.root / "cancel").exists())

    def test_current_non_git_and_menu_flags(self):
        folder = self.root / "non git"
        folder.mkdir()
        plan = agent.prepare(str(folder), "current")
        agent.execute(self.config, plan, self.preset, "folder", self.rpc)
        self.assertEqual(next(p for m, p in reversed(self.calls) if m == "terminal.backend.create")["cwd"], project.canonical(folder))
        rendered = launcher.render_manifest([*launcher.DEFAULTS, self.preset])
        self.assertEqual(rendered.count('contexts = ["pane", "workspace", "agent"]'), 6)
        self.assertIn("Open Codex — skip permissions", rendered)
        self.assertIn("Open Muse — YOLO", rendered)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", rendered)
        self.assertIn("muse --yolo", rendered)


if __name__ == "__main__":
    unittest.main()
