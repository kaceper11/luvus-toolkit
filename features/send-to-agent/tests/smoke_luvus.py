"""Opt-in real Luvus transport/UI test using a local fake Codex, never a model service."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from checkout_state import review
from send_agent import Host, Store, deliver, message, MODULE


def main():
    binary = shutil.which("luvus")
    if not binary:
        raise SystemExit("Install Luvus to run this opt-in smoke test.")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="lsa-smoke-", dir="/tmp" if os.name != "nt" else None) as tmp:
        path = Path(tmp)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LUVUS_")}
        env["LUVUS_HOME"] = str(path / "home")
        bins = path / "bin"
        bins.mkdir()
        fake = bins / "codex"
        fake.write_text("#!" + sys.executable + "\n" + '''import os, sys, json, subprocess
from pathlib import Path
subprocess.run([os.environ['LUVUS_BIN_PATH'], 'agent', 'report', os.environ['LUVUS_PANE_ID'], '--source', 'send-module-smoke', '--kind', 'codex', '--status', 'idle'], capture_output=True)
print('Synthetic Codex ready (no model)', flush=True)
for line in sys.stdin:
    with (Path(os.environ['LUVUS_HOME']) / ('received-' + os.environ['LUVUS_PANE_ID'] + '.txt')).open('a') as f:
        f.write(line)
    print('Received test input', flush=True)
''')
        fake.chmod(0o755)
        env["PATH"] = str(bins) + os.pathsep + env.get("PATH", "")
        # Keep fake-provider discovery and the server on the same explicit environment.
        os.environ["PATH"] = env["PATH"]
        os.environ["LUVUS_HOME"] = env["LUVUS_HOME"]
        os.environ["LUVUS_BIN_PATH"] = binary
        os.environ["LUVUS_SOCKET_PATH"] = str(Path(env["LUVUS_HOME"]) / "luvus.sock")
        host = Host({"LUVUS_BIN_PATH": binary, "LUVUS_HOME": env["LUVUS_HOME"]})
        def cli(*args):
            r = subprocess.run([binary, *args], env=env, cwd=root, capture_output=True, text=True, timeout=30)
            if r.returncode:
                raise AssertionError(r.stdout + r.stderr)
            return r.stdout
        def until(check, seconds=15):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if check():
                    return
                time.sleep(.2)
            raise AssertionError("Timed out waiting for smoke condition")
        try:
            cli("server", "start")
            host.discover()
            cli("module", "link", str(root))
            info = host.call("module.info", id=MODULE)
            assert MODULE in json.dumps(info)
            opened = host.call("module.pane.open", module=MODULE, entrypoint="composer", placement="tab")
            until(lambda: "Send to agent" in json.dumps(host.call("pane.read", pane=opened["pane"], lines=120)))
            output = json.dumps(host.call("pane.read", pane=opened["pane"], lines=120))
            assert "Traceback" not in output, output
            def tab_name(pane):
                info = host.call("pane.get", pane=str(pane))
                return next(t.get("name") for t in host.call("tab.list")["tabs"] if t["tab_id"] == info["tab_id"])
            until(lambda: tab_name(opened["pane"]) == "↗ Send to agent")
            print("Manifest linked and composer rendered in disposable Luvus.", flush=True)
            checkout = path / "checkout"
            checkout.mkdir()
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            (checkout / "dirty.txt").write_text("unchanged by delivery\n")
            before = (checkout / "dirty.txt").read_bytes()
            store = Store(path / "state")
            context = {"pane": {"cwd": str(checkout), "id": opened["pane"]}, "selection": "Selected answer\nzażółć 🌍 $HOME `literal`"}
            record = store.create(context, host.session)
            record["instruction"] = "SMOKE_NEW: quote only"
            record["reviewed_checkouts"] = review(record)
            deliver(store, host, record, message(record))
            assert record["state"] == "delivered"
            pane = record["target"]["pane"]
            host.call("pane.focus", pane=str(pane))
            try:
                until(lambda: tab_name(pane) == "Codex · SMOKE_NEW: quote only")
            except AssertionError:
                print("Title:", tab_name(pane), "pane:", host.call("pane.get", pane=str(pane)), flush=True)
                print("Records:", [(str(p), p.read_text()) for p in path.rglob("tab-titles/*.json")], flush=True)
                print(cli("module", "log", MODULE), flush=True)
                raise
            from tab_titles import remember
            info = host.call("pane.get", pane=str(pane))
            host.call("tab.rename", tab_id=info["tab_id"], name="My manual title")
            remember(host.call, pane, "Codex · replacement")
            assert tab_name(pane) == "My manual title"
            received = Path(env["LUVUS_HOME"]) / ("received-" + str(pane) + ".txt")
            until(lambda: received.exists() and "SMOKE_NEW" in received.read_text())
            second = store.create(context, host.session)
            second["target"] = host.agents()[0]
            second["instruction"] = "SMOKE_EXISTING: quote only"
            second["reviewed_checkouts"] = review(second)
            deliver(store, host, second, message(second))
            until(lambda: "SMOKE_EXISTING" in received.read_text())
            assert (checkout / "dirty.txt").read_bytes() == before
            assert message(record) in received.read_text(), "New-agent transport changed quoted text"
            assert message(second) in received.read_text(), "Existing-agent transport changed quoted text"
            assert received.read_text().count("SMOKE_NEW") == 1
            assert received.read_text().count("SMOKE_EXISTING") == 1
            store.db.close()
            print("New and existing synthetic-agent deliveries received once; checkout unchanged.", flush=True)
            # Open action captures selection before opening its composer.
            context_env = {**env, "LUVUS_MODULE_CONTEXT_JSON": json.dumps(context), "LUVUS_BIN_PATH": binary}
            r = subprocess.run([sys.executable, str(root / "launcher.py"), "open"], env=context_env, capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, r.stderr
            native_store = Store(Path(env["LUVUS_HOME"]) / "modules/config" / MODULE)
            assert any(r["items"] and r["items"][0]["text"] == context["selection"] for r in native_store.records())
            native_store.db.close()
            print("Open action preserved the captured multiline selection.", flush=True)
        finally:
            cli("server", "stop")


if __name__ == "__main__":
    main()
