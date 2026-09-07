"""Portable manifest entrypoint; prefer this checkout's installed virtualenv."""
import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    if len(sys.argv) > 1 and sys.argv[1] == "shell":
        env = {k: v for k, v in os.environ.items() if not k.startswith("LUVUS_TASKS_TOKEN_") and k not in ("GH_TOKEN", "GITHUB_TOKEN")}
        if os.name == "nt":
            command = [os.environ.get("COMSPEC", "cmd.exe"), "/d"]
        else:
            # A clean shell prevents startup files re-exporting tracker credentials.
            command = ["/bin/bash", "--noprofile", "--norc", "-i"]
        os.execvpe(command[0], command, env)
    python = root.parents[1] / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists() and Path(sys.prefix).resolve() != (root.parents[1] / ".venv").resolve():
        return subprocess.call([str(python), str(__file__), *sys.argv[1:]])
    if sys.version_info < (3, 11):
        print("Luvus Tasks requires Python 3.11+. See README.md to create .venv.", file=sys.stderr)
        return 1
    if len(sys.argv) == 4 and sys.argv[1] == "review-worker":
        from luvus_tasks.review import worker
        return worker(sys.argv[2], sys.argv[3])
    if len(sys.argv) == 3 and sys.argv[1] == "bundle-api":
        from luvus_tasks.workflow import bundle_api
        return bundle_api(sys.argv[2])
    if len(sys.argv) > 1 and sys.argv[1] == "workflow":
        from luvus_tasks.bounded import cli
        return cli(sys.argv[2:])
    if len(sys.argv) == 4 and sys.argv[1] == "hub-ui":
        from luvus_tasks.module_hub import run
        return run(sys.argv[2], sys.argv[3])
    from luvus_tasks.ui import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
