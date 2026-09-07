> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# CLI Launcher for Luvus

Right-click a **pane**, **workspace**, or **agent** and select:

- **Open Codex — skip permissions**: `codex --dangerously-bypass-approvals-and-sandbox`
- **Open Muse — YOLO**: `muse --yolo`
- **Agent Launcher…**: saved tools, branch/worktree launch, links, layouts, bundles, and settings

The two quick actions launch immediately in one new tab at the clicked directory.
Other saved tools remain available in the dashboard and through their module action IDs.
The launcher does not change other agents' permission flags.

## Branch and worktree launch

In **Agent Launcher… → Tools**, select any saved tool and choose **Launch…**:

- **New worktree / new branch** (default for Git repositories): select current HEAD or a local/remote-tracking ref, name the branch, and choose an absolute destination outside the source checkout.
- **Existing local branch / worktree**: reuse the branch's existing worktree, or choose a fresh destination for it.
- **Switch this checkout**: switch a clean checkout to a local branch, then launch. Uncommitted files and branches checked out elsewhere block switching.
- **Current directory**: open a new agent tab without changing branches. Also works outside Git.

The final screen shows the repository, branch, destination, and command. No Git changes
happen until **Launch**. Branches/paths are passed as Git arguments; no automatic fetch,
stash, reset, force checkout, setup command, or service launch occurs.
Suggested worktrees live under this module's configuration directory; the destination
can be changed. Muse runs inside that prepared worktree without creating a nested one.

**More… → Open here immediately** keeps the direct dashboard launch available.
**More… → Retry last launch…** retries a known rejected tab creation using the prepared
checkout. The review dialog also retains a rejected launch for retry. A timeout or lost
reply is not replayed: inspect the tab/destination first. Only the latest explicit launch
is tracked in `agent-launch.json`; created branches/worktrees are never automatically deleted.

## Requirements and installation

Luvus 0.13.4+, Python 3.9+ (`python3` on macOS/Linux; `python` on Windows),
and your separately installed CLI tools. The dashboard uses Textual in this
module's own virtual environment. Windows also requires `pwsh` or `powershell`
on PATH for saved tool commands.

From the module directory, install the UI once:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows use `python -m venv .venv` and
`.venv\Scripts\python.exe -m pip install -r requirements.txt`.
Dashboard launches select this module's environment automatically; other modules
and system Python are not changed. Immediate tool actions and the plain menus
still use only the Python standard library. No installation happens on menu clicks.

```sh
luvus module link /absolute/path/to/luvus-cli-launcher
```

This is a locally linked module: its directory must remain writable. The manifest
is generated from saved tools; do not edit its generated actions by hand.

## Saved tools

Open tool management through **Agent Launcher…**, or directly:

```sh
luvus module run personal.luvus-cli-launcher launch
```

On Windows use `launch-windows`. Luvus 0.13.4 does not support module settings
buttons that open a management screen.

The management action opens the dashboard's **Tools** tab. Select a row, then
**Launch…**, **Add**, **Edit**, or **More…** for removal and menu refresh. You can also
reach Links, Layouts, Bundles and Settings from the same hub. Tool forms have labelled name and command fields;
invalid entries retain your input so you can correct them. Opening a tool from
the dashboard creates a separate native tab.

Presets are stored in `presets.json` in Luvus's per-module configuration directory,
shared across workspaces on this installation. Existing presets are preserved.
Each save updates the right-click menu by refreshing only this module's
registration. Running tool tabs are not closed, and no server restart is needed.
Close and reopen an existing context menu to see changed entries. A stale entry
cannot launch a command that was changed or deleted.

Permission flags are part of the saved command. Examples: `codex`, `claude`,
`git status`, or `python3 -m http.server 8000`. Only save commands you trust.
macOS/Linux use `$SHELL -c`, falling back to `/bin/sh`; Windows uses PowerShell
without a profile. Use an executable on PATH or its absolute path; interactive
shell aliases and profiles are not loaded. Commands and quoting are platform-specific.

Pane and agent actions use the clicked pane's directory. Workspace actions use
the clicked workspace root, even when another project is focused. When the tool
exits, its tab shows the exit status and **Press Enter to close**. Ctrl+C reaches
the running CLI. The launcher never types commands into an existing terminal.

## Project dashboard

Right-click the target pane/workspace/agent → **Project…**, or invoke action
`project` (`project-windows` on Windows). Pane targeting is captured when opening
the menu. Git projects use the selected checkout root; ordinary tool actions
continue using the captured pane directory, including subdirectories.

Use the **Tools**, **Links**, **Layouts**, **Bundles**, and **Settings** tabs.
The project path stays visible. Lists are searchable and show selection details;
empty lists explain the next step. Click a row/button, or use ↑/↓, Enter and Tab.
**Ctrl+N** adds an item, **Ctrl+F** searches, **Ctrl+R** refreshes, and **Ctrl+Q**
closes the dashboard. Forms support **Esc** to cancel and **Ctrl+S** to save. Persistence errors keep the editor and entered values visible. In search, Enter opens the selected result and Esc clears the filter.
During an operation the dashboard prevents duplicate actions and remains
responsive. Closing is deferred until the action finishes or its dialog is cancelled.
Short terminals use a compact header; below 50 columns, actions wrap into two rows. 80×24 remains recommended; 40×24 keeps all actions reachable.

Save scope is a dropdown: all repository worktrees, or only this worktree.
Forms show plain-language summaries instead of raw JSON. Settings contains
optional owner integrations and **Worktree overrides → Configure** to restore
repository defaults. Standard terminal color preferences, including `NO_COLOR`,
are respected.

Configuration lives in `projects.json` beside the existing `presets.json`, never
in a repository. Repository defaults are keyed by Git's canonical common
directory; worktree overrides are keyed by the canonical checkout root. This
shares defaults across Git worktrees while keeping overrides, launches and
services distinct. Non-Git folders use their canonical path for both identities.
Entries have stable IDs. A worktree entry overrides the same repository entry;
**More… → Remove** writes a null entry at the selected scope. Resetting worktree
overrides restores all repository defaults for that worktree. Moving a checkout
does not silently migrate its configuration. Global tools remain separate.

**Links:** add/edit/open named repository, docs, design, app, deployment-preview,
or issue-board destinations. HTTP(S) URLs cannot contain credentials or control
characters. Relative document paths resolve against the selected worktree.
Existing Markdown, text, PDF, HTML, and image documents are supported; scripts
and executable URL schemes are rejected. Destinations go to the platform opener
as arguments, never shell source. For a managed app URL, choose **Running service
URL** and enter the service name; this queries Project Commands without starting
the service or caching its URL.

**Layouts:** choose **Add**, then one pane, two side-by-side/stacked panes, or
copy an open project tab from a list. Choose each pane's saved tool from a
dropdown, or select Shell and enter its program and arguments (one per line).
No pane IDs or JSON are required. Capture stores only pane roles and split geometry, not running
processes, conversations, or terminal contents. Up to 16 roles are supported.
Use **Edit** to update an arrangement under the same ID, including its tool references.
Changed/deleted global tools must be reassigned before a new launch.

Opening creates a dedicated tab with native panes, then applies the captured
geometry using the new pane IDs. Reopening focuses the recorded live arrangement;
even an edited configuration does not replace an existing completed instance.
Use **More… → Open another copy** for an explicit new copy. Only the latest copy for an arrangement/worktree
is tracked; older copies remain open. Services remain owned/deduplicated by
Project Commands. Enter optional startup service/test names one per line; the
adapter resolves their revisions and kinds. This field is disabled until Project
Commands is connected. Connected command references refresh their revisions on every save, including unchanged names. When disconnected, existing references are preserved. No
entrypoint is configured automatically.

**Recovery:** **More… → Show launch status** displays recorded role/command
outcomes. Failures show the reason and retained steps in a dialog.
**More… → Recover** recreates missing panes alongside survivors and retries confirmed failed
steps. Successful steps are retained. A timeout or interrupted response is
uncertain: pane results are reconciled by unique labels and live terminal IDs;
unresolved outcomes are not replayed. Owner-run startup confirmation does not
mean asynchronous tests passed—consult Project Commands for completion/results.
Moved panes must be restored before recovery. After a server restart, inspect
restored tabs before using **More… → Forget launch tracking** and creating another copy.
Forgetting tracking never closes panes/stops services and may allow duplicate
tools; it requires explicit confirmation.

**Bundles:** Tasks supplies the relationship snapshot. The list previews exact
checkout paths and expected branch/detached identities. Opening reuses matching
workspaces, opens missing ones, reports failures per member, and never switches
branches or creates worktrees. Saved arrangements are off by default; selecting
them shows their commands before confirmation. Retry reuses successful opens. An unavailable member or stale layout fails only that member, including when optional layouts are selected.

**Integrations:** Settings detects the installed Tasks and Project Commands entrypoints through the selected Luvus registry. **Test & save** verifies the read API before connecting; failed checks retain the form and leave saved configuration untouched. Clearing Program disconnects. Advanced users can still enter an explicit compatible program and arguments. No module installation or service launch occurs during connection setup.

Tasks supplies bundles through its read-only `bundle-api`. The launcher's `commands_adapter.py` translates to Project Commands' public API; it does not read owner databases or manage processes. Startup requests preserve reviewed definitions, exact checkout/session identity and durable operation IDs. Owner-side reservations reuse matching active services. Live URLs require one matching ready service with an owner status update within 15 seconds; URLs must already be configured by the owner. Missing/ambiguous/stale URLs direct you to Project Commands.

Both installed integrations were connected and their read APIs checked on 2026-09-06. This checkout returned zero bundles and commands; define relationships in Tasks and command/service definitions in Project Commands. No production services or tools were started to verify the connections. See [CONTRACTS.md](CONTRACTS.md).

### Plain terminal fallback

The original line menus remain available with `--plain` (and are used for
noninteractive stdin). For example, from a Luvus terminal:

```sh
python3 launcher.py --project --plain --cwd /absolute/project --config /absolute/module-config/presets.json
```

Use `--picker --plain` for the original numbered tool picker. Plain project
menus retain their letter shortcuts and advanced JSON prompts. Existing direct
menu actions, saved files, and integration contracts remain compatible.

## Troubleshooting

```sh
luvus module info personal.luvus-cli-launcher
luvus module log personal.luvus-cli-launcher
luvus module config-dir personal.luvus-cli-launcher
luvus module run personal.luvus-cli-launcher refresh
```

On Windows use `refresh-windows`. You can open management directly with action
`launch` (`launch-windows` on Windows).

Preset writes are atomic and reject conflicting edits. Invalid JSON is left
untouched. If menu refresh fails, presets remain saved and the launcher attempts
to restore the previous registration. Use **Tools → More… → Refresh right-click
menu** (`r` in plain mode) to retry. If a
connection failure leaves the module unregistered, link the module directory
again, then run its refresh action. After a process crash, remove `presets.lock`
or `menu-refresh.lock` only after confirming no save/refresh is running.

Project files use the same exclusive-lock/atomic-replace strategy. Their locks
are `projects.lock`, `arrangement-runs.lock`, and `bundle-runs.lock`. A crash can
leave a lock: confirm that its recorded process is no longer running before
removing it. Never remove the JSON state to retry an uncertain operation without
first inspecting existing panes and owner runs. Files are bounded to 1 MiB;
exceeding the limit preserves the previous file. No background process monitors
or cleans this state.

If a tool launch times out, inspect tabs before retrying: it may have succeeded
even though the response was lost. The launcher does not automatically retry.

To hide these menu entries:

```sh
luvus module disable personal.luvus-cli-launcher
```

## Validation

```sh
.venv/bin/python -m unittest -v test_launcher.py test_projects.py test_ui.py test_adapter.py
.venv/bin/python native_smoke.py --binary /absolute/path/to/luvus
```

Checked 2026-09-06: 55 checks pass, including the original 16 checks, scoped
configuration, link safety, real temporary Git worktrees, native-call fixtures,
concurrent operations, cancellation, partial failures and owner contracts.
Thirteen headless UI checks cover mouse/keyboard interaction, form validation and
cancellation, scope selection, layout creation, search, concurrent edits,
missing integrations, duplicate-action prevention and 80×24 resizing. Dashboard
and form SVG renders were inspected visually in color and monochrome.
The opt-in native check starts and stops a disposable Luvus home, validates the
generated manifest, creates harmless Python shell panes, captures/applies geometry,
checks repeat focus and missing-first-pane recovery, and starts the actual
dashboard in a native terminal. It passed on macOS with
Luvus 0.13.4; it does not touch the production session.

Owner read APIs are verified live; production service starts and URL opening, host-terminal mouse-event forwarding, platform document handlers, and Windows/Linux interactive behavior remain unverified. The native test exercises
tool/shell pane topology, not agent conversations or real development services.

The review regression checks cover retained values on save conflict, Enter/Escape after search, unchanged command revision refresh, partial bundles with layouts, connection failures, compact action geometry, focused Input/Select/TextArea rendering, and a single focused Close button in information dialogs. Adapter checks cover owner identity, durable request IDs, stale revisions, uncertain starts and stale service URLs.

### Real owner API smoke check

```sh
.venv/bin/python integration_smoke.py --binary /absolute/path/to/luvus --commands-root /absolute/path/to/luvus-project-commands
```

Passed on macOS, 2026-09-06: real Project Commands API dispatch, service reuse across distinct operation IDs, ready URL resolution, stale revision rejection and cancellation. The script creates a disposable Luvus home, owner state and temporary HTTP service, then stops its own session. It does not use production service definitions or launch production services. This complements the 55 unit/UI/adapter checks and native dashboard smoke; Windows/Linux and host mouse forwarding remain unverified.

Module tabs use short descriptive names with a small symbol. New agent tabs include their task or instruction. Existing manual names are preserved; background tabs receive their name when focused. Naming is best effort and never retries an agent launch.
