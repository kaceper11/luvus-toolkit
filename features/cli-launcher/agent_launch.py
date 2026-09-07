"""Prepare a Git destination once, then launch a saved tool in a native tab."""
import json
from pathlib import Path
import uuid

import launcher
import project_launcher as project


MODES = [("New worktree / new branch", "new"), ("Existing local branch / worktree", "existing"),
         ("Switch this checkout", "switch"), ("Current directory", "current")]


def snapshot(cwd):
    ident = project.identity(cwd)
    root = ident["worktree"]
    head = project.git(root, "rev-parse", "--verify", "HEAD")
    branch = project.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    refs = project.git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").splitlines()
    worktrees, entry = [], {}
    for part in project.git(root, "worktree", "list", "--porcelain", "-z").split("\0"):
        if not part:
            if entry:
                worktrees.append(entry)
                entry = {}
        else:
            key, _, value = part.partition(" ")
            entry[key] = value
    if entry:
        worktrees.append(entry)
    return {**ident, "head": head, "branch": branch, "refs": refs, "worktrees": worktrees}


def default_path(config, cwd, branch):
    repo = project.identity(cwd)["repository"]
    # Keep Git branch spelling out of filesystem paths (slashes, reserved names).
    return str(Path(config).parent / "worktrees" / (Path(cwd).name + "-" + project.digest([repo, branch])[:8] + "-" + uuid.uuid4().hex[:8]))


def prepare(cwd, mode, base="HEAD", branch="", destination=""):
    if mode not in dict((value, title) for title, value in MODES):
        raise ValueError("Choose a launch destination.")
    cwd = project.canonical(cwd)
    if not Path(cwd).is_dir():
        raise ValueError("The launch directory is unavailable.")
    if mode == "current":
        return {"mode": mode, "cwd": cwd, "destination": cwd, "project": project.identity(cwd)}
    state = snapshot(cwd)
    root = state["worktree"]
    branch = project.text(branch, "Branch")
    if branch.startswith("-"):
        raise ValueError("Branch names cannot begin with a dash.")
    project.git(root, "check-ref-format", "refs/heads/" + branch)
    ref = "refs/heads/" + branch
    occupied = next((w for w in state["worktrees"] if w.get("branch") == ref), None)
    if mode == "new":
        if ref in state["refs"]:
            raise ValueError("This branch already exists. Choose Existing local branch.")
        revision = project.git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
    else:
        if ref not in state["refs"]:
            raise ValueError("Choose an existing local branch.")
        revision = project.git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    if mode == "switch":
        if project.git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError("This checkout has uncommitted changes. Commit/stash them yourself or use a worktree.")
        if occupied and project.canonical(occupied["worktree"]) != root:
            raise ValueError("This branch is checked out elsewhere. Choose Existing local branch to open it.")
        target = root
    elif mode == "existing" and occupied:
        target = project.canonical(occupied["worktree"])
        if not Path(target).is_dir() or project.identity(target)["repository"] != state["repository"]:
            raise ValueError("The branch's existing worktree is unavailable. Inspect git worktree list.")
    else:
        path = Path(destination).expanduser()
        if not destination or not path.is_absolute():
            raise ValueError("Choose an absolute destination path.")
        target = project.canonical(path)
        if Path(target).exists() or Path(target).is_symlink():
            raise ValueError("Destination already exists. Choose a fresh path.")
        if Path(target) == Path(root) or Path(root) in Path(target).parents:
            raise ValueError("Create the worktree outside the source checkout.")
    return {"mode": mode, "cwd": cwd, "destination": target, "base": base, "branch": branch,
            "revision": revision, "source_head": state["head"], "source_branch": state["branch"],
            "project": {"repository": state["repository"], "worktree": root},
            "reuse": mode == "existing" and occupied is not None}


def summary(plan, preset):
    return (f"Repository: {plan['project']['worktree']}\n"
            f"Branch: {plan.get('branch', 'unchanged')}\n"
            f"Destination: {plan['destination']}\n"
            f"Command: {preset['command']}" +
            ("\nThis switches the selected checkout's branch." if plan["mode"] == "switch" else ""))


def execute(config, plan, preset, operation, rpc=launcher.call):
    """Durable last operation makes a rejected native launch retryable without repeating Git."""
    config = Path(config)
    path = config.with_name("agent-launch.json")
    with project.locked(path.with_suffix(".lock")):
        launcher.selected_preset(config, json.dumps(preset))
        command = project.role_command({"tool": preset}, plan["destination"], config)
        record = project.read_json(path, {})
        if record.get("operation") != operation:
            fresh = prepare(plan["cwd"], plan["mode"], plan.get("base", "HEAD"), plan.get("branch", ""), plan["destination"])
            if fresh != plan:
                raise ValueError("The checkout or branch changed. Review a fresh launch selection.")
            record = {"operation": operation, "plan": plan, "preset": preset, "state": "preparing"}
            project.atomic_write(path, record)
            root, target = plan["project"]["worktree"], plan["destination"]
            try:
                if plan["mode"] in ("new", "existing") and not plan["reuse"]:
                    Path(target).parent.mkdir(parents=True, exist_ok=True)
                    args = ["worktree", "add"]
                    if plan["mode"] == "new":
                        args += ["-b", plan["branch"]]
                    project.git(root, *args, "--", target, plan["revision"] if plan["mode"] == "new" else plan["branch"])
                elif plan["mode"] == "switch":
                    project.git(root, "switch", "--", plan["branch"])
            except Exception:
                # Git may have completed before a timeout. Never replay an uncertain mutation.
                raise ValueError("Git preparation did not finish cleanly. Inspect the destination before starting another launch.")
            record["state"] = "prepared"
            project.atomic_write(path, record)
        elif record["plan"] != plan or record["preset"] != preset:
            raise ValueError("The saved launch changed. Review a new launch.")
        if record["state"] == "preparing":
            raise ValueError("Git preparation outcome is uncertain. Inspect the destination; it will not be recreated.")
        if record["state"] in ("launching", "launched"):
            raise ValueError("This launch was already dispatched. Inspect its tab; it will not be repeated.")
        target = plan["destination"]
        if project.identity(target)["repository"] != plan["project"]["repository"]:
            raise ValueError("The destination repository changed.")
        if plan["mode"] != "current":
            state = snapshot(target)
            if state["head"] != plan["revision"] or state["branch"] != plan["branch"]:
                raise ValueError("The destination branch changed. Review a new launch.")
        # Opening a workspace is idempotent; failures here leave the prepared checkout retryable.
        project.workspace(plan["project"]["worktree"] if plan["mode"] == "current" else target, rpc)
        record["state"] = "launching"
        project.atomic_write(path, record)
        try:
            terminal = rpc("terminal.backend.create", cwd=plan["project"]["worktree"] if plan["mode"] == "current" else target,
                           placement={"kind": "workspace"}, command=command, focus=True,
                           label=preset["name"] + " · " + plan.get("branch", Path(target).name))
        except launcher.RpcError as error:
            if error.dispatch in ("not_started", "rejected"):
                record["state"] = "prepared"
                project.atomic_write(path, record)
            raise
        from tab_titles import remember
        remember(rpc, terminal["pane_id"], preset["name"] + " · " + plan.get("branch", Path(target).name))
        record.update(state="launched", terminal=terminal)
        project.atomic_write(path, record)
        return terminal
