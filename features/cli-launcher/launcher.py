"""Right-click CLI launching and a project dashboard for stock Luvus."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


MODULE_ID = "personal.luvus-cli-launcher"
VERSION = "0.6.0"
DEFAULTS = [{"name": "Codex", "command": "codex --dangerously-bypass-approvals-and-sandbox"},
            {"name": "Muse", "command": "muse --yolo"}]


def quick_title(preset):
    return {DEFAULTS[0]["command"]: "Open Codex — skip permissions",
            DEFAULTS[1]["command"]: "Open Muse — YOLO"}.get(preset["command"])


class RpcError(ValueError):
    def __init__(self, error):
        super().__init__(error.get("message", str(error)))
        self.dispatch = error.get("dispatch")


def call(method, **params):
    binary = os.environ.get("LUVUS_BIN_PATH")
    if not binary:
        raise ValueError("Open this launcher through Luvus; LUVUS_BIN_PATH is missing.")
    from toolkit_core.transport import request as toolkit_request, response as toolkit_response
    params, toolkit_owner = toolkit_request('cli-launcher', method, params)
    request = {"id": "launcher", "method": method, "params": params}
    try:
        result = subprocess.run(
            [binary, "uhp", "proxy"], input=json.dumps(request) + "\n",
            text=True, encoding="utf-8", capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Luvus timed out. Inspect its tabs before retrying; the launch may have succeeded.") from error
    try:
        response = json.loads(result.stdout)
    except ValueError:
        result.check_returncode()
        raise
    if "error" in response:
        raise RpcError(response["error"])
    result.check_returncode()
    return toolkit_response(toolkit_owner, method, response["result"])


def launch_target(context, rpc=call):
    """Workspace menus must ignore the unrelated focused pane in their context."""
    if context.get("invocation_source") == "menu:workspace":
        clicked = context["workspace"]
        workspace = rpc("workspace.get", workspace=clicked["id"])
        if Path(workspace["cwd"]) != Path(clicked["cwd"]):
            raise ValueError("The clicked workspace changed. Open its menu again.")
        cwd = clicked["cwd"]
    else:
        clicked = context["pane"]
        pane = rpc("pane.get", pane=clicked["id"])
        if pane.get("cwd", clicked["cwd"]) != clicked["cwd"]:
            raise ValueError("The clicked pane changed directory. Open its menu again.")
        workspace = rpc("workspace.get", workspace_id=pane["workspace_id"])
        cwd = clicked["cwd"]
    if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise ValueError(f"The selected directory is unavailable: {cwd}")
    return workspace["cwd"], cwd


def open_launcher(context, config, rpc=call, view="picker", preset=None, operation=None):
    root, cwd = launch_target(context, rpc)
    # Pass argv directly: paths and user commands never become shell source here.
    command = [sys.executable, str(Path(__file__).resolve()), "--" + view,
               "--cwd", cwd, "--config", str(config)]
    if os.environ.get("LUVUS_BIN_PATH"):
        command += ["--luvus-bin", os.environ["LUVUS_BIN_PATH"]]
    if preset is not None:
        command += ["--preset", json.dumps(preset)]
    if operation:
        command += ["--operation", operation]
    if view in ("project", "picker"):
        command += ["--source", json.dumps(context)]
    opened = rpc(
        "terminal.backend.create", cwd=root, placement={"kind": "workspace"},
        focus=True, label=preset["name"] if preset and view == "run" else "CLI Launcher",
        command=command,
    )

    from tab_titles import remember
    remember(rpc, opened.get("pane_id"), "▷ " + (preset["name"] if preset and view == "run" else "CLI Launcher"))
    return opened


def render_manifest(presets):
    validate(presets)
    lines = [f'id = "{MODULE_ID}"', 'name = "CLI Launcher"', f'version = "{VERSION}"',
             'min_luvus_version = "0.13.4"',
             'description = "Open saved CLI tools directly from the right-click menu."']
    for windows in (False, True):
        suffix = "-windows" if windows else ""
        prefix = ["python" if windows else "python3", "launcher.py"]
        actions = []
        for preset in presets:
            raw = json.dumps(preset, ensure_ascii=False, sort_keys=True)
            identity = hashlib.sha256(raw.encode()).hexdigest()[:16]
            title = quick_title(preset)
            actions.append(("open-" + identity, title or f"Open {preset['name']} in new tab",
                            prefix + ["--action", "run", "--preset", raw], bool(title)))
        actions += [("project", "Agent Launcher…", prefix + ["--open"], True),
                    ("launch", "Manage saved tools…", prefix + ["--open"], False),
                    ("refresh", "Refresh tool menu", prefix + ["--action", "refresh"], False)]
        for action, title, command, menu in actions:
            fields = {"id": action + suffix, "title": title,
                      "platforms": ["windows"] if windows else ["macos", "linux"],
                      "command": command}
            if menu:
                fields["contexts"] = ["pane", "workspace", "agent"]
            lines += ["", "[[actions]]"] + [f"{key} = {json.dumps(value, ensure_ascii=False)}"
                                            for key, value in fields.items()]
    lines += ['', '[[events]]', 'on = "pane.focused"', 'platforms = ["macos", "linux"]', 'command = ["python3", "tab_titles.py"]', '', '[[events]]', 'on = "pane.focused"', 'platforms = ["windows"]', 'command = ["python", "tab_titles.py"]']
    return "\n".join(lines) + "\n"


def refresh_menu(config, rpc=call, root=None):
    bundle_root = Path(root) if root is not None else None
    root = Path(root or Path(__file__).resolve().parent)
    config.parent.mkdir(parents=True, exist_ok=True)
    lock = config.with_name("menu-refresh.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("Menu refresh is busy. Retry Refresh tool menu in Tools → More… (r in plain mode).") from error
    temporary = None
    try:
        os.close(descriptor)
        from toolkit_core import ROOT as toolkit_root
        from toolkit_core.install import render
        import tomllib
        manifest = (bundle_root or toolkit_root) / "luvus-module.toml"
        previous = manifest.read_bytes()
        content = render({"cli-launcher": tomllib.loads(render_manifest(load_presets(config)))})
        info = rpc("module.info", id=MODULE_ID)
        if Path(info["root"]).resolve() != root.resolve():
            raise ValueError("The registered module belongs to a different directory.")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest)
        try:
            rpc("module.unlink", id=MODULE_ID)
            rpc("module.link", path=str(root), disabled=not info["enabled"])
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            # Reconcile an uncertain reply before recovery; never unlink a successful relink.
            modules = rpc("module.list")["modules"]
            if not any(module["id"] == "kacper.toolkit" for module in modules):
                manifest.write_bytes(previous)
                rpc("module.link", path=str(root), disabled=not info["enabled"])
            raise ValueError(f"Menu refresh failed: {error}. Retry Refresh tool menu in Tools → More… (r in plain mode).") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def selected_preset(config, raw):
    preset = validate([json.loads(raw)])[0]
    if preset not in load_presets(config):
        raise ValueError("This tool changed or was deleted. Open its right-click menu again.")
    return preset


def menu_action(action, config, context, raw=None, rpc=call):
    if action == "refresh":
        return refresh_menu(config, rpc)
    if action == "project":
        return open_launcher(context, config, rpc, view="project")
    preset = selected_preset(config, raw)
    return open_launcher(context, config, rpc, view="run", preset=preset)


def form(config, operation, preset=None):
    previous = load_presets(config)
    if preset is not None and preset not in previous:
        raise ValueError("This tool changed or was deleted. Open its menu again.")
    updated = [dict(item) for item in previous]
    print(f"\n{operation.title()} tool\n\nCtrl+C cancels without saving.\n")
    if operation == "delete":
        if input(f"Delete {preset['name']}? [y/N] ").strip().lower() != "y":
            return
        updated.remove(preset)
    else:
        old = preset or {"name": "", "command": ""}
        name = input(f"Name [{old['name']}]: ").strip() or old["name"]
        command = input(f"Command [{old['command']}]: ").strip() or old["command"]
        item = {"name": name, "command": command}
        if preset is None:
            updated.append(item)
        else:
            updated[updated.index(preset)] = item
        validate(updated)
        print(f"\nName: {name}\nCommand: {command}")
        if input("Save? [Y/n] ").strip().lower() not in ("", "y", "yes"):
            return
    save_presets(config, updated, previous)
    print("Saved.")


def single_view(config, cwd, view, preset=None, operation=None):
    os.chdir(cwd)
    try:
        if view == "run":
            selected_preset(config, json.dumps(preset))
            print(f"{preset['name']} — {cwd}\n", flush=True)
            print(f"\nExit status: {run_tool(preset['command'], cwd)}")
        else:
            form(config, operation, preset)
    except (KeyboardInterrupt, EOFError):
        return
    except (ValueError, OSError) as error:
        print(f"\nError: {error}")
    try:
        input("\nPress Enter to close.")
    except (KeyboardInterrupt, EOFError):
        pass


def validate(presets):
    if not isinstance(presets, list):
        raise ValueError("Presets must be a JSON list.")
    names = set()
    for preset in presets:
        if not isinstance(preset, dict) or set(preset) != {"name", "command"}:
            raise ValueError("Each preset needs exactly a name and command.")
        for key in ("name", "command"):
            value = preset[key]
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError(f"Preset {key} must be non-empty, single-line text without control characters.")
        if preset["name"].casefold() in names:
            raise ValueError("Preset names must be unique.")
        names.add(preset["name"].casefold())
    return presets


def load_presets(path):
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return [dict(preset) for preset in DEFAULTS]


def save_presets(path, presets, previous):
    validate(presets)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A short exclusive file lock prevents two picker tabs losing each other's edits.
    lock = path.with_suffix(".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError(f"Another picker is saving. Retry; if it crashed, remove {lock}.") from error
    temporary = None
    try:
        os.close(descriptor)
        if load_presets(path) != previous:
            raise ValueError("Presets changed in another tab. Try your edit again.")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(presets, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    if os.environ.get("LUVUS_BIN_PATH"):
        try:
            refresh_menu(path)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            print(f"Saved, but menu refresh failed: {error}", file=sys.stderr)


def shell_command(command, windows=None):
    windows = os.name == "nt" if windows is None else windows
    if windows:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            raise ValueError("PowerShell is not available on PATH.")
        return [shell, "-NoLogo", "-NoProfile", "-Command", command]
    # Interactive shell job control can steal the picker's foreground terminal.
    # Tools still inherit the real PTY; only the shell wrapper is non-interactive.
    return [os.environ.get("SHELL") or "/bin/sh", "-c", command]


def run_tool(command, cwd):
    child = subprocess.Popen(shell_command(command), cwd=cwd)
    # Both processes share the terminal foreground group; Ctrl+C also reaches the tool.
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:
            continue


def choose_index(presets, prompt="Preset number: "):
    index = int(input(prompt)) - 1
    if index < 0 or index >= len(presets):
        raise ValueError("Choose one of the listed preset numbers.")
    return index


def picker(config, cwd):
    os.chdir(cwd)
    while True:
        try:
            presets = load_presets(config)
            print(f"\nManage saved tools — {cwd}\n")
            for index, preset in enumerate(presets, 1):
                print(f"  {index}. {preset['name']}  ({preset['command']})")
            print("\nEnter a number to launch · a add · e edit · d delete · r refresh menu · q close")
            choice = input("> ").strip().lower()
            if choice == "q":
                return
            if choice == "r":
                refresh_menu(config)
                print("Menu refreshed.")
            elif choice in ("a", "e", "d"):
                preset = None if choice == "a" else presets[choose_index(presets)]
                form(config, {"a": "add", "e": "edit", "d": "delete"}[choice], preset)
            else:
                index = int(choice) - 1
                if not 0 <= index < len(presets):
                    raise ValueError("Choose one of the listed preset numbers.")
                preset = selected_preset(config, json.dumps(presets[index]))
                print(f"\nExit status: {run_tool(preset['command'], cwd)}")
        except KeyboardInterrupt:
            print("\nCancelled. Enter q to close the launcher.")
        except EOFError:
            return
        except (ValueError, OSError) as error:
            print(f"\nError: {error}\nPresets: {config}")
            try:
                if input("Enter to retry, q to close: ").strip().lower() == "q":
                    return
            except (EOFError, KeyboardInterrupt):
                return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--open", action="store_true")
    mode.add_argument("--picker", action="store_true")
    mode.add_argument("--action", choices=["refresh", "run", "project"])
    mode.add_argument("--project", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--form", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--preset")
    parser.add_argument("--operation", choices=["add", "edit", "delete"])
    parser.add_argument("--luvus-bin")
    parser.add_argument("--source", default="{}")
    parser.add_argument("--plain", action="store_true", help="Use the original line-based menus")
    args = parser.parse_args()
    if args.luvus_bin:
        os.environ["LUVUS_BIN_PATH"] = args.luvus_bin
    graphical = (args.project or args.picker) and not args.plain and sys.stdin.isatty()
    if graphical:
        python = Path(__file__).resolve().parent / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if python.exists() and Path(sys.prefix).resolve() != python.parent.parent.resolve():
            os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    try:
        if args.open or args.action:
            directory = os.environ.get("LUVUS_MODULE_CONFIG_DIR")
            if not directory:
                raise ValueError("Luvus module configuration directory is missing.")
            config = Path(directory).resolve() / "presets.json"
            context = json.loads(os.environ.get("LUVUS_MODULE_CONTEXT_JSON", "{}"))
            result = (open_launcher(context, config) if args.open else
                      menu_action(args.action, config, context, args.preset))
            print(json.dumps(result))
        else:
            if not args.cwd or not args.config:
                parser.error("terminal views require --cwd and --config")
            if graphical:
                try:
                    from launcher_ui import LauncherApp
                except ImportError as error:
                    raise ValueError("Dashboard dependency missing. Install requirements.txt in this module's .venv, or use --plain.") from error
                LauncherApp(args.config.resolve(), args.cwd, json.loads(args.source),
                            section="tools" if args.picker else "links").run()
            elif args.picker:
                picker(args.config.resolve(), args.cwd)
            elif args.project:
                from project_launcher import project_view
                project_view(args.config.resolve(), args.cwd, json.loads(args.source))
            else:
                if args.run and not args.preset or args.form and not args.operation:
                    parser.error("--run requires --preset; --form requires --operation")
                single_view(args.config.resolve(), args.cwd, "run" if args.run else "form",
                            json.loads(args.preset) if args.preset else None, args.operation)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        message = f"CLI Launcher: {error}"
        print(message, file=sys.stderr)
        if args.open or args.action:
            try:
                call("ui.toast", text=message)
            except Exception:
                pass  # The original error remains in the module log if Luvus is unavailable.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
