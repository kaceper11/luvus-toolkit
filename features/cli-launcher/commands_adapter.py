"""Translate the launcher contract to Project Commands' public argv/JSON API."""
import json
import os
import subprocess
import sys
import time

import launcher
import project_launcher as project


def request_owner(entrypoint, cwd, action, **params):
    response = subprocess.run(project.argv(entrypoint), input=json.dumps({"version": 1, "root": cwd, "action": action, **params}),
                              cwd=cwd, text=True, encoding="utf-8", capture_output=True, timeout=30)
    if len(response.stdout.encode()) > 1024 * 1024:
        raise ValueError("Project Commands response exceeds 1 MiB.")
    result = json.loads(response.stdout)
    if result.get("version") != 1:
        raise ValueError("Unsupported Project Commands API.")
    if response.returncode or "error" in result:
        raise ValueError(str(result.get("error", "Project Commands failed.")))
    return result["result"]


def adapt(request, command, owner=request_owner, rpc=launcher.call):
    cwd = project.canonical(request["cwd"])
    operation = request["operation"]
    config = owner(command, cwd, "config")
    if config.get("version") != 1 or not isinstance(config.get("projects"), dict):
        raise ValueError("Invalid Project Commands configuration response.")
    definitions = config["projects"].get(cwd, {}).get("commands", [])
    if operation == "commands.list":
        return [{"id": d["id"], "name": d["name"], "kind": d.get("kind", "command")} for d in definitions]
    matches = [dict(d) for d in definitions if d["id"] == request["id"]]
    if len(matches) != 1:
        raise ValueError("Command is missing or ambiguous for this checkout. Configure it in Project Commands.")
    definition = matches[0]
    definition.setdefault("kind", "command")
    definition.setdefault("category", "custom")
    revision = project.digest(definition)
    if operation == "commands.describe":
        return {"id": definition["id"], "revision": revision, "kind": definition["kind"]}
    if operation == "services.urls":
        if definition["kind"] != "service":
            raise ValueError("Select a managed service.")
        runs = owner(command, cwd, "status")
        live = [r for r in runs if r.get("root") == cwd and r.get("definition") == definition
                and r.get("state") == "running" and r.get("readiness") == "ready"
                and 0 <= time.time() - r.get("updated", 0) <= 15]
        if len(live) != 1:
            raise ValueError("Service is not ready, ambiguous, or its status is stale. Inspect Project Commands.")
        return {"state": "running", "fresh": True, "urls": definition.get("urls", [])}
    if operation not in ("commands.run", "services.ensure"):
        raise ValueError("Unsupported launcher operation.")
    if revision != request["revision"] or (operation == "services.ensure") != (definition["kind"] == "service"):
        raise ValueError("Command changed. Edit the layout before launching.")
    session = {"socket": os.environ.get("LUVUS_SOCKET_PATH"), "generation": project.inventory(rpc)["server_generation"]}
    if not session["socket"]:
        raise ValueError("Selected Luvus socket is unavailable.")
    run = owner(command, cwd, "run", command=definition["id"], reviewed=definition,
                request_id=request["operation_id"], session=session)
    if run.get("root") != cwd or run.get("definition") != definition or run.get("session") != session or not run.get("id"):
        raise ValueError("Project Commands returned a mismatched run; inspect before retrying.")
    state = run.get("state")
    if state == "failed":
        return {"state": "failed", "dispatch": "executed" if run.get("child") else "not_started", "run_id": run["id"]}
    if state in ("running", "succeeded", "passed") or state == "starting" and run.get("terminal_creation"):
        return {"state": "succeeded" if state in ("succeeded", "passed") else "running", "run_id": run["id"]}
    raise ValueError("Command start is unconfirmed. Inspect Project Commands; it will not be replayed.")


def main():
    request = {}
    try:
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw.encode()) > 1024 * 1024:
            raise ValueError("Request exceeds 1 MiB.")
        request = json.loads(raw)
        if not isinstance(request, dict):
            request = {}
            raise ValueError("Expected a request object.")
        if request.get("version") != 1 or not request.get("request_id"):
            raise ValueError("Version 1 and request ID required.")
        result = adapt(request, sys.argv[1:])
        print(json.dumps({"version": 1, "request_id": request["request_id"], "cwd": request["cwd"], "result": result}))
        return 0
    except Exception as error:
        print(json.dumps({"version": 1, "request_id": request.get("request_id"), "cwd": request.get("cwd"), "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
