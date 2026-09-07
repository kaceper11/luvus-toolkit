import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("LUVUS_BIN_PATH", None)

    def test_atomic_presets_and_conflicting_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presets.json"
            original = launcher.load_presets(path)
            saved = [{"name": "Quoted", "command": "echo 'spaces $ ` ✓'"}]
            launcher.save_presets(path, saved, original)
            self.assertEqual(launcher.load_presets(path), saved)
            with self.assertRaisesRegex(ValueError, "changed"):
                launcher.save_presets(path, [], original)
            self.assertEqual(launcher.load_presets(path), saved)
            path.write_text("broken JSON", encoding="utf-8")
            with self.assertRaises(ValueError):
                launcher.save_presets(path, [], saved)
            self.assertEqual(path.read_text(), "broken JSON")

    def test_invalid_presets(self):
        for value in ({}, [None], [{"name": "", "command": "codex"}],
                      [{"name": "bad\033", "command": "codex"}],
                      [{"name": "one", "command": "x"}, {"name": "ONE", "command": "y"}]):
            with self.assertRaises(ValueError):
                launcher.validate(value)

    def test_inactive_workspace_ignores_focused_pane_and_creates_once(self):
        with tempfile.TemporaryDirectory(prefix="launcher space ") as directory:
            calls = []
            def rpc(method, **params):
                calls.append((method, params))
                if method == "workspace.get":
                    return {"cwd": directory}
                return {"pane_id": "99"}
            context = {"invocation_source": "menu:workspace",
                       "workspace": {"id": "2", "cwd": directory},
                       "pane": {"id": "unrelated", "cwd": "/wrong"}}
            config = Path(directory) / "config with spaces.json"
            launcher.open_launcher(context, config, rpc)
            self.assertEqual(calls[0], ("workspace.get", {"workspace": "2"}))
            self.assertEqual([m for m, _ in calls].count("terminal.backend.create"), 1)
            params = next(p for m, p in reversed(calls) if m == "terminal.backend.create")
            self.assertEqual(params["cwd"], directory)
            self.assertEqual(params["placement"], {"kind": "workspace"})
            command = params["command"]
            self.assertEqual(command[command.index("--config") + 1], str(config))
            self.assertEqual(command[command.index("--cwd") + 1], directory)

    def test_pane_uses_its_actual_workspace_and_captured_directory(self):
        with tempfile.TemporaryDirectory() as root:
            cwd = Path(root) / "sub folder"
            cwd.mkdir()
            calls = []
            def rpc(method, **params):
                calls.append((method, params))
                return {"workspace_id": "stable-id"} if method == "pane.get" else {"cwd": root}
            context = {"invocation_source": "menu:agent", "pane": {"id": "7", "cwd": str(cwd)}}
            self.assertEqual(launcher.launch_target(context, rpc), (root, str(cwd)))
            self.assertEqual(calls[-1], ("workspace.get", {"workspace_id": "stable-id"}))

    def test_shell_preserves_command_and_windows_uses_powershell(self):
        command = "echo 'quotes $ ` with spaces'"
        self.assertEqual(launcher.shell_command(command, windows=False)[-1], command)
        with patch("launcher.shutil.which", return_value="powershell.exe"):
            self.assertEqual(launcher.shell_command(command, windows=True),
                             ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command])

    def test_cancel_and_launch_in_directory_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="launcher space ") as directory:
            config = Path(directory) / "presets.json"
            config.write_text(json.dumps([{"name": "Directory", "command": "pwd"}]))
            command = [sys.executable, str(Path(launcher.__file__).resolve()),
                       "--picker", "--cwd", directory, "--config", str(config)]
            result = subprocess.run(command, input="1\nq\n", text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Exit status: 0", result.stdout)
            self.assertIn(directory, result.stdout)
            result = subprocess.run(command, input="q\n", text=True, capture_output=True, timeout=15)
            self.assertNotIn("Exit status", result.stdout)

    def test_ctrl_c_waits_for_tool_before_returning(self):
        with patch("launcher.subprocess.Popen") as spawn:
            spawn.return_value.wait.side_effect = [KeyboardInterrupt, 130]
            self.assertEqual(launcher.run_tool("codex", os.getcwd()), 130)
            self.assertEqual(spawn.return_value.wait.call_count, 2)

    def test_missing_command_returns_nonzero(self):
        result = subprocess.run(launcher.shell_command("luvus_launcher_nonexistent_987654"),
                                capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)

    def test_generated_menu_preserves_quotes_and_empty_management(self):
        preset = {"name": 'Codex "fast" ✓', "command": "codex --model 'example'"}
        manifest = launcher.render_manifest([preset])
        self.assertIn('title = ' + json.dumps('Open Codex "fast" ✓ in new tab', ensure_ascii=False), manifest)
        command_line = next(line for line in manifest.splitlines() if line.startswith("command = "))
        argv = json.loads(command_line.removeprefix("command = "))
        self.assertEqual(json.loads(argv[-1]), preset)
        self.assertIn('contexts = ["pane", "workspace", "agent"]', manifest)
        self.assertNotIn("[[docks]]", manifest)
        self.assertIn("Manage saved tools…", launcher.render_manifest([]))
        self.assertNotIn('id = "open-', launcher.render_manifest([]))
        self.assertIn('title = "Agent Launcher…"', launcher.render_manifest([]))
        identity = next(line for line in manifest.splitlines() if line.startswith('id = "open-'))
        self.assertNotIn(identity, launcher.render_manifest([{**preset, "name": "New name"}]))

    def test_stale_menu_selection_never_creates_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "presets.json"
            config.write_text("[]")
            with patch("launcher.call") as rpc:
                with self.assertRaisesRegex(ValueError, "changed or was deleted"):
                    launcher.menu_action("run", config, {}, json.dumps(launcher.DEFAULTS[0]), rpc)
                rpc.assert_not_called()

    def test_add_edit_delete_and_cancel_form(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            config = Path(directory) / "presets.json"
            with patch("builtins.input", side_effect=["Example", "echo hi", "y"]):
                launcher.form(config, "add")
            added = launcher.load_presets(config)[2]
            with patch("builtins.input", side_effect=["New", "pwd", "n"]):
                launcher.form(config, "edit", added)
            self.assertEqual(launcher.load_presets(config)[2], added)
            with patch("builtins.input", side_effect=["New", "pwd", "y"]):
                launcher.form(config, "edit", added)
            edited = launcher.load_presets(config)[2]
            self.assertEqual(edited, {"name": "New", "command": "pwd"})
            with patch("builtins.input", side_effect=["n"]):
                launcher.form(config, "delete", edited)
            self.assertEqual(len(launcher.load_presets(config)), 3)
            with patch("builtins.input", side_effect=["y"]):
                launcher.form(config, "delete", edited)
            self.assertEqual(launcher.load_presets(config), launcher.DEFAULTS)

    def test_menu_run_skips_picker_and_uses_clicked_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "presets.json"
            calls = []
            def rpc(method, **params):
                calls.append((method, params))
                return {"cwd": directory, "workspace_id": "ws"}
            context = {"invocation_source": "menu:workspace", "workspace": {"id": "0", "cwd": directory},
                       "pane": {"id": "1", "cwd": directory}}
            launcher.menu_action("run", config, context, json.dumps(launcher.DEFAULTS[0]), rpc)
            self.assertEqual([m for m, _ in calls].count("terminal.backend.create"), 1)
            command = next(p for m, p in reversed(calls) if m == "terminal.backend.create")["command"]
            self.assertIn("--run", command)
            self.assertNotIn("--picker", command)

    def test_refresh_on_save(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "presets.json"
            with patch.dict(os.environ, {"LUVUS_BIN_PATH": "test"}), patch("launcher.refresh_menu") as refresh:
                launcher.save_presets(config, [], launcher.DEFAULTS)
                refresh.assert_called_once_with(config)

    def test_refresh_failure_restores_registration_and_keeps_presets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "presets.json"
            config.write_text("[]")
            manifest = root / "luvus-module.toml"
            manifest.write_text("old manifest")
            links = []
            def rpc(method, **params):
                if method == "module.info":
                    return {"root": str(root), "enabled": True}
                if method == "module.list":
                    return {"modules": []}
                if method == "module.link":
                    links.append(manifest.read_text())
                    if len(links) == 1:
                        raise ValueError("test registration failure")
                return {}
            with self.assertRaisesRegex(ValueError, "Refresh tool menu"):
                launcher.refresh_menu(config, rpc, root)
            self.assertEqual(links[-1], "old manifest")
            self.assertEqual(config.read_text(), "[]")
            self.assertFalse((root / "menu-refresh.lock").exists())

    def test_successful_relink_with_lost_reply_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "presets.json"
            manifest = root / "luvus-module.toml"
            manifest.write_text("old manifest")
            links = []
            def rpc(method, **params):
                if method == "module.info":
                    return {"root": str(root), "enabled": True}
                if method == "module.list":
                    return {"modules": [{"id": "kacper.toolkit"}]}
                if method == "module.link":
                    links.append(params)
                    raise ValueError("reply lost")
                return {}
            with self.assertRaises(ValueError):
                launcher.refresh_menu(config, rpc, root)
            self.assertEqual(len(links), 1)
            self.assertIn("Open Codex — skip permissions", manifest.read_text())

    @unittest.skipIf(os.name == "nt", "POSIX controlling-terminal regression")
    def test_picker_keeps_controlling_terminal_after_tool_exit(self):
        import pty
        import select
        import signal
        import time
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "presets.json"
            config.write_text(json.dumps([{"name": "Check", "command": "printf TOOL_FINISHED"}]))
            master, slave = pty.openpty()
            # Establish the controlling terminal in a fresh interpreter; forkpty
            # can deadlock after macOS libraries initialize process-global locks.
            command = [sys.executable, str(Path(launcher.__file__).resolve()),
                       "--picker", "--plain", "--cwd", directory, "--config", str(config)]
            child = subprocess.Popen([sys.executable, "-c",
                "import fcntl,termios,os,sys; fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv(sys.argv[1],sys.argv[1:])", *command],
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            reaped = False
            try:
                os.write(master, b"1\n")
                output = b""
                deadline = time.monotonic() + 10
                while output.count(b"Enter a number") < 2 and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 8192)
                self.assertIn(b"Exit status: 0", output)
                # A second launch proves input() still owns a usable foreground PTY.
                os.write(master, b"1\n")
                while output.count(b"Exit status: 0") < 2 and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 8192)
                self.assertEqual(output.count(b"Exit status: 0"), 2)
                os.write(master, b"q\n")
                while time.monotonic() < deadline:
                    status = child.poll()
                    if status is not None:
                        reaped = True
                        self.assertEqual(status, 0)
                        break
                    if select.select([master], [], [], .02)[0]:
                        try: os.read(master, 8192)
                        except OSError: pass  # EOF may be reported as EIO on a PTY.
                self.assertTrue(reaped, "Picker did not close")
            finally:
                os.close(master)
                if not reaped:
                    child.kill()
                    child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
