"""Explicit opt-in native check in a disposable Luvus home; never production state."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import launcher
import project_launcher as project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    args = parser.parse_args()
    binary = str(args.binary.resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix="ln-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        home = root / "home"
        cwd = root / "project space ✓"
        cwd.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("LUVUS_")}
        env.update(LUVUS_HOME=str(home), LUVUS_BIN_PATH=binary, PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([binary, "server", "start"], env=env, check=True, capture_output=True, timeout=30)
        try:
            with patch.dict(os.environ, env, clear=True):
                module = root / "module"
                module.mkdir()
                for name in ("launcher.py", "project_launcher.py", "launcher_ui.py", "commands_adapter.py", "agent_launch.py"):
                    shutil.copy2(Path(__file__).with_name(name), module / name)
                (module / "luvus-module.toml").write_text(launcher.render_manifest([]), encoding="utf-8")
                launcher.call("module.link", path=str(module))
                info = launcher.call("module.info", id=launcher.MODULE_ID)
                assert info["runnable"] and info["version"] == launcher.VERSION, info
                config_path = root / "config" / "projects.json"
                ident = project.identity(str(cwd))
                item = {"name": "Native check", "roles": {"one": {"shell": [sys.executable, "-i"]},
                                                           "two": {"shell": [sys.executable, "-i"]}},
                        "tree": {"Split": {"axis": 1, "ratio": .4, "a": {"Leaf": "one"}, "b": {"Leaf": "two"}}},
                        "commands": []}
                config = deepcopy(project.EMPTY)
                config["repositories"][ident["repository"]] = {"arrangements": {"check": item}}
                project.save_config(config_path, config, deepcopy(project.EMPTY))
                first = project.run_arrangement(config_path, ident, "check", item)
                second = project.run_arrangement(config_path, ident, "check", item)
                assert first == second
                inv = project.inventory(launcher.call)
                ours = [t for t in inv["terminals"] if (t.get("label") or "").startswith("launcher:")]
                assert len(ours) == 2, ours
                order, tree = project.capture(first["pane_id"], None, "")
                assert len(order) == 2 and tree["Split"]["axis"] == 1, tree
                # The first role is deliberately removed to verify recovery anchors to the survivor.
                record = next(iter(project.read_json(config_path.with_name("arrangement-runs.json"), {}).values()))
                launcher.call("terminal.backend.close", **project.locator(record["roles"]["one"]["terminal"]))
                project.run_arrangement(config_path, ident, "check", item, recover=True)
                inv = project.inventory(launcher.call)
                ours = [t for t in inv["terminals"] if (t.get("label") or "").startswith("launcher:")]
                assert len(ours) == 2, ours
                print("PASS: native manifest validation, creation, captured geometry, repeat focus, and missing-first-pane recovery")
                ui_python = Path(__file__).resolve().parent / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                if not ui_python.exists():
                    raise RuntimeError("Install requirements.txt in .venv to run the dashboard smoke check.")
                ui = launcher.call("terminal.backend.create", cwd=str(cwd), placement={"kind": "workspace"},
                                   focus=False, label="launcher-dashboard-check",
                                   command=[str(ui_python), str(module / "launcher.py"), "--project", "--cwd", str(cwd),
                                            "--config", str(config_path.with_name("presets.json"))])
                waited = subprocess.run([binary, "wait", "output", ui["pane_id"], "--match", "CLI LAUNCHER", "--timeout", "10"],
                                        env=env, capture_output=True, text=True, check=True, timeout=15)
                captured = launcher.call("pane.read", pane=ui["pane_id"], lines=80, source="visible")
                assert "CLI LAUNCHER" in json.dumps(captured), captured
                print("PASS: dashboard starts through the real native terminal entrypoint")
                import agent_launch
                repo = root / "git repo"
                repo.mkdir()
                subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@example.com",
                                "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
                agent_config = root / "agent-config" / "presets.json"
                agent_config.parent.mkdir()
                preset = {"name": "Native Python", "command": "python3 -i"}
                agent_config.write_text(json.dumps([preset]))
                plan = agent_launch.prepare(str(repo), "new", branch="native-test", destination=str(root / "worktree space"))
                created = agent_launch.execute(agent_config, plan, preset, "native-test")
                info = launcher.call("pane.get", pane=created["pane_id"])
                assert Path(info["cwd"]).resolve() == Path(plan["destination"]).resolve(), info
                assert project.git(repo, "branch", "--show-current") == "main"
                assert project.git(plan["destination"], "branch", "--show-current") == "native-test"
                print("PASS: native agent tab starts in its freshly created worktree; source branch stays main")

                # Validate all changed manifests without starting their background hooks.
                parent = Path(__file__).resolve().parent.parent
                for name in ("luvus-tasks", "luvus-git-sidebar", "luvus-project-commands"):
                    launcher.call("module.link", path=str(parent / name), disabled=True)
                git_root = parent / "luvus-git-sidebar"
                git_env = {**env, "LUVUS_SOCKET_PATH": str(home / "luvus.sock"), "LUVUS_MODULE_STATE_DIR": str(root / "git-state")}
                target = {"repo": str(repo), "identity": ["refs/heads/main", project.git(repo, "rev-parse", "HEAD")]}
                hub = launcher.call("terminal.backend.create", cwd=str(repo), placement={"kind": "workspace"}, focus=False,
                    label="git-hub-check", command=[sys.executable, str(git_root / "git_sidebar.py"), "hub-terminal",
                                                   json.dumps(target), json.dumps(git_env)])
                subprocess.run([binary, "wait", "output", hub["pane_id"], "--match", "Git actions", "--timeout", "10"],
                               env=env, check=True, capture_output=True, timeout=15)
                captured = launcher.call("pane.read", pane=hub["pane_id"], lines=80, source="visible")
                assert "Git actions" in json.dumps(captured) and "Traceback" not in json.dumps(captured), captured
                print("PASS: changed module manifests validate; Git hub renders in disposable native tabs")

        finally:
            # Every pane in this separate test home was created by this check.
            subprocess.run([binary, "server", "stop"], env=env, check=True, capture_output=True, timeout=30)


if __name__ == "__main__":
    main()
