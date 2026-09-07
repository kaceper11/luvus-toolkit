> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Project Commands for Luvus

Run reviewed project commands and development services with persistent output and truthful results. The console includes run history, clickable problems, health checks, resource measurements and explicitly bound container controls. Tasks keeps ownership of task completion and ORCH; CLI Launcher keeps generic interactive tool launches.

## Open

The local installation uses module ID `personal.project-commands`. Right-click a workspace, pane or agent and choose **Open Project Commands**. Or use:

```sh
luvus module pane open personal.project-commands commands
```

On native Windows use entrypoint `commands-windows`. In a console, **Discover** opens a searchable checklist grouped by category, with the originating script and arguments shown for the selected row. Select only the commands you want, then review and save them. **Add**, **Edit** and **Remove** manage individual commands; **Configuration** remains available for advanced fields. Select a command and **Run / Start**. Discovery, opening the console and saving configuration do not run project scripts.

**Runs / Services** provides captured output, a live output terminal, rerun, graceful stop, force-stop, problems and URLs. **Restart** reviews the replacement definition, gracefully stops the selected service, waits up to 15 seconds for a confirmed exit, then starts the replacement. An unknown exit or stop timeout prevents replacement; force-stop remains a separate explicit action after ten seconds. Closing the console does not stop its output terminals or services. Ctrl+Q closes the panel after protecting unsaved drafts and pending operations. **Reload config** asks before discarding edits. Ctrl+R refreshes and checks the selected completed result’s source freshness; **Check freshness** does the same explicitly. The result is timestamped as an observation, never a promise that later edits were absent. Invalid lifecycle actions are disabled, and Tab/Shift+Tab navigate controls; button strips scroll at narrow widths.

## Installation

Requires Luvus 0.13.4+ with terminal backend methods and Python 3.11+. This module uses its own virtualenv with Textual 6 and psutil 7. The manifest bootstrap can be started by older `python3`, but redirects to this directory's `.venv`.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
luvus module link /absolute/path/to/luvus-project-commands
```

Native Windows:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
luvus module link C:\absolute\path\to\luvus-project-commands
```

Linux/WSL use their own Linux tools, filesystem paths and Luvus session. The module has no startup command, automatic project setup, background global daemon, tracker publication or agent launch.

## Local definitions

Configuration lives in `<LUVUS_HOME>/modules/config/personal.project-commands/projects.json` (normally `~/.luvus/...`). It is installation-local and indexed by canonical checkout path. No configuration or run artifacts are written to Novulum repositories. Configuration writes are atomic and reject concurrent changes; malformed JSON is preserved. Another module's injected config directory is never used. Removing a worktree does not block saves for other projects; its configuration is retained, but launch still requires an existing directory.

Example project entry within `{"version":1,"projects":{...}}`:

```json
{
  "/absolute/canonical/checkout": {
    "commands": [
      {
        "id": "tests",
        "name": "Backend tests",
        "kind": "command",
        "category": "test",
        "argv": ["dotnet", "test", "Project.slnx", "--configuration", "Release", "--logger", "trx", "--results-directory", "{run_dir}"],
        "report_format": "trx",
        "report": "*.trx"
      },
      {
        "id": "web",
        "name": "Frontend dev",
        "kind": "service",
        "category": "dev",
        "argv": ["corepack", "pnpm", "--dir", "web", "dev", "--port", "3100"],
        "ports": [3100],
        "urls": ["http://localhost:3100"],
        "readiness": {"kind": "tcp", "host": "127.0.0.1", "port": 3100}
      }
    ],
    "containers": [],
    "health_providers": []
  }
}
```

Optional fields: `cwd` is checkout-relative; `inputs` lists additional Git roots or files to fingerprint; `platform_argv` overrides argv for `macos`, `linux` or `windows`; `exclusive_group` prevents concurrent managed stacks across worktrees. A platform override can replace a script marked in `unsupported_platforms`. HTTP readiness accepts a loopback `url` and optional expected `status` (default 200), without following redirects. Readiness requires an observed listening process belonging to the managed tree; inaccessible attribution remains unknown.

Arguments are transported as arrays. Shell syntax is supported only when the definition explicitly names its shell. Commands receive the inherited environment; use existing credential mechanisms rather than embedding secrets in argv/configuration. Managed commands are non-interactive (stdin is closed). Use CLI Launcher for interactive login/setup tools, then rerun the relevant managed validation command. Windows Corepack/npm/pnpm batch shims use verified adjacent Node entrypoints; unusual shim layouts require an explicit `platform_argv`. Other batch files require an explicitly reviewed shell/native entrypoint.

Bookulum suggestions distinguish frontend scripts from .NET build/test/restore. ESLint JSON, Vitest JSON and multi-file TRX suggestions are available. The existing Aspire stack remains one managed service: its known fixed ports and shared volumes are guarded, not rewritten. The equivalent `pnpm dev` suggestion receives the same guard. The AppHost can run local migrations when explicitly started. Full-stack multi-worktree isolation still requires a separate AppHost change. Other configurable services can run independently in separate worktrees.

## Evidence and lifecycle

A run stores the reviewed definition, exact checkout, selected Luvus session, native terminal locator and process creation identity. Start refuses a changed definition/session. Repeated Start reuses a matching active run. Every request ID, including requests that reused an active run, remains associated with that run for its retained lifetime. Retrying that ID after completion returns the existing result, not a new execution. Once history is pruned, request reconciliation is unavailable; do not blindly retry expired requests. `starting`, `running`, `cancelling`, `passed`, `failed`, `cancelled`, `unknown` and `interrupted` remain distinct. Missing output terminals, spawn failure and lost replies never produce success. Unknown runs retain their reservation until inspected and explicitly acknowledged; there is no blind retry.

The supervisor records the actual exit and controls an owned POSIX process group or Windows Job Object. Stop requests graceful termination; force-stop becomes available after ten seconds. Saved PIDs and port occupants are never used as stop targets. On POSIX a live group leader retains the process-group identity until cleanup; an ownership pipe closes the group if the supervisor exits abruptly. Lost exit evidence is still marked unknown. Descendants which deliberately escape the process group cannot be adopted or stopped automatically. Daemonizing commands are outside the supported managed-service contract. An escaped descendant holding output open produces interrupted tracking.

Source fingerprints include HEAD, index entries, tracked working-file contents/modes, non-ignored untracked files and declared additional inputs. They are checked before/after execution, periodically while running, and when opening results/problems. A passing exit is separate from source freshness and parser completeness. History lists do not claim a current green result without a fresh check.

This is observed source evidence, not a hermetic build: ignored files must be declared when relevant; undeclared dependencies or edits reverted between samples cannot be proven absent. Non-Git directories, missing inputs, symlinks, oversized inputs, or snapshot limits produce unknown freshness. Limits are 50,000 files, 32 MiB per input and ten seconds per snapshot.

Completed history retains the newest 50 runs per checkout for at most 14 days, with a 500 MiB aggregate budget for retained records/logs/reports. Active and unresolved runs are preserved. Captured command output keeps the first 10 MiB; service output retains a bounded recent log. Truncation is explicit. Reports are limited to 100 files and 10 MiB combined; problems retain at most 5,000 entries for both text and structured reports. Vitest suite/import failures are shown even when no assertion results exist; incomplete report evidence remains partial. Raw tool output may contain sensitive application data: files are private on Unix, previews redact known environment secrets, and every agent send is separately previewed and explicitly targeted. Redaction is best effort, not a secret scanner.

## Health, resources and containers

Health checks select Git/Node/package-manager/.NET checks from the project files and resolve every configured command executable from the inherited PATH. Arbitrary configured executables are checked for existence without running them. Checks compare supported Node/SDK contracts, inspect declared inputs, Docker availability when relevant, setup evidence and configured readiness. A missing tool is distinct from a failed or unverified check. Health checks never install tools, alter credentials or run setup. Corepack presence alone is not proof that a pinned pnpm version is cached. Provider credentials remain with their owners.

Resources uses native terminal identities and psutil to group host process trees. Native identity is checked before and after measurement; each process lifetime is counted once. CPU samples warm up again when tree membership changes. RSS includes shared pages, so it is not unique physical-memory accounting. Unknown attribution remains unknown. Polling occurs only while the Resources tab is visible; viewing it never stops processes. Container/VM resource use is not added to host-process totals.

To bind a container, select **Bind by ID**, provide its Docker context and exact container ID, inspect the returned daemon/creation identity, then confirm the project binding. `manual` ownership enables explicit start/stop; `aspire` ownership leaves lifecycle with Aspire and allows status/logs. Bind only containers you have verified belong to this checkout. Binding is an explicit association, not inferred from a similar container name. Actions recheck immutable identity and pin the Docker context without inherited `DOCKER_HOST` redirection. Duplicate bindings are rejected even across aliases of the same daemon. **Unbind** removes the selected project association without stopping or deleting the container. Failed action output is retained in run history; uncertain mutations require inspection. No container/volume deletion or general container manager is provided.

## Integration API

Run the module's interpreter and `launcher.py api --store <own-module-state>` with one UTF-8 JSON request on stdin. `--store` is optional when the standard Luvus home is selected. Input is limited to 1 MiB; output is `{"version":1,"result":...}` or `{"version":1,"error":{"message":...}}` with a nonzero exit. `describe` lists operations; `session` returns the selected socket/generation for a reviewed launch. Responses echo a supplied `request_id`. `discover`, `config`, `save-config`, `status`, `cancel`, `resolve-interrupted`, `health`, `resources`, `container-preview` and `container-action` use an explicit canonical `root`.

A run request is:

```json
{
  "version": 1,
  "action": "run",
  "root": "/absolute/canonical/checkout",
  "command": "tests",
  "request_id": "caller-generated-stable-id",
  "reviewed": {"id":"tests","name":"Tests","argv":["dotnet","test"],"kind":"command","category":"test"},
  "session": {"socket":"/selected/luvus.sock","generation":"native-server-generation"}
}
```

`reviewed` must match the saved definition; session identity comes from the caller's selected socket and native terminal inventory. Reuse `request_id` to reconcile an uncertain response. Do not generate a new ID as a timeout retry. Status can reconcile an exact `request_id` (including an alias that reused a run), with an optional matching `run`; cancel uses `run`. `restart` takes `run`, `reviewed`, `session` and a new stable `request_id`; force-stop uses `force:true`. Interrupted acknowledgement requires `confirm:true` and refuses a surviving verified supervisor/child. Container requests identify a saved `container`, `operation`, and `confirm:true` for start/stop. `save-config` supplies the full `config` and its exact `previous` value.

Tasks may consume evidence and request explicitly authorized setup/validation runs. It keeps task history, artifacts, ORCH gates and completion. Launcher arrangements may reference command/service IDs. The Tasks API handoff now supplies the reviewed definition and session and reconciles request aliases. Launcher’s owner-provided `commands_adapter.py` consumes this API. The isolated smoke exercises both actual adapters when those sibling modules are installed, without changing their production settings. To enable Tasks, set its **Configuration → Integration bridges → Project Commands** argv to this module’s `.venv` Python, `launcher.py`, `api`, `--store`, and this module’s config directory. In Launcher use its **Settings → owner integrations → Project Commands** connection. Per-project command definitions must still be reviewed here; connecting an adapter never starts setup or services.

Owners can supply reviewed `health_providers` entries with `name`, `module`, `argv` and optional `recovery_actions` mapping action IDs to argv arrays. A check receives `{"version":1,"root":"..."}` on stdin and returns `{"checks":[{"name":"...","state":"healthy|missing|failing|unverified","evidence":"...","action":"next-action text","at":1780000000,"recovery":{"module":"owner.id","action":"repair"}}]}`. Provider output is bounded to 64 KiB/five seconds; supplied evidence older than 60 seconds is unverified. Providers own credential redaction and project-specific checks.

Recovery references are not executable log text. Only a matching, locally reviewed `recovery_actions` argv adapter can run, after user confirmation, receiving version/root/action JSON on stdin. Native generic module actions lack an explicit checkout target, so the module does not use them as a fallback. Missing adapters remain unavailable. No other module's database or source files are shared.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/smoke_luvus.py
```

The second command explicitly starts/stops an isolated temporary Luvus home, registers this module there, and runs a harmless disposable process. It does not control production services or start coding agents. The Git fixtures are temporary repositories; no real repository is committed or modified.

The CI definition covers macOS, Linux and native Windows unit/process tests. It has not been published or executed remotely by this installation. See [VALIDATION.md](VALIDATION.md) for what was actually verified and the remaining platform limits.

Module tabs use short descriptive names with a small symbol. New agent tabs include their task or instruction. Existing manual names are preserved; background tabs receive their name when focused. Naming is best effort and never retries an agent launch.


### Bounded command evidence (0.1.2)

The version-1 `evidence` action accepts canonical `root`, exact `request_id`, and optional matching `run`. It returns the normal current run status plus `output_excerpt` (up to 16,000 log bytes: the beginning and end for longer logs, decoded and passed through existing credential redaction) and `excerpt_truncated`. Request aliases remain bound to their original run. Mismatched checkouts/runs and symlinked logs are rejected. Consumers must verify identity and freshness; an excerpt is diagnostic context, not proof of success. Tasks 0.8.0 uses this owner API to supply failed-check output to its bounded repair attempt.


In 0.1.3, long excerpts retain the first/last 8,000 bytes with an explicit omission marker. `diagnostic_excerpt` also supplies bounded, redacted error/problem text. This prevents verbose startup output from hiding a failure at the end of a log.
