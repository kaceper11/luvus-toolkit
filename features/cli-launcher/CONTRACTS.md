# Launcher integration contracts — version 1

Updated 2026-09-06: Tasks now provides `launcher.py bundle-api <tasks-config-dir>` compatible with the bundle contract below. Project Commands provides its own `launcher.py api --store <commands-config-dir>` API; launcher-owned `commands_adapter.py` translates it to this contract. Source and live read APIs were checked; both integrations are configured in this installation. No other module's source/configuration was changed.

Settings → Configure discovers installed module roots and config directories through the selected Luvus registry and pre-fills the entrypoint. Test & save checks `bundles.list` or `commands.list` before persisting. Commands list is a read-only adapter extension returning id/name/kind for the exact checkout. A connection check never invokes commands.run/services.ensure. Missing bundles/definitions must be created in their owning module.

The Commands adapter hashes the full normalized definition for revisions and passes that same reviewed definition plus selected socket/generation and durable operation ID to the owner. Owner reservations enforce checkout/definition/session matching and active-service reuse. Unconfirmed starts remain uncertain. URLs come only from the owner's current definition, with a matching ready running record updated within 15 seconds; this adapter does not discover arbitrary ports or scrape logs.

## Transport and ownership

In **Project… → Settings** (or `i integrations` in plain mode), configure `tasks` and/or `commands` as a JSON
argv array with an absolute executable path, for example
`["/absolute/python", "/absolute/owner-entrypoint.py", "--launcher-api"]`.
This example is a contract illustration, not an existing command to run.
Entrypoints are explicitly trusted user configuration. No shell interpolation,
automatic discovery, installation or fallback executable is used.

Each invocation receives one JSON request on stdin and returns one JSON response
on stdout, with a 30-second response timeout. Mutating operations should return
promptly after starting/reusing an owner-managed run, not wait for a long test.
Diagnostic output belongs on stderr. Version 1 documents are limited to 1 MiB.

```json
{"version":1,"request_id":"unique-request","operation":"bundles.list","cwd":"/exact/checkout"}
```

```json
{"version":1,"request_id":"unique-request","cwd":"/exact/checkout","result":[]}
```

The response must echo the exact version, request ID and canonical cwd. An error
response replaces `result` with `error`. A nonzero exit status, timeout, malformed
JSON, or mismatched identity is a failure, never success. Owners must validate the
target and configuration revision at the point of mutation. A request ID is for
correlation; **operation_id** is the durable idempotency key for a launch step.

The launcher retains the caller's Luvus binary/socket environment. Owner
entrypoints must use their own configuration and must not assume that
`LUVUS_MODULE_CONFIG_DIR` belongs to their module.

## Tasks → bundle snapshots

Read-only operations:

- `bundles.list`: return an array of full bundle snapshots available for `cwd`.
- `bundles.get`, with `id`: return the current snapshot. Launcher compares this
  with the reviewed snapshot before opening anything.

```json
{
  "id":"backend", "revision":"7", "name":"Backend",
  "members":[
    {"cwd":"/projects/api", "branch":{"kind":"branch","value":"main"}, "arrangement":"dev"},
    {"cwd":"/worktrees/client", "branch":{"kind":"detached","value":"full-commit-id"}}
  ]
}
```

Paths must be unique, absolute checkout roots (maximum 32 members). Non-Git
folders use `{"kind":"none","value":""}`. Tasks owns relationships, ordering,
branch expectations and task metadata. Launcher checks actual branches without
checking out, creating a worktree, fetching, or updating task records.

Opening defaults to workspaces only. A member's optional `arrangement` is a
launcher arrangement ID, never a command. Applying arrangements is opt-in and
requires a separately reviewed arrangement revision for each target checkout.
Unavailable paths/branch mismatches fail only that member. Reopening reconciles
exact existing workspace paths and does not repeat successful opens.

## Project Commands → command/service operations

- `commands.describe`, with `id`: read-only result contains `id`, string
  `revision`, and `kind` (`command` or `service`). The arrangement pins these
  fields. The owner may return additional descriptive fields.
- `commands.run` / `services.ensure`: receive `id`, `revision`, and
  `operation_id`. Return `{"state":"running","run_id":"owner-run-id"}`;
  `succeeded` and `reused` are also accepted. This confirms dispatch/reuse,
  **not that asynchronous tests passed**.
- For a known failed attempt return `state:"failed"`, `dispatch:"not_started"`
  or `dispatch:"executed"`, and a run ID when execution occurred. The launcher
  records the failure; explicit Recover retries only failed steps with a new
  attempt key. An uncertain response is never replayed automatically.
- `services.urls`, with `id`: read-only result
  `{"state":"running","fresh":true,"urls":["http://localhost:3210"]}`.
  Return only the actual selected worktree's current, preferred URL. Missing,
  stale or multiple URLs require selection/recovery in Project Commands.

The owner must deduplicate repeated operation IDs and make `services.ensure`
reuse a matching managed service **across operation IDs** by checkout and
service/configuration identity. Services in different worktrees remain distinct.
The launcher never starts a service merely to obtain/open its URL, never stores
dynamic URLs, and never owns stop/restart/log/health behavior.

## Calling the launcher

Run `python3 project_launcher.py --config /absolute/module-config/projects.json`
(`python` on Windows) with a JSON request on stdin. It requires the selected
session's `LUVUS_BIN_PATH` and socket environment and explicit `confirm:true`.

`operation:"arrangements.open"` requires `cwd`, `id`, and `revision` (SHA-256
of the effective arrangement JSON using Python's `json.dumps(value,
sort_keys=True, ensure_ascii=False).encode()`; `project_launcher.digest` defines
the exact encoding). `operation:"bundles.open"` requires `cwd` and the reviewed
`bundle` snapshot. Optional `layouts:true` additionally requires
`arrangement_revisions`, keyed by canonical checkout path with arrangement hashes.
Tasks' configured `bundles.get` is still consulted; supplied JSON is not a second
relationship registry.

Replies include the version, request ID and result. Bundle results contain one
outcome per member; callers must inspect those outcomes even when the process
returns zero. No operation implicitly approves an agent or publishes data.
