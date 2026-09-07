# Tasks integration contracts — version 1

Source integration verified against the local owning modules on 2026-09-06. These commands are configuration examples; none has been enabled in production. Use absolute paths appropriate to the installation. No shell interpolation, automatic module installation, or fallback runner is used. The selected Luvus binary/socket environment is preserved.

## Project Commands

In Tasks → Configuration → Integration bridges → Project Commands, configure an argv array:

```json
["/absolute/project-commands/.venv/bin/python", "/absolute/project-commands/launcher.py", "api"]
```

Tasks adapts the owner's `version:1, action, root` API: `config` discovers the exact root's command definitions, `run` receives `command` and durable `request_id`, and `status` receives `run` or finds the original request among recent results. Responses are `{"version":1,"result":...}`. Tasks compares the full selected definition with current owner config immediately before dispatch. Services are excluded. A missing recent result remains uncertain; it is never a reason to resubmit. Request/root/run identity and producer freshness are verified. The owner manages processes, logs, cancellation and deduplication.

## CLI Launcher

Tasks → Configuration → Integration bridges → CLI Launcher:

```json
["/absolute/python3", "/absolute/luvus-cli-launcher/project_launcher.py", "--config", "/absolute/launcher-config/projects.json"]
```

Launcher → Project → integrations → Tasks must point back to the read-only Tasks entrypoint:

```json
["/absolute/luvus-tasks/.venv/bin/python", "/absolute/luvus-tasks/launcher.py", "bundle-api", "/absolute/tasks-config"]
```

The explicit Tasks state root prevents accidental use of the calling module's `LUVUS_MODULE_CONFIG_DIR`. `bundle-api` opens `tasks.sqlite3` using SQLite read-only mode and supports only `bundles.list` / `bundles.get`. It does not initialize a missing database. Requests include `version:1`, `request_id`, canonical absolute `cwd`, and `id` for get. Replies echo version/request/cwd and contain result or error. Bundles are visible only from a member checkout. A snapshot has `id`, `revision`, `name`, and 1–32 unique members containing canonical `cwd` and expected branch identity. The revision hashes relationship content, not opening outcomes.

After user confirmation, Tasks calls Launcher `operation:bundles.open`, with the reviewed snapshot, `confirm:true`, `layouts:false`, request ID and member cwd. Launcher fetches `bundles.get` again, validates identity, and owns workspace reuse and per-member outcomes. Tasks validates every returned cwd/state and stores the outcome as evidence of that invocation. Reopening rechecks current workspace presence in Launcher rather than trusting an old success forever.

Transport uses a 30-second timeout, JSON argv/stdin/stdout, and a 1 MiB response limit. A malformed/mismatched/timed-out response is not success. These are explicitly trusted local executable entrypoints. No other owner's database or Python internals are imported. Large bridge output is rejected after capture; owners must obey the documented bound.

## Remaining owner boundaries

Git Sidebar owns local Git operations and native DIFF notes. Tasks retains the exact-conversation follow-up flow. Native search owns workspace/pane/source metadata; Tasks has no custom native result registration API on the inspected server. AI Usage retains its own notification delivery. Native ORCH gates are reported separately from Project Commands validation; Tasks does not create another gate runner.


## Bounded workflow control

`python launcher.py workflow --store /absolute/tasks-config list|inspect|pause|resume|cancel|report [run-id]` returns a version-1 JSON result or error. `report` reads at most 1 MiB of JSON from stdin and requires the approved worker pane and native session. The dispatch prompt supplies the exact run ID, attempt and token:

```json
{"version":1,"attempt":0,"token":"attempt-token","outcomes":[{"id":"provider-feedback-id","outcome":"addressed","reply":"What changed and why"}],"files":["src/example.py"]}
```

Outcomes are `addressed`, `disagree` or `blocked`; each selected item appears exactly once. Files must exactly cover current checkout changes. A report is evidence for the next runner step, not authorization to publish. `workflow_runs` in the Tasks database owns immutable reviewed inputs, state, attempt reports and durable operation intent. The process lock serializes runner transitions and competing handover launches/followups. Restarted helpers pause inherited runs for reconciliation.

Project Commands 0.1.2 adds `action:evidence` with canonical `root`, exact `request_id` and optional `run`. Tasks verifies the same request/run/checkout, current producer freshness and terminal state before consuming its bounded output excerpt. Runner requests supply the reviewed command definition and exact native session. No consumer reads another module's database or logs directly.

GitHub supports review-thread replies/resolution and PR discussion replies. Azure supports PR thread comments and fixed status. Workflow reply markers provide provider readback/deduplication and are excluded from selected-feedback signatures. Unknown mutations remain paused unless their exact recorded effect can be verified.


The evidence response may include `diagnostic_excerpt`. Long `output_excerpt` values contain an explicit middle-omission marker between beginning/end segments. Tasks bounds repair context per failed check and preserves each excerpt's end. Workflow list responses are paginated with `--offset`/`--limit`; default page size is 50. The watcher publishes a session-keyed helper heartbeat used by launch preflight, with a 120-second freshness window. This is recent liveness evidence, not a guarantee against a subsequent crash.
