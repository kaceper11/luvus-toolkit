# Luvus Toolkit

One installable Luvus module containing seven feature areas:

| Feature | What it does |
| --- | --- |
| Tasks | GitHub, Jira and Azure DevOps issues, handovers, history and orchestration |
| Git Sidebar | Local Git status, reviewed operations and Lazygit views |
| CLI Launcher | Saved tools, project layouts and agent launching |
| Send to Agent | Reviewed text and code context delivery |
| Project Commands | Persistent commands, services and diagnostics |
| AI Usage | Codex, Claude, OpenCode, Muse and Copilot usage |
| Keep Awake | Prevent idle system sleep while agents work |

**Status: migration candidate.** Automated checks pass on macOS, Linux and native Windows, including a fresh Windows installation. WSL2 and authenticated-provider acceptance on the target platforms remain pending. Existing installations should be retained until those checks pass. See [VALIDATION.md](VALIDATION.md).

## Install

Install Luvus 0.13.4 or newer, Python 3.11+, Node.js 22.12+ (with npm), and Git. Python must be available as `python3` on macOS/WSL or `python` on Windows; the bootstrap also searches versioned Python 3.11–3.14 executables when the default Python is older.

```sh
luvus module install kaceper11/luvus-toolkit
luvus module info kacper.toolkit
```

The native build step creates one local Python environment and installs the locked Node dependencies. No global Python packages or additional server are installed. Authenticate the optional agent and tracker tools separately on each computer. Lazygit is required for its Git views.

In **Settings → Modules → Luvus Toolkit**, each feature has its own enabled setting. Existing provider defaults are retained: only Codex usage is enabled initially. Module actions, widgets and panes use feature prefixes such as `tasks-open`, `git-sidebar-refresh`, and `ai-usage-codex`. Native Windows command entries use a `-windows` suffix.

Under WSL2, install Luvus, Python, Node and development tools in the distribution. Windows interoperability must be enabled for Keep Awake to control host power. Native Windows Claude status-line integration uses Git Bash, optionally selected through `CLAUDE_CODE_GIT_BASH_PATH`.

## Develop and extend

```sh
python3 toolkit.py bootstrap
python3 toolkit.py manifest
python3 toolkit.py test
npm test
luvus module link /absolute/path/to/luvus-toolkit --disabled
```

Use `python` on Windows. `toolkit.py` re-enters the shared `.venv` automatically. To run an isolated real-Luvus smoke test, set `LUVUS_BIN_PATH` to your installed binary and run `.venv/bin/python scripts/smoke.py` (Windows: `.venv\Scripts\python.exe`). The smoke test creates and stops its own disposable server. Run `node scripts/power-smoke.mjs` on macOS, native Windows and WSL2 to verify the native power helper exits after its parent; it briefly prevents idle sleep. Verify actual power requests on the host as well.

Feature code lives in `features/`. `toolkit_core/` owns configuration paths, UI name translation, locks, tab titles and platform helpers. Feature manifests are input fragments; **only the root manifest is installed**. Add a feature directory and register its name in `toolkit_core.FEATURES`, then regenerate the root manifest. Keep feature behavior local and share only helpers with actual multiple callers.

Internal legacy IDs remain in feature contracts and credential-store keys for migration compatibility. The transport boundary maps module operations to `kacper.toolkit`; no old module registration is required.

## Existing installation migration

Run the preview on the original computer:

```sh
python3 toolkit.py migrate
```

At cutover, disable the old modules and wait for their workers to stop, then apply:

```sh
python3 toolkit.py migrate --apply
```

Migration copies durable records and settings into the toolkit's feature directories. Recognized standalone Launcher bridges are rewritten to Toolkit entrypoints; custom integration commands are retained and must be checked before removing their source code. It retains source data, existing worktrees and credential-store references. Conflicting destination data stops migration; rerunning with unchanged source data is safe. Transient helper locks and sessions are recreated. Do not use migration as a cross-computer credential or workspace-path synchronization tool.

A connected Claude status-line wrapper must be restored using the old module before reconnecting through Toolkit. Restoration refuses to overwrite external edits. Keep the old code until the wrapper has been reconnected and checked.

Disabling a feature prevents new actions and stops its background observation workers. Existing user commands/services and agent panes are managed explicitly through their own controls. Disable the full module before unlinking it; restore the Claude status line first if connected.

## Keep Awake

Auto follows working agents across the selected session. On forces a temporary override; Off releases the toolkit's assertion. Modes reset to Auto when the helper restarts. On battery at 20% or below, prevention is released even in On mode. Missing power or agent observations release prevention. Assertions expire within 30 seconds if the owning helper dies; display sleep and lid behavior are not overridden.

## License

MIT. Installed dependencies retain their own licenses. Historical feature documentation records the standalone implementations and is not a claim of toolkit validation.
