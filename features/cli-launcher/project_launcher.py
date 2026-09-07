"""Project links and native arrangements; Python standard library only."""

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid

import launcher


EMPTY = {"version": 1, "repositories": {}, "worktrees": {}, "providers": {}}


def text(value, label="Value"):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label} must be nonempty text without control characters.")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


def git(cwd, *args):
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                            text=True, encoding="utf-8", timeout=10)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git could not identify the checkout.")
    return result.stdout.strip()


def identity(cwd):
    path = Path(cwd)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"Project directory is unavailable: {cwd}")
    path = path.resolve()
    # A .git marker distinguishes a broken Git checkout from an ordinary folder.
    has_git = any((parent / ".git").exists() for parent in (path, *path.parents))
    if has_git:
        root = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
        common = Path(git(path, "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = path / common
        return {"repository": canonical(common), "worktree": canonical(root)}
    return {"repository": canonical(path), "worktree": canonical(path)}


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError(f"Another operation is active. Retry later; stale lock: {path}") from error
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def atomic_write(path, data):
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(encoded.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Configuration/state exceeds the 1 MiB limit; existing data was preserved.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def read_json(path, default):
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError(f"File is too large: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return deepcopy(default)


def argv(value):
    if not isinstance(value, list) or not value or len(value) > 128:
        raise ValueError("Entrypoint must be a nonempty JSON argv array (up to 128 arguments).")
    for part in value:
        text(part, "Argument")
        if len(part.encode("utf-8")) > 16384:
            raise ValueError("An argument exceeds the native terminal limit.")
    return value


def map_tree(tree, leaf, depth=0):
    if depth > 32 or not isinstance(tree, dict):
        raise ValueError("Invalid or excessively deep layout.")
    if set(tree) == {"Leaf"}:
        return {"Leaf": leaf(tree["Leaf"])}
    if set(tree) != {"Split"} or not isinstance(tree["Split"], dict):
        raise ValueError("Layout needs Leaf or Split nodes.")
    split = tree["Split"]
    if set(split) != {"axis", "ratio", "a", "b"} or type(split["axis"]) is not int or split["axis"] not in (0, 1):
        raise ValueError("Invalid layout split.")
    ratio = split["ratio"]
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("Layout ratios must be between zero and one.")
    return {"Split": {**split, "a": map_tree(split["a"], leaf, depth + 1),
                      "b": map_tree(split["b"], leaf, depth + 1)}}


def validate_arrangement(item):
    if not isinstance(item, dict) or set(item) != {"name", "roles", "tree", "commands"}:
        raise ValueError("Arrangement needs name, roles, tree, and commands.")
    text(item["name"])
    roles = item["roles"]
    if not isinstance(roles, dict) or not 1 <= len(roles) <= 16:
        raise ValueError("An arrangement needs 1–16 tool/shell roles.")
    for name, role in roles.items():
        text(name)
        if not isinstance(role, dict):
            raise ValueError("Invalid pane role.")
        if set(role) == {"tool"}:
            launcher.validate([role["tool"]])
        elif set(role) == {"shell"}:
            argv(role["shell"])
        else:
            raise ValueError("Each role needs a saved tool or explicit shell argv.")
    leaves = []
    def collect(name):
        text(name)
        leaves.append(name)
        return name
    map_tree(item["tree"], collect)
    if len(leaves) != len(set(leaves)) or set(leaves) != set(roles):
        raise ValueError("Layout must include each role exactly once.")
    if not isinstance(item["commands"], list) or len(item["commands"]) > 32:
        raise ValueError("Commands must be a list of up to 32 owner references.")
    seen = set()
    for command in item["commands"]:
        if not isinstance(command, dict) or set(command) != {"id", "revision", "kind"}:
            raise ValueError("Command reference needs id, revision, and kind.")
        text(command["id"])
        text(command["revision"])
        if command["kind"] not in ("command", "service") or command["id"] in seen:
            raise ValueError("Invalid or duplicate command reference.")
        seen.add(command["id"])
    return item


def validate_link(item):
    if not isinstance(item, dict) or "name" not in item:
        raise ValueError("Link needs a name.")
    text(item["name"])
    if set(item) == {"name", "service"}:
        text(item["service"])
    elif set(item) == {"name", "destination"}:
        text(item["destination"])
    else:
        raise ValueError("Link needs destination or service ID.")
    return item


def validate_config(value):
    if not isinstance(value, dict) or set(value) != set(EMPTY) or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported project configuration; existing file was left untouched.")
    if not isinstance(value["providers"], dict):
        raise ValueError("Invalid integration entrypoints.")
    for owner, command in value["providers"].items():
        if owner not in ("tasks", "commands"):
            raise ValueError("Unknown integration owner.")
        argv(command)
    for scope in ("repositories", "worktrees"):
        if not isinstance(value[scope], dict):
            raise ValueError("Invalid project scopes.")
        for key, entries in value[scope].items():
            text(key)
            if not isinstance(entries, dict) or set(entries) - {"links", "arrangements"}:
                raise ValueError("Invalid project entries.")
            for kind, items in entries.items():
                if not isinstance(items, dict):
                    raise ValueError("Project entries must be keyed by ID.")
                for entry_id, item in items.items():
                    text(entry_id)
                    if item is not None:
                        (validate_link if kind == "links" else validate_arrangement)(item)
    return value


def load_config(path):
    return validate_config(read_json(path, EMPTY))


def save_config(path, value, previous):
    # Validate without writing invalid data or changing the original on conflict.
    validate_config(value)
    with locked(path.with_suffix(".lock")):
        if load_config(path) != previous:
            raise ValueError("Project configuration changed. Reopen the form.")
        atomic_write(path, value)


def effective(config, project, kind):
    values = {}
    for scope, key in (("repositories", "repository"), ("worktrees", "worktree")):
        values.update(config[scope].get(project[key], {}).get(kind, {}))
    return {key: value for key, value in values.items() if value is not None}


def selected(path, project, kind, entry_id, expected):
    if identity(project["worktree"]) != project:
        raise ValueError("Project identity changed. Reopen Project…")
    actual = effective(load_config(path), project, kind).get(entry_id)
    if actual is None or actual != expected:
        raise ValueError("Selection changed or was deleted. Reopen its list.")
    return actual


def provider(config, owner, operation, cwd, **params):
    command = config["providers"].get(owner)
    if not command:
        raise ValueError(f"{owner.title()} integration is unavailable. Configure an agreed JSON entrypoint first.")
    request = {"version": 1, "request_id": uuid.uuid4().hex, "operation": operation,
               "cwd": canonical(cwd), **params}
    result = subprocess.run(argv(command), input=json.dumps(request) + "\n", cwd=cwd,
                            text=True, encoding="utf-8", capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError(f"{owner.title()} entrypoint failed ({result.returncode}).")
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("Integration response is too large.")
    response = json.loads(result.stdout)
    if (not isinstance(response, dict) or response.get("version") != 1
            or response.get("request_id") != request["request_id"]
            or response.get("cwd") != request["cwd"]):
        raise ValueError("Integration response identity/version mismatch.")
    if "error" in response:
        raise ValueError(f"{owner.title()}: {response['error']}")
    return response["result"]


def installed_provider(owner, rpc=launcher.call):
    module = {"tasks": "personal.luvus-tasks", "commands": "personal.project-commands"}[owner]
    info = rpc("module.info", id=module)
    if not info.get("enabled") or not info.get("runnable"):
        raise ValueError(f"{module} must be enabled and runnable.")
    root = Path(info["root"])
    from toolkit_core import ROOT as toolkit_root
    python = toolkit_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = root / "launcher.py"
    if not python.is_file() or not script.is_file():
        raise ValueError("The installed module's interpreter or entrypoint is missing.")
    directory = rpc("module.config_dir", id=module)["dir"]
    if owner == "tasks":
        return [str(python), str(script), "bundle-api", directory]
    return [sys.executable, str(Path(__file__).with_name("commands_adapter.py").resolve()),
            str(python), str(script), "api", "--store", directory]


def check_provider(config, owner, cwd):
    operation = "bundles.list" if owner == "tasks" else "commands.list"
    results = provider(config, owner, operation, cwd)
    if not isinstance(results, list):
        raise ValueError("Connection returned an invalid list.")
    for item in results:
        if owner == "tasks":
            validate_bundle(item)
        elif not isinstance(item, dict) or item.get("kind") not in ("command", "service"):
            raise ValueError("Connection returned an invalid command.")
    return f"Connection checked · {len(results)} {('bundles' if owner == 'tasks' else 'commands/services')} for this checkout"


def destination(value, cwd):
    text(value, "Destination")
    # Drive letters are paths, not URL schemes on Windows.
    if not Path(value).is_absolute():
        parsed = urlsplit(value)
        if parsed.scheme:
            if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Only HTTP(S) URLs without embedded credentials are supported.")
            parsed.port  # Reject malformed ports as well.
            if any(c.isspace() for c in value) or "\\" in value:
                raise ValueError("URL contains whitespace or backslashes.")
            return value
    path = Path(value)
    if not path.is_absolute():
        path = Path(cwd) / path
    path = path.resolve()
    # Opening a document must not execute a script or application association.
    if not path.is_file() or path.suffix.lower() not in (".md", ".txt", ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".svg"):
        raise ValueError("Choose an existing Markdown, text, PDF, HTML, or image document.")
    return path.as_uri()


def open_link(config_path, project, entry_id, item, rpc=provider, opener=None):
    selected(config_path, project, "links", entry_id, item)
    cwd = project["worktree"]
    if "service" in item:
        result = rpc(load_config(config_path), "commands", "services.urls", cwd, id=item["service"])
        if result.get("state") != "running" or result.get("fresh") is not True:
            raise ValueError("Service URL is unavailable or stale. Open Project Commands.")
        urls = result.get("urls", [])
        if not isinstance(urls, list) or len(urls) != 1:
            raise ValueError("Service must expose one selected URL; choose it in Project Commands.")
        url = destination(urls[0], cwd)
        if not url.startswith(("http://", "https://")):
            raise ValueError("Service URLs must use HTTP(S).")
    else:
        url = destination(item["destination"], cwd)
    if opener:
        opener(url)
    elif os.name == "nt":
        os.startfile(url)
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", url], check=True, timeout=15)
    return url


def capture(pane_id, roles, name, rpc=launcher.call):
    pane = rpc("pane.get", pane=str(pane_id))
    exported = rpc("layout.export", workspace_id=pane["workspace_id"], tab_id=pane["tab_id"])
    order = []
    def assign(pane_id):
        key = f"pane{len(order) + 1}"
        order.append(str(pane_id))
        return key
    tree = map_tree(exported["tree"], assign)
    if roles is None:
        return order, tree
    return validate_arrangement({"name": name, "tree": tree, "roles": roles, "commands": []})


def workspace(cwd, rpc):
    matches = [item for item in rpc("workspace.list")["workspaces"] if canonical(item["cwd"]) == canonical(cwd)]
    if len(matches) > 1:
        raise ValueError("Multiple workspaces match this checkout. Resolve the ambiguity first.")
    return matches[0] if matches else None


def inventory(rpc):
    value = rpc("terminal.backend.inventory")
    if value.get("truncated"):
        raise ValueError("Terminal inventory is incomplete; cannot safely reconcile launches.")
    return value


def locator(created):
    return {key: created[key] for key in ("server_generation", "terminal_id", "pane_id")}


def role_command(role, cwd, presets_path):
    if "shell" in role:
        command = argv(role["shell"])
        if not shutil.which(command[0]):
            raise ValueError(f"Shell executable unavailable: {command[0]}")
        return command
    preset = launcher.selected_preset(presets_path, json.dumps(role["tool"]))
    return [sys.executable, str(Path(launcher.__file__).resolve()), "--run", "--cwd", cwd,
            "--config", str(presets_path), "--preset", json.dumps(preset)]


def focus_recorded(record, cwd, inv, rpc, focus):
    target = None
    for step in record["roles"].values():
        terminal = step["terminal"]
        live = [t for t in inv["terminals"] if t["terminal_id"] == terminal["terminal_id"] and t["pane_id"] == terminal["pane_id"]]
        if not live or canonical(live[0]["workspace"]["root"]) != cwd:
            raise ValueError("An arrangement pane is missing or moved. Inspect it, then choose Recover.")
        if rpc("terminal.backend.validate", **locator(terminal)).get("state") != "alive":
            raise ValueError("A recorded terminal is not alive. Inspect/close it before recovery.")
        pane = rpc("pane.get", pane=terminal["pane_id"])
        if target and (target["workspace_id"], target["tab_id"]) != (pane["workspace_id"], pane["tab_id"]):
            raise ValueError("Arrangement panes moved to different tabs. Restore their locations.")
        target = target or {**pane, "pane_id": terminal["pane_id"]}
    if not target:
        raise ValueError("Arrangement has no recorded panes.")
    if focus:
        rpc("pane.focus", pane=target["pane_id"])
    return {"state": "succeeded", "operation_id": record["id"], "pane_id": target["pane_id"]}


def run_arrangement(config_path, project, entry_id, item, rpc=launcher.call, owner=provider,
                    new_copy=False, recover=False, focus=True):
    selected(config_path, project, "arrangements", entry_id, item)
    validate_arrangement(item)
    cwd = project["worktree"]
    config = load_config(config_path)
    state_path = config_path.with_name("arrangement-runs.json")
    # ponytail: one module-wide operation lock; per-worktree locks if concurrency matters.
    with locked(state_path.with_suffix(".lock")):
        state = read_json(state_path, {})
        key = digest([cwd, entry_id])
        record = state.get(key)
        inv = inventory(rpc)
        if record and record["generation"] != inv["server_generation"]:
            raise ValueError("The server restarted. Inspect restored tabs, then explicitly create a new copy." if not new_copy else
                             "Use the Project list's forget action after inspecting restored tabs, then create a new copy.")
        if (record and not new_copy and not recover and record["layout"]
                and all(step["state"] == "succeeded" for step in record["commands"].values())):
            result = focus_recorded(record, cwd, inv, rpc, focus)
            if record["revision"] != digest(item):
                result["notice"] = "Focused the previous arrangement version. New copy uses the edited configuration."
            return result
        if record and not new_copy and record["revision"] != digest(item):
            raise ValueError("A previous arrangement version is recorded. Focus it or explicitly create a new copy.")
        commands = {key: role_command(role, cwd, config_path.with_name("presets.json")) for key, role in item["roles"].items()}
        for command in item["commands"]:
            current = owner(config, "commands", "commands.describe", cwd, id=command["id"])
            if any(current.get(key) != command[key] for key in ("id", "revision", "kind")):
                raise ValueError("Project command changed. Edit the arrangement before launching.")
        if not record or new_copy:
            record = {"revision": digest(item), "generation": inv["server_generation"],
                      "id": uuid.uuid4().hex, "roles": {},
                      "commands": {c["id"]: {"state": "pending"} for c in item["commands"]}, "layout": False}
            state[key] = record
            atomic_write(state_path, state)
        anchor = None
        # Missing first roles must join surviving panes, not create a second tab.
        for step in record["roles"].values():
            if step["state"] == "succeeded":
                saved = step["terminal"]
                live = [t for t in inv["terminals"] if t["terminal_id"] == saved["terminal_id"] and t["pane_id"] == saved["pane_id"]]
                if live:
                    if canonical(live[0]["workspace"]["root"]) != cwd:
                        raise ValueError("A recorded pane moved to another workspace. Restore it before recovery.")
                    anchor = saved
                    break
        for role_id, command in commands.items():
            selected(config_path, project, "arrangements", entry_id, item)
            label = f"launcher:{record['id']}:{role_id}"
            step = record["roles"].get(role_id)
            inv = inventory(rpc)
            if inv["server_generation"] != record["generation"]:
                raise ValueError("Server changed during launch. Inspect tabs before recovery.")
            matches = [t for t in inv["terminals"] if t.get("label") == label]
            if len(matches) > 1:
                raise ValueError("Ambiguous launch result; inspect matching panes.")
            if step and step["state"] == "uncertain":
                if not matches:
                    raise ValueError("Launch outcome is uncertain. Inspect tabs; no automatic retry is safe.")
                step = {"state": "succeeded", "terminal": {"server_generation": inv["server_generation"], **matches[0]}}
                record["roles"][role_id] = step
                atomic_write(state_path, state)
            if step and step["state"] == "succeeded":
                terminal = step["terminal"]
                live = [t for t in inv["terminals"] if t["terminal_id"] == terminal["terminal_id"] and t["pane_id"] == terminal["pane_id"]]
                if not live or canonical(live[0]["workspace"]["root"]) != cwd:
                    if not recover:
                        raise ValueError("An arrangement pane is missing or moved. Inspect it, then choose Recover.")
                    if live:
                        raise ValueError("A pane moved to another workspace. Restore its location before recovery.")
                    step = None
                else:
                    validation = rpc("terminal.backend.validate", **locator(terminal))
                    if validation.get("state") != "alive":
                        raise ValueError("A recorded terminal is not alive. Inspect/close it, then choose Recover.")
            if step and step["state"] == "failed":
                if not recover:
                    raise ValueError("A pane could not be created. Choose Recover to retry the failed step.")
                step = None
            if step is None:
                workspace(cwd, rpc)
                record["roles"][role_id] = {"state": "uncertain"}
                atomic_write(state_path, state)
                placement = {"kind": "sibling", "of_terminal": locator(anchor)} if anchor else {"kind": "workspace"}
                try:
                    created = rpc("terminal.backend.create", cwd=cwd, placement=placement,
                                  focus=False, label=label, command=command)
                except launcher.RpcError as error:
                    if error.dispatch in ("not_started", "rejected"):
                        record["roles"][role_id] = {"state": "failed"}
                        atomic_write(state_path, state)
                    raise
                if not anchor:
                    from tab_titles import remember
                    remember(rpc, created.get("pane_id"), "▷ " + item["name"] + " · " + Path(cwd).name)
                terminal = locator(created)
                record["roles"][role_id] = {"state": "succeeded", "terminal": terminal}
                record["layout"] = False
                atomic_write(state_path, state)
            else:
                terminal = step["terminal"]
            if anchor is None:
                anchor = terminal
        target = rpc("pane.get", pane=anchor["pane_id"])
        for step in record["roles"].values():
            pane = rpc("pane.get", pane=step["terminal"]["pane_id"])
            if (pane["workspace_id"], pane["tab_id"]) != (target["workspace_id"], target["tab_id"]):
                raise ValueError("Arrangement panes have moved to different tabs. Restore their locations before recovery.")
        if not record["layout"]:
            tree = map_tree(item["tree"], lambda role: int(record["roles"][role]["terminal"]["pane_id"]))
            rpc("layout.apply", workspace_id=target["workspace_id"], tab_id=target["tab_id"],
                focus=anchor["pane_id"], tree=tree)
            record["layout"] = True
            atomic_write(state_path, state)
        for command in item["commands"]:
            step = record["commands"].get(command["id"])
            if step:
                if step["state"] == "succeeded":
                    continue
                if step["state"] not in ("pending", "failed"):
                    raise ValueError("Command outcome is uncertain. Inspect Project Commands; it will not be replayed.")
                if step["state"] == "failed" and not recover:
                    raise ValueError("A command failed. Choose Recover to retry only the failed step.")
            selected(config_path, project, "arrangements", entry_id, item)
            if load_config(config_path)["providers"] != config["providers"]:
                raise ValueError("Integration configuration changed. Reopen the arrangement.")
            attempt = (step or {}).get("attempt", 0) + 1
            record["commands"][command["id"]] = {"state": "uncertain", "attempt": attempt}
            atomic_write(state_path, state)
            result = owner(config, "commands", "services.ensure" if command["kind"] == "service" else "commands.run",
                           cwd, id=command["id"], revision=command["revision"],
                           operation_id=f"{record['id']}:{command['id']}:{attempt}")
            if result.get("state") == "failed" and result.get("dispatch") in ("not_started", "executed"):
                record["commands"][command["id"]] = {"state": "failed", "attempt": attempt, "run_id": result.get("run_id")}
                atomic_write(state_path, state)
                raise ValueError("Project command failed. Its successful sibling steps were retained.")
            if result.get("state") not in ("succeeded", "running", "reused") or not result.get("run_id"):
                raise ValueError("Command did not confirm a successful start. Inspect Project Commands.")
            record["commands"][command["id"]] = {"state": "succeeded", "run_id": result["run_id"]}
            atomic_write(state_path, state)
        if focus:
            rpc("pane.focus", pane=anchor["pane_id"])
        return {"state": "succeeded", "operation_id": record["id"], "pane_id": anchor["pane_id"]}


def branch_identity(cwd):
    project = identity(cwd)
    if project["repository"] == project["worktree"]:
        return {"kind": "none", "value": ""}
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return {"kind": "detached", "value": git(cwd, "rev-parse", "HEAD")} if branch == "HEAD" else {"kind": "branch", "value": branch}


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {"id", "revision", "name", "members"}:
        raise ValueError("Bundle needs id, revision, name, and members.")
    for key in ("id", "revision", "name"):
        text(bundle[key])
    if not isinstance(bundle["members"], list) or not 1 <= len(bundle["members"]) <= 32:
        raise ValueError("Bundle needs 1–32 members.")
    seen = set()
    for member in bundle["members"]:
        if not isinstance(member, dict) or not {"cwd", "branch"} <= set(member) or set(member) - {"cwd", "branch", "arrangement"}:
            raise ValueError("Bundle member needs cwd, branch, and optional arrangement ID.")
        text(member["cwd"])
        if not Path(member["cwd"]).is_absolute() or canonical(member["cwd"]) in seen:
            raise ValueError("Bundle paths must be absolute and unique.")
        seen.add(canonical(member["cwd"]))
        branch = member["branch"]
        if not isinstance(branch, dict) or set(branch) != {"kind", "value"} or branch["kind"] not in ("branch", "detached", "none"):
            raise ValueError("Invalid expected branch identity.")
        if branch["kind"] != "none":
            text(branch["value"])
        elif branch["value"] != "":
            raise ValueError("Non-Git identity must have an empty value.")
        if "arrangement" in member:
            text(member["arrangement"])
    return bundle


def open_bundle(config_path, project, bundle, layouts=False, rpc=launcher.call, owner=provider, arrangement_revisions=None):
    validate_bundle(bundle)
    if identity(project["worktree"]) != project:
        raise ValueError("Project identity changed. Reopen the bundle list.")
    config = load_config(config_path)
    current = owner(config, "tasks", "bundles.get", project["worktree"], id=bundle["id"])
    if current != bundle:
        raise ValueError("Bundle changed. Review the new selection before opening.")
    results = []
    state_path = config_path.with_name("bundle-runs.json")
    with locked(state_path.with_suffix(".lock")):
        state = read_json(state_path, {})
        for member in bundle["members"]:
            cwd = canonical(member["cwd"])
            try:
                if identity(cwd)["worktree"] != cwd:
                    raise ValueError("Bundle member must name an exact checkout root.")
                if branch_identity(cwd) != member["branch"]:
                    raise ValueError("Branch identity differs from the bundle. No branch was switched.")
                target = workspace(cwd, rpc)
                if target is None:
                    # A lost reply is recoverable by the exact directory on the next invocation.
                    rpc("workspace.open", path=cwd, focus=False)
                    target = workspace(cwd, rpc)
                    if target is None:
                        raise ValueError("Workspace open was not confirmed.")
                if layouts and member.get("arrangement"):
                    member_project = identity(cwd)
                    arrangement = effective(config, member_project, "arrangements").get(member["arrangement"])
                    if arrangement is None:
                        raise ValueError("Saved arrangement is missing for this checkout.")
                    if (arrangement_revisions or {}).get(cwd) != digest(arrangement):
                        raise ValueError("This bundle layout changed. Review its commands before opening.")
                    run_arrangement(config_path, member_project, member["arrangement"], arrangement,
                                    rpc=rpc, owner=owner, focus=False)
                results.append({"cwd": cwd, "state": "succeeded", "workspace_id": target["workspace_id"]})
            except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
                results.append({"cwd": cwd, "state": "failed", "error": str(error)})
            state[digest([project["worktree"], bundle["id"]])] = {"revision": bundle["revision"], "members": results}
            atomic_write(state_path, state)
        successful = [result for result in results if result["state"] == "succeeded"]
        if successful:
            rpc("workspace.focus", workspace_id=successful[0]["workspace_id"])
    return results


def confirmed(prompt="Continue? [y/N] "):
    return input(prompt).strip().lower() == "y"


def choose(items, prompt="Number: "):
    pairs = list(items.items())
    for number, (entry_id, item) in enumerate(pairs, 1):
        print(f"  {number}. {item['name']} [{entry_id}]")
    return pairs[launcher.choose_index(pairs, prompt)]


def edit_entry(path, project, kind, entry_id, item, previous=None):
    previous = load_config(path) if previous is None else previous
    print("Scope: r repository defaults · w this worktree")
    scope = input("Scope [r]: ").strip().lower() or "r"
    if scope not in ("r", "w"):
        raise ValueError("Choose r or w.")
    level, key = ("repositories", "repository") if scope == "r" else ("worktrees", "worktree")
    updated = deepcopy(previous)
    entries = updated[level].setdefault(project[key], {}).setdefault(kind, {})
    entries[entry_id] = item
    print(f"{level}: {project[key]}\n{json.dumps(item, ensure_ascii=False, indent=2)}")
    if confirmed("Save? [y/N] "):
        save_config(path, updated, previous)


def links_view(path, project):
    previous = load_config(path)
    items = effective(previous, project, "links")
    print("\nLinks: o open · a add · e edit · d hide/delete · q back")
    action = input("> ").strip().lower()
    if action == "q":
        return
    entry_id, item = (uuid.uuid4().hex, {}) if action == "a" else choose(items)
    if action == "o":
        print(open_link(path, project, entry_id, item))
    elif action in ("a", "e"):
        name = input(f"Name [{item.get('name', '')}]: ").strip() or item.get("name", "")
        print("Use service:<ID> for a current Project Commands URL.")
        old = "service:" + item["service"] if "service" in item else item.get("destination", "")
        target = input(f"URL or document [{old}]: ").strip() or old
        value = {"name": text(name)}
        if target.startswith("service:"):
            value["service"] = text(target[8:])
        else:
            destination(target, project["worktree"])
            value["destination"] = target
        edit_entry(path, project, "links", entry_id, value, previous)
    elif action == "d":
        edit_entry(path, project, "links", entry_id, None, previous)
    else:
        raise ValueError("Choose o, a, e, d, or q.")


def capture_form(presets_path, project, source, rpc=launcher.call):
    hint = source.get("pane", {}).get("id", "")
    pane_id = input(f"Capture geometry from pane ID [{hint}]: ").strip() or hint
    pane = rpc("pane.get", pane=pane_id)
    ws = rpc("workspace.get", workspace_id=pane["workspace_id"])
    if identity(ws["cwd"]) != project:
        raise ValueError("Capture source must belong to this project worktree.")
    order, tree = capture(pane_id, None, "", rpc)
    roles = {}
    presets = launcher.load_presets(presets_path)
    for number, preset in enumerate(presets, 1):
        print(f"  {number}. {preset['name']}: {preset['command']}")
    print("Assign each captured pane a tool number or s for a shell. No process/session state is captured.")
    for number, pane in enumerate(order, 1):
        choice = input(f"Role pane{number} (source pane {pane}): ").strip().lower()
        if choice == "s":
            default = [shutil.which("pwsh") or shutil.which("powershell") or "powershell"] if os.name == "nt" else [os.environ.get("SHELL") or "/bin/sh"]
            raw = input(f"Shell argv JSON [{json.dumps(default)}]: ").strip()
            role = {"shell": argv(json.loads(raw)) if raw else default}
        else:
            index = int(choice) - 1
            if not 0 <= index < len(presets):
                raise ValueError("Choose one of the listed tool numbers.")
            role = {"tool": presets[index]}
        roles[f"pane{number}"] = role
    name = text(input("Arrangement name: ").strip())
    print("Optional Project Commands references as JSON: [{\"id\":\"test\",\"revision\":\"...\",\"kind\":\"command\"}]")
    commands = json.loads(input("References [empty]: ").strip() or "[]")
    return validate_arrangement({"name": name, "roles": roles, "tree": tree, "commands": commands})


def forget_run(path, project, entry_id):
    print("This forgets launch tracking only. Existing panes and services remain running; another launch may duplicate tools.")
    if not confirmed("I inspected existing panes; forget tracking? [y/N] "):
        return
    state_path = path.with_name("arrangement-runs.json")
    with locked(state_path.with_suffix(".lock")):
        state = read_json(state_path, {})
        state.pop(digest([project["worktree"], entry_id]), None)
        atomic_write(state_path, state)


def show_run(path, project, entry_id):
    state = read_json(path.with_name("arrangement-runs.json"), {})
    record = state.get(digest([project["worktree"], entry_id]))
    if not record:
        print("No launch recorded for this worktree.")
        return
    print(f"Operation {record['id']} — layout {'applied' if record['layout'] else 'pending'}")
    for kind in ("roles", "commands"):
        for name, step in record[kind].items():
            target = step.get("terminal", {}).get("pane_id") or step.get("run_id") or "—"
            print(f"  {kind}/{name}: {step['state']} ({target})")


def arrangements_view(path, project, source):
    previous = load_config(path)
    items = effective(previous, project, "arrangements")
    print("\nArrangements: o open/focus · r recover · n new copy · a capture · e replace · d hide/delete · s status · f forget tracking · q back")
    action = input("> ").strip().lower()
    if action == "q":
        return
    entry_id, item = (uuid.uuid4().hex, {}) if action == "a" else choose(items)
    if action in ("a", "e"):
        item = capture_form(path.with_name("presets.json"), project, source)
        edit_entry(path, project, "arrangements", entry_id, item, previous)
    elif action == "d":
        edit_entry(path, project, "arrangements", entry_id, None, previous)
    elif action == "f":
        forget_run(path, project, entry_id)
    elif action == "s":
        show_run(path, project, entry_id)
    elif action in ("o", "r", "n"):
        print(f"Worktree: {project['worktree']}\n{json.dumps(item, ensure_ascii=False, indent=2)}")
        if confirmed("Open this arrangement? [y/N] "):
            try:
                print(json.dumps(run_arrangement(path, project, entry_id, item,
                                                new_copy=action == "n", recover=action == "r")))
            finally:
                show_run(path, project, entry_id)
    else:
        raise ValueError("Unknown arrangement action.")


def bundles_view(path, project):
    config = load_config(path)
    bundles = provider(config, "tasks", "bundles.list", project["worktree"])
    if not isinstance(bundles, list):
        raise ValueError("Tasks returned an invalid bundle list.")
    for bundle in bundles:
        validate_bundle(bundle)
    _, bundle = choose({bundle["id"]: bundle for bundle in bundles})
    print(json.dumps(bundle, ensure_ascii=False, indent=2))
    layouts = confirmed("Also apply the listed saved arrangements (may launch tools/services/tests)? [y/N] ")
    revisions = {}
    if layouts:
        for member in bundle["members"]:
            if member.get("arrangement"):
                member_project = identity(member["cwd"])
                item = effective(config, member_project, "arrangements").get(member["arrangement"])
                if item is None:
                    raise ValueError("A requested arrangement is missing.")
                revisions[canonical(member["cwd"])] = digest(item)
                print(f"{member['cwd']}: {json.dumps(item, ensure_ascii=False, indent=2)}")
    if confirmed("Open these workspaces? [y/N] "):
        print(json.dumps(open_bundle(path, project, bundle, layouts, arrangement_revisions=revisions), ensure_ascii=False, indent=2))


def integrations_view(path):
    previous = load_config(path)
    print("Only configure entrypoints whose owner implements CONTRACTS.md. Commands execute with your permissions.")
    owner = input("Owner (tasks/commands): ").strip().lower()
    if owner not in ("tasks", "commands"):
        raise ValueError("Choose tasks or commands.")
    print(f"Current: {json.dumps(previous['providers'].get(owner))}")
    raw = input("Entrypoint JSON argv (empty removes): ").strip()
    updated = deepcopy(previous)
    if raw:
        updated["providers"][owner] = argv(json.loads(raw))
        if not Path(updated["providers"][owner][0]).is_absolute():
            raise ValueError("Use an absolute executable path for an integration.")
    else:
        updated["providers"].pop(owner, None)
    if confirmed("Save integration? [y/N] "):
        save_config(path, updated, previous)


def reset_override(path, project):
    previous = load_config(path)
    print(json.dumps(previous["worktrees"].get(project["worktree"], {}), ensure_ascii=False, indent=2))
    if confirmed("Remove this worktree's overrides and use repository defaults? [y/N] "):
        updated = deepcopy(previous)
        updated["worktrees"].pop(project["worktree"], None)
        save_config(path, updated, previous)


def project_view(presets_path, cwd, source):
    project = identity(cwd)
    path = presets_path.with_name("projects.json")
    while True:
        try:
            if identity(cwd) != project:
                raise ValueError("Project identity changed. Close and reopen Project…")
            print(f"\nProject — {project['worktree']}\nRepository defaults — {project['repository']}")
            print("l links · a arrangements · b Tasks bundles · i integrations · w reset worktree overrides · q close")
            choice = input("> ").strip().lower()
            if choice == "q":
                return
            if choice == "l":
                links_view(path, project)
            elif choice == "a":
                arrangements_view(path, project, source)
            elif choice == "b":
                bundles_view(path, project)
            elif choice == "i":
                integrations_view(path)
            elif choice == "w":
                reset_override(path, project)
        except KeyboardInterrupt:
            print("\nCancelled; already completed launch steps are retained.")
        except EOFError:
            return
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
            print(f"\nError: {error}")


def main():
    parser = argparse.ArgumentParser(description="Structured launcher entrypoint; see CONTRACTS.md.")
    parser.add_argument("--config", type=Path, required=True, help="Absolute projects.json path")
    args = parser.parse_args()
    request = {}
    try:
        if not args.config.is_absolute():
            raise ValueError("--config must be an absolute projects.json path.")
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Request exceeds the 1 MiB limit.")
        request = json.loads(raw)
        if not isinstance(request, dict):
            request = {}
            raise ValueError("Request must be an object.")
        if request.get("version") != 1 or request.get("confirm") is not True:
            raise ValueError("Version 1 and explicit confirm:true are required.")
        project = identity(request["cwd"])
        if request["operation"] == "bundles.open":
            result = open_bundle(args.config.resolve(), project, request["bundle"], request.get("layouts") is True,
                                 arrangement_revisions=request.get("arrangement_revisions"))
        elif request["operation"] == "arrangements.open":
            item = effective(load_config(args.config), project, "arrangements").get(request["id"])
            if item is None or digest(item) != request.get("revision"):
                raise ValueError("Arrangement changed or was deleted.")
            result = run_arrangement(args.config.resolve(), project, request["id"], item)
        else:
            raise ValueError("Unknown operation.")
        print(json.dumps({"version": 1, "request_id": request.get("request_id"), "cwd": request["cwd"], "result": result}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"version": 1, "request_id": request.get("request_id"), "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
