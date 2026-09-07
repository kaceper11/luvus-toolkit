"""Opt-in integration smoke test: a disposable Luvus home, no trackers or agents."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    binary = os.environ.get("LUVUS_BIN_PATH") or shutil.which("luvus")
    if not binary:
        raise SystemExit("Luvus is required for this opt-in test.")
    root = Path(__file__).resolve().parents[1]
    # Keep the Unix socket path below the platform's length limit.
    with tempfile.TemporaryDirectory(prefix="lt-smoke-", dir="/tmp" if os.name != "nt" else None) as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith("LUVUS_")}
        env["LUVUS_HOME"] = tmp

        def cli(*args):
            r = subprocess.run([binary, *args], env=env, cwd=root, capture_output=True, text=True, timeout=30)
            if r.returncode:
                raise AssertionError(r.stderr or r.stdout)
            return r.stdout

        def call(method, **params):
            r = subprocess.run([binary, "uhp", "proxy"], env=env, cwd=root, capture_output=True, text=True, timeout=30,
                               input=json.dumps({"id": "smoke", "method": method, "params": params}) + "\n")
            value = json.loads(r.stdout)
            if "error" in value:
                raise AssertionError(value["error"])
            return value["result"]

        try:
            cli("server", "start")
            cli("module", "link", str(root))
            info = call("module.info", id="personal.luvus-tasks")
            print("Module manifest registered:", json.dumps(info))
            before = {t["terminal_id"] for t in call("terminal.backend.inventory")["terminals"]}
            cli("module", "run", "personal.luvus-tasks", "hub-windows" if os.name == "nt" else "hub")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                added = [t for t in call("terminal.backend.inventory")["terminals"] if t["terminal_id"] not in before]
                if added:
                    break
                time.sleep(0.2)
            assert len(added) == 1, added
            pane = {"pane": added[0]["pane_id"]}
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                output = call("pane.read", pane=pane["pane"], lines=100)
                if "Luvus Tasks" in json.dumps(output):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("Task pane did not render: " + json.dumps(output))
            # Exercise navigation from another dashboard tab through the real action.
            code = "from luvus_tasks.core import Store; from luvus_tasks.handover import Luvus; from luvus_tasks.operations import open_console; s=Store(); open_console(s,Luvus(),ticket='dashboard:{\"config\":true}'); s.db.close()"
            subprocess.run([sys.executable, "-c", code], env=env, cwd=root, check=True, timeout=30)
            time.sleep(1)
            configured = call("pane.read", pane=pane["pane"], lines=100)
            assert "Connections" in json.dumps(configured), configured
            cli("module", "run", "personal.luvus-tasks", "hub-windows" if os.name == "nt" else "hub")
            time.sleep(1)
            reopened = call("pane.read", pane=pane["pane"], lines=100)
            assert "Search title, ID, status" in json.dumps(reopened), reopened
            print("Tasks returned from Configuration to the issue list.")
            docks = call("ui.dock.list")
            assert "luvus-tasks" in json.dumps(docks), docks
            logs = call("module.log.list", id="personal.luvus-tasks", limit=20)
            print("Task pane rendered; sidebar dock registered.")
            count = len(call("terminal.backend.inventory")["terminals"])
            for _ in range(2):
                cli("module", "run", "personal.luvus-tasks", "hub-windows" if os.name == "nt" else "hub")
                time.sleep(0.5)
            assert len(call("terminal.backend.inventory")["terminals"]) == count, "Open created a duplicate console"
            print("Repeated Open reused the exact console terminal.")
            print("Module logs:", json.dumps(logs))
            terminal = call("terminal.backend.create", cwd=str(root), placement={"kind": "workspace"},
                            focus=False, label="task-smoke-shell", command=[sys.executable, str(root / "launcher.py"), "shell"])
            inventory = call("terminal.backend.inventory")
            assert terminal["server_generation"] == inventory["server_generation"]
            created = next(t for t in inventory["terminals"] if t["terminal_id"] == terminal["terminal_id"])
            assert Path(created["cwd"]).resolve() == root
            assert created["pane_id"] == terminal["pane_id"]
            call("pane.focus", pane=terminal["pane_id"])
            cli("module", "run", "personal.luvus-tasks", "hub-windows" if os.name == "nt" else "hub")
            time.sleep(1)
            focused = call("pane.get", pane=pane["pane"])
            print("Cross-workspace target:", json.dumps(focused))
            assert focused.get("focused") is True, focused
            assert len(call("terminal.backend.inventory")["terminals"]) == count + 1
            print("Tasks action switched back from another workspace without creating a picker or duplicate.")
            print("Dedicated handover shell created with matching cwd and terminal identity; no agent launched.")
            task = call("task.add", title="Disposable module tracking test", paths=["test-scope/**"], deps=[])["task"]
            call("task.claim", id=task["id"], pane=terminal["pane_id"])
            call("lease.acquire", task=task["id"], pane=terminal["pane_id"], paths=task["paths"])
            code = "from luvus_tasks.core import Store; from luvus_tasks.handover import Luvus; from luvus_tasks.orch import show; s=Store(); c=s.config(); c['orchestration']=True; s.save_config(c); show(s,Luvus(),reveal=True); s.db.close()"
            probe = subprocess.run([sys.executable, '-c', code], env=env, cwd=root, capture_output=True, text=True, timeout=30)
            assert probe.returncode == 0, probe.stdout + probe.stderr
            assert 'tasks-orch' not in json.dumps(call('ui.dock.list'))
            print('No duplicate ORCH dock; native task management retained.')
            released = call("task.release", id=task["id"])
            assert released["task"]["status"] == "queued"
            assert released["released_leases"]
            print("ORCH task claim, path lease, and release verified in disposable state; no gate executed.")
        finally:
            cli("server", "stop")


if __name__ == "__main__":
    main()
