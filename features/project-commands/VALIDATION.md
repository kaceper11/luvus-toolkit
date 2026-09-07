> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Validation record — 2026-09-06

## Executed locally

- Python 3.11.16, Textual 6.12.0 and psutil 7.2.2 in this module's own `.venv`.
- macOS unit/process/UI suite: `python -m unittest discover -s tests -q` — 41 checks passed after the review fixes.
- Isolated Luvus 0.13.4 smoke: manifest registration, actual console rendering, native terminal identity, a harmless service's captured output, repeated-start reuse, panel-independent lifetime and cancellation — passed. Temporary server/home stopped and removed afterward.
- Python source compilation and Python 3.11 TOML parsing — passed. Native Luvus validated the manifest during the isolated link.
- Novulum discovery read-only check: Bookulum produced 41 suggestions, including both guarded Aspire launch paths and .NET TRX, ESLint JSON and Vitest JSON report commands. No suggestion was saved or executed in Bookulum.
- Registered `personal.project-commands` 0.1.0 at `/path/to/original-module` in the selected production Luvus session after isolated validation. Registration returned module ID and revision 55853. No production command/service was launched.

The tests exercise successful/nonzero exits; source edits and untracked inputs; unknown inputs; spawn failure; cancellation and unrelated-process survival; forced POSIX group stop; supervisor-crash ownership-pipe cleanup; duplicate/conflicting reservations; uncertain launch replies; changed reviewed definitions and sessions; stale/foreign run targets; malformed and unsupported reports; source-path boundaries; bounded collectors; container identity/owner guards and retained failure output; retention; Windows shim argument transport; narrow UI, cancelled previews, no automatic launch, and stable selection during refresh.

## Platform and integration limits

Native Windows process execution/Job Objects and Linux/WSL execution have **not** been run here. Windows shim transport has a fixture check on macOS; it is not a Windows execution claim. The CI matrix is provided but has not been published or run remotely. A multi-platform release still requires those platform runs and an isolated Luvus smoke on each target.

Real Novulum builds/tests/setup, SDK installation, credential changes, Aspire startup, actual Docker start/stop, browser navigation, production agent messaging and mouse interaction were not exercised. Container identity/error behavior is tested with fixtures; the real daemon/provider integrations require explicit user operations in suitable projects.

The review follow-up changed only the Tasks command handoff and its focused contract test in the Tasks module: reviewed definition/session submission and exact request reconciliation. Launcher’s owner supplied its adapter independently. Both actual consumer adapters passed the isolated native end-to-end check. Production integration settings were not changed. Owner-specific credential health/recovery adapters still require owner-provided endpoints; missing adapters remain unavailable. No Git/tracker writes or coding-agent launches were made. Git commits in tests were confined to disposable fixture repositories.

## Evidence provenance

Planning used exact module graphs and direct reads where Launcher metadata was stale, plus live Luvus capability/schema/process reads. The new module was indexed separately with `persistence=false`; no Codebase Memory artifacts were persisted in a repository. Coverage is best effort, not proof of exhaustive correctness.

## Review follow-up — 0.1.1

- Durable aliases prevent a repeated Start request from executing again after the reused run ends. Missing worktree configuration no longer blocks other saves.
- Vitest suite/import failures and missing aggregate diagnostics remain visible. Text and structured problems share the 5,000-entry limit. Unparsed failure output is retained as partial evidence.
- npm subdirectory scripts execute in the correct cwd. Health probes only relevant toolchains and resolves arbitrary configured executables without executing them.
- Searchable discovery checklist, individual command forms, protected reload/close, selected-result freshness checks, asynchronous native lookups, state-aware actions/countdown, duplicate binding prevention and Unbind are covered by headless checks at 80×24.
- One reviewed Restart stops and confirms exit before replacement. A timeout or unknown exit starts nothing. Real owned-process and isolated native checks passed.
- Isolated smoke passed through the actual Tasks bridge and Launcher adapter, using disposable Python commands and a temporary Luvus home. It also confirmed an alias from a completed run cannot create a third service.
- The focused Tasks bridge contract check passed. A broader 32-check Tasks workflow run had one failure in the separately owned PR-sidebar menu expectation (`Address PR comments`); this does not exercise the changed command bridge. That feature was not changed here.
- The configured Docker context is `orbstack`; its socket was unavailable. No Docker daemon was started and no production container was changed. No native Windows or Linux execution environment was available for this validation. The CI matrix remains the runnable platform check, not evidence of a completed remote run.

- Refreshed only the existing Project Commands registration to source/package version 0.1.1 (revision 75503). Luvus 0.13.4 rejects linking an already registered ID, so the registration was unlinked/relinked at its verified original root, preserving enabled state. No running service terminal was stopped.


## Bounded evidence API — 0.1.2

- Local macOS full suite: 42 tests passed. The new evidence check verifies exact request/checkout binding, bounded output and existing credential redaction.
- Tasks consumes the owner API for bounded repair diagnostics, without reading command state/log files directly.
- Repositories are now published privately. The first baseline CI run (34052106424) exposed a Linux discovery-form UI test failure, before this API addition. Earlier statements that CI had not run describe the prior local-only installation; remote cross-platform validation is not wholly green.


## Fresh-review fixes — 0.1.3

All 42 local macOS tests passed after changing evidence excerpts to preserve log beginning/end and adding bounded redacted error/problem context. The focused evidence regression checks long-log failure visibility and credential redaction. No live provider writes were needed. Existing Windows test failures are not addressed by this API change.
