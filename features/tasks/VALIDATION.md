> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Validation record

## Remaining-gap follow-up — 2026-09-06

Full suite: 131 tests passed in 36.731 seconds. Final draft-history regression: 40 workflow tests passed in 6.943 seconds. Added GraphQL pagination/resolution/reply/error checks, explicit sandbox-profile rejection checks, null-draft handling, watcher duplicate-start/disable/reacquire checks, and bounded 250-draft sidebar coverage. Real macOS write-denial probe now passes; no reviewer was launched. Manifest parsing and compilation passed. Refreshed the linked Tasks registration and verified the selected session owns its watcher OS lock. Removed only the authorized haha connection/cache; history counts were unchanged. Bookulum already selects github; its provider refresh currently reports HTTP 404. See UX-REVIEW.md for current limitations; older failed-probe/event-only observations below are historical.

## Tasks UI/UX review and readability update — 2026-09-06

127 unittest/Textual tests passed in 34.975 seconds. A second agent performed a bounded read-only source/UI review and follow-up. Fixed empty-state recovery, duplicate/noisy sidebar cards, priority ordering, irrelevant actions, surprising resume behavior, cached/offline inspection and attention acknowledgment clarity. Final targeted workflow validation covers the last repository-label refinement. See UX-REVIEW.md for findings, checks and remaining gaps.

The live sidebar refresh succeeded (log 982, code 0, empty stderr); the resulting dock projection was inspected. The open console was not forcibly restarted, so its tab/layout changes apply when reopened. No tracker/PR write, agent prompt, credential/configuration change or Git write occurred. An in-progress string-escaping error was caught and fixed before final validation.


## Live refresh confirmation — 2026-09-06

On the user's continuation request, the inherited session already reported Tasks 0.5.0 enabled/runnable from this checkout, with no registration warning. No relink was needed. `module run personal.luvus-tasks refresh` completed successfully (log 206, exit 0, empty stderr). The existing Tasks pane showed Attention and Evidence / PR; the successful dock publication record contained the PR/CI section and all six PR/CI shortcuts.

Live configuration still requires user selection: the Bookulum handover matches multiple authorized PR connections, so discovery correctly reports ambiguity instead of choosing one. The legacy `gh` issue connection also remains invalid and has not been repaired automatically. No agent message, tracker/PR mutation, module setting edit, or pane restart was performed. This live check supersedes earlier source-only rollout notes; it does not establish successful PR/CI provider reads for ambiguous/unconfigured handovers.


## Automatic PR/CI sidebar follow-up — 2026-09-06

- Full unittest/Textual suite: **122 tests passed** in 32.045 seconds. Final targeted workflow checks passed after the sidebar row/bounded-queue refinements. Source/test AST and manifest parsing passed; tracked diff whitespace check passed.
- Added coverage for automatic unique PR selection without rewriting handovers, ambiguous PR refusal, branch CI with no PR, duplicate-handover request coalescing, polling backoff, revision-aware sidebar status, exact handover shortcut routing without resume, and cancellation of filtered failure feedback without sending.
- Automatic checking uses the existing console timer and native dock event entrypoint. It does not install a daemon. Reads cover up to four groups per pass, throttled for 60 seconds on success / five minutes on failure. Closed-console updates require native events. These are bounds on groups and cadence, not a wall-clock guarantee for slow providers.
- Sidebar status and shortcut flows were fixture/headless tested; no production reload, live provider calls/writes, configuration change or agent message occurred. The source remains part of the unrolled-out 0.5.0 increment.


## 0.5.0 grouped Tasks / attention source increment — 2026-09-06

- Final `.venv/bin/python -m unittest discover -s tests -q`: **115 tests passed** in 30.764 seconds on macOS. Includes existing provider/handover/context/recovery coverage plus revision freshness, evidence ordering and replay, notification deduplication, stale navigation, reviewed PR cancellation/publication uncertainty/readback, Project Commands API identity, setup default/cancellation/failure, frozen review packets, artifact retention, alternatives, bundle read-only export, and narrow Evidence/Attention navigation.
- Python AST and TOML parsing passed. Source manifest/project versions are 0.5.0. `git diff --check` passed for tracked changes; newly added Python files were parsed/tested separately. No dependencies were added or installed.
- The current CLI Launcher's actual `validate_bundle` accepted a Tasks-generated snapshot in a pure local check. No workspace/native command was called. Project Commands version-1 wire shape was verified from exact owner source and tested using controlled responses; no setup/service/test command was dispatched to the live module.
- Read-only native inspection found Luvus 0.13.4 and live Tasks registration 0.4.0. Relevant attention/search/lifecycle methods were advertised, but native automation and custom search-result registration were not available. Source registration was not reloaded or installed.
- An actual Codex sandbox write-denial probe failed. The independent-review action therefore fails closed; no reviewer/coding agent was launched. Packet freezing and unsupported-capability launch blocking passed fixture checks. Supported-host reviewer execution remains unverified.
- One intermediate full run hit a transient Textual narrow-editor test error: `NoMatches` while a CommandPalette was unexpectedly active. The same 10-test handover UX file passed on targeted rerun, and the final 115-test suite passed. The trigger is not established; do not treat this as a proven fixed application bug. The historical target-cancellation regression passed.
- Codebase Memory coverage was checked for all implementation/test evidence paths with current metadata and no recorded source gaps. Excluded Python caches were not treated as code evidence. Concurrently added Launcher contract/source files were absent from its old graph and were read directly. No repository-persistent index was created.

### Rollout and live acceptance still required

No production-mutating smoke test, module reload/install, Luvus upgrade, Git commit/push/merge, tracker/PR write, agent launch or production integration setting change was performed. GitHub/Azure PR/check/review/log APIs use controlled responses here; authenticated end-to-end reads/writes and permissions/retention behavior need designated test resources. The new UI was checked headlessly, not in the production desktop. Windows/Linux/WSL and supported sandbox platforms were not exercised.

Configure both Tasks/Launcher bridge ends and exact-checkout Project Commands definitions before live use. New worktrees do not inherit setup definitions automatically. Source-changing setup may need producer reconciliation before the strict freshness gate permits launch. GitHub fork PRs, GitHub Enterprise and non-Azure-Repos Azure pipelines are outside the current adapters. Provider ref revalidation is best effort, not atomic compare-and-swap. Unknown-revision policies are not successful validation. Saved log excerpts are bounded, while GitHub's temporary full log download is time-bounded rather than byte-bounded.

Fingerprinting covers observed source (including untracked files) up to 64 MiB, not ignored dependencies or external environment, and cannot be an atomic snapshot against arbitrary concurrent external edits. Timeline stores meaningful observations with no automatic retention purge. Native ORCH/gate reports remain distinct from revision-proven execution. These limitations are exposed in README.md and the owned requirements; no requested feature was silently represented as live-validated.


## 0.4.0 handover and dashboard update — 2026-09-06

- 86 unittest/Textual checks passed, including the persistent editor, narrow-width mouse navigation, custom/legacy prompt preservation, context repair, save-failure launch blocking, workspace continuation, worktree-scoped dashboard rows, and per-platform menu uniqueness. Existing provider and launch-recovery tests remain in the suite.
- The isolated Luvus smoke test passed with the 0.4.0 manifest: console rendering/reuse, native dock callbacks, dedicated shell identity, and ORCH claim/lease/release. No coding agent, tracker mutation, or quality gate was started.
- Headless Target, Context, and Prompt screenshots were rendered and inspected. Optional workspace/branch choices are collapsible, editing remains in one tab, and the review footer stays visible. Host-specific clipboard behavior and every terminal mouse gesture are not exhaustively tested.
- Installed the editable 0.4.0 package, refreshed only the production module registration, repainted its native dock, and opened the updated Tasks tab in pane 67. Runtime readback confirmed a runnable 0.4.0 registration and rendered issue list. All 12 previously recorded terminals and all 3 previously recorded coding agents were preserved.
- Agent identity now joins the live agent list with terminal inventory and checks server generation, terminal/pane identity, and checkout. Reviewed sends also validate the branch. No live agent handover/follow-up was sent as part of verification.
- No provider credentials, repository mappings, tracker status, or unrelated module configuration was changed. Old Tasks panes remain open intentionally; use the newly opened tab for the new editor.

## GitHub open-issue default — 2026-09-06

- 78 unittest/Textual checks and the isolated Luvus smoke test passed. Tests cover the open/unassigned default, unchanged explicit closed/personal queries, legacy label normalization, and isolation from the old blank-query cache even during network failure.
- Read-only live verification returned 97 open issues across the configured connection's six selected repositories (33 Bookulum, 30 dotnet, 15 email, 11 user, 8 billing, 0 UI reference). No issue/status/comment writes were performed.
- Existing GitHub default labels normalize on config read; non-empty custom filters, repository scope, credentials and history are unchanged. Jira/Azure defaults are unchanged. The linked editable installation picks up the fix in newly opened Tasks tabs.

## 0.3.0 usability update — 2026-09-06

- 75 unittest/Textual checks passed, plus package/launcher/test bytecode compilation.
- New tests cover same-number GitHub issues in multiple repositories, exact comment destinations and out-of-scope rejection, per-repository partial failures/cache preservation, explicit lookup expansion, account-change rejection, unborn checkout errors, branch placeholders, no implicit module checkout, pinned commits/custom worktree parents, context preservation, repository checklist filtering/refresh, inline configuration cancellation, independent checkbox clicks/text selection, clipboard copying, connection removal preserving history, and captured native-pane selections.
- Headless 140×44 UI screenshot rendered and visually inspected. Corrected the settings navigation grid; prompt input has solid borders and Configuration stays inline. The actual user's terminal rendering/clipboard combinations are not exhaustively verified.
- Disposable Luvus smoke test passed for the 0.3.0 manifest with 22 actions: pane rendering, sidebar callbacks, console reuse, dedicated shell, ORCH claim/lease/release. No agent or quality gate launched; temporary server stopped.
- Local package upgraded and production module registry refreshed to 0.3.0. New Tasks pane 70 rendered with no traceback. All 12 pre-existing terminals and 4 agents remained present. No provider connection settings or credentials were changed. The pre-existing invalid `gh` connection remains an explicit repair warning.
- No live tracker mutations or actual agent handovers were performed. Azure remains REST/PAT and ORCH behavior is unchanged. No new dependencies or hosted services were added.

The following records describe the earlier 0.2 validation, not additional live checks of 0.3.

Run on 2026-09-05, macOS arm64, Python 3.11.16, Luvus 0.13.4, gh 2.98.0.

## Passed locally

- 64 standard-library unit/integration and Textual interaction tests: `python -m unittest discover -s tests -q`.
- Cockpit mouse navigation, 80×24 layout, search, pinning, batch drafts, unsaved-configuration cancellation, target autosave, complete four-step wizard with preset changes and final launch confirmation. Native UI rendering is covered by the isolated smoke test; pixel-perfect appearance and all host mouse gestures have not been manually verified.
- Session-scoped inbox consumption, terminal-generation guards, exact-console reuse, native/standalone state-directory consistency, uncertain status/comment/ORCH writes, gated-task ownership, explicit path leases, and wrong-checkout DIFF rejection.
- Production rollout: locally linked module registration refreshed to 0.2.0, 20 platform-specific actions accepted without warnings, and the cockpit rendered in pane 50. Only the temporary verification pane created during this rollout was replaced; pre-existing panes/agents and provider configuration were preserved. The existing `gh` connection has an invalid repository value and is surfaced as a non-blocking configuration warning.
- Python bytecode compilation for package, launcher, and tests.
- Read-only Luvus capability discovery. Codex and Muse executables are available on this machine; required agent methods and module/terminal interfaces are advertised.
- Disposable Luvus server smoke test: 0.2 manifest accepted without warnings, Textual pane rendered, sidebar/status bar registered, startup/event callbacks succeeded, repeated Open reused the exact console, and a dedicated handover shell had matching cwd/terminal identity. Native task claim, path-lease acquisition, and release succeeded in disposable state; no gate or agent was executed. The temporary server was stopped after the test.
- GitHub read-only provider check through authenticated gh, against the public `RizRiyz/luvus` repository: search returned 13 open issues; detail, status-action discovery, comments, and related-issue reads completed successfully.
- Temporary Git repository tests cover new/existing worktrees, preserving dirty changes, matching terminal identity, startup recovery, duplicate launch prevention, and uncertain prompt outcomes. GitHub uses the same tested handover implementation as Jira/Azure.

## Not yet exercised live

- Jira/Azure authentication, real custom workflow transitions, and provider comments: no designated test credentials/tickets were supplied.
- Remote GitHub mutations: deliberately not performed on public issues. Their request construction, stale guards, and shared reviewed write-back behavior are tested with controlled responses.
- Actual coding-agent startup and prompt consumption, native conversation restore, or image interpretation. Tests use controlled Luvus responses; the smoke test does not launch a real coding agent. Claude Code and Copilot executables are not available on this workstation.
- Native Windows, Linux, and WSL execution. A three-OS CI workflow is included but has not run on a remote repository.

## Operational limits

- Images use an explicitly accepted local-path fallback because the generic Luvus prompt interface does not advertise image payloads.
- GitHub search rejects incomplete results and queries above the service's 1,000-result cap. GitHub issue state is supported, not Projects board fields.
- GitHub status stale checking is best effort; unlike Azure revision tests it is not an atomic compare-and-swap.
- Session resume requires an exact known native session. Unknown or ambiguous outcomes require inspection instead of guessing or automatically resending.
- Windows privacy relies on the current user's directory ACLs. Agent execution shares the user's account; environment scrubbing is not a security sandbox.

See README.md for the live setup and smoke-test commands. No provider tokens are included in this repository, and no remote repository has been created or published.

## Find work and worktree navigation fixes — 2026-09-06

- Find work trims queries, matches repository/checkout paths, and keeps connection/project-qualified issue identities. Same-number issues from different projects are no longer hidden by an unrelated handover. It includes cached issues outside the currently loaded list and opens issue results inside Tasks.
- Native search defaults to workspace/tab/agent navigation; file/output search is an explicit slower option. Partial/unavailable native results are labelled. Missing/replaced pane identities cannot activate. Opening stored results clears conflicting dashboard/history filters.
- Handover in this worktree rejects missing paths, invalid checkouts, detached HEADs and changed workspace/branch targets. Saved existing-worktree drafts and stopped handovers remain discoverable without creating another draft. Agents in checkout subdirectories count as occupying that checkout.
- Candidate issues come from cached/current issues and saved handovers, with repository matching for every provider. The old unrelated Jira/Azure fallback was removed. Missing mappings/cache get an actionable configuration/refresh message. Provider identity and repository mapping are rechecked before creating a draft.
- A rejected/failed console opening no longer leaves a newly queued action that unexpectedly appears later. Existing queues were not deleted.

Validation: full unittest suite passed **138 tests** in 40.953 seconds, including six isolated navigation regressions. A read-only native `search.query` with scope `navigate` succeeded and reported partial results correctly. No real agent launch, resume, prompt send, tracker write, repository mapping change or production-mutating smoke test was performed. Close the existing Tasks console and reopen Open Tasks to load the updated console code; no existing pane was forcibly closed.


## Draft and navigation reliability — 2026-09-06

- Draft editors open from saved data immediately; agent/workspace/branch discovery runs afterward without changing the active tab. Opening another draft retains the previous editor buffer. Incomplete drafts autosave; failed saves remain visible and do not block navigation. Save and Back to handovers are explicit actions.
- Tab changes clear stale focus before focusing the destination. Sidebar shortcuts remain responsive during provider work, and the latest pending navigation wins. Late operations retain their original issue/handover identity and cannot reopen an abandoned form or editor. Each newly opened console has its own shortcut inbox.
- Find work displays local matches before native search completes. Tables retain scroll and selection across unchanged refreshes; filtering out a selected row clears its action target. Checked issues survive filters with visible/hidden counts and explicit clearing. Copy confirmation is nonmodal.
- Interrupted text forms retain their input for the current console session. Exit requires explicit discard if a save failed or interrupted form text remains. These temporary form buffers are not crash-persistent. Saved unavailable agent choices remain visible. Drafts without a checkout render and open safely.

Validation includes isolated Textual interaction tests for delayed discovery, tab focus, failed-save draft switching, busy shortcuts, progressive search, action identity, long-list scrolling, hidden selections, interrupted forms and independent console inboxes. Desktop mouse behavior and Windows/Linux behavior remain unverified; no real agent launch or tracker write is part of these checks.

Final verification: the current full suite passed **160 tests** in 51.177 seconds. Python compilation and `git diff --check` passed. Opened the updated Tasks console in native pane 21 and read back its rendered issue list, selection counts, tabs and ready status; existing panes were preserved. The legacy `gh` connection still displays its explicit repository-scope repair message. Full desktop interaction remains unverified.


## Productivity stages — 2026-09-06

Implementation adds background/progressive refresh, shared Start / Continue, reversible archive, keyboard commands, context packs, phase continuation, batch edits, manual Up next, grouped/source-filtered/snoozed attention, and local captures with reviewed provider publication. No agent was prompted and no real tracker issue was created by validation.

- Final full unittest/Textual suite: **170 tests passed in 52.509 seconds**. Compilation, TOML parsing, and tracked/new-file whitespace checks passed.
- New isolated productivity suite: 24 tests passed as part of that run. Covers scope-qualified identity, atomic queue updates, archive and follow-up exclusion, polling exclusion, pack copy/reference failures, phase-owned attachments, snooze signatures/expiry/urgent conditions, capture state and timeout/readback recovery, required fields, GitHub/Jira/Azure payloads, hidden batch selections, cancellation and custom prompts, grouped attention, text-entry shortcuts, failed autosave retention, and Jira project-scoped refresh with preserved cache.
- Disposable Luvus smoke passed: manifest registered both native capture actions, console and sidebar rendered, terminal identity and ORCH claim/lease/release checked in temporary state. No coding agent launched or native quality gate executed.
- Controlled benchmark: 1,000 issues, 100 handovers sharing a 140,000-byte temporary Git checkout, five samples each. Reconstructed previous search path median **3699.39 ms**; cached search path median **2.12 ms**. Both use the current renderer; the baseline restores synchronous per-handover attention hashing. This is a controlled path comparison, not a released-build or end-to-end desktop benchmark.
- Live GitHub/Jira/Azure creation, organization-specific custom-field workflows, native Windows/WSL interaction, and real agent phase delivery remain unverified. Unsupported required custom/multi-value fields use browser completion and explicit linking.
- Production registration and running console processes were preserved. Reopen Tasks to load Python code; relink the manifest for the new native capture action.


Busy-guard follow-up (2026-09-06): full current suite passed **171 tests** in 55.144 seconds; compilation and diff whitespace checks passed. Added an isolated test proving Evidence, saved-draft Start / Continue, Next attention and Copy work while a separate action remains busy. Native pane 23 opened and rendered Ready. Existing panes were preserved; no agent or tracker write was exercised.


Empty-base follow-up (2026-09-06): **173 tests passed** in 56.742 seconds. Real temporary Git checkout regression covers missing origin/HEAD, ambiguous main/master, configured base, remote default, and blank-base rejection without worktree creation. Textual regression covers restoring an unset saved base while preserving a manually cleared field. Compilation and diff whitespace checks passed. Updated native console pane 25 rendered Ready; no production task was launched.

## ORCH coordination and checkout awareness — 0.7.0

- Full Tasks suite: 188 tests passed in 64.266 seconds. The new coordination tests cover claim-before-prompt, conflict/retry reuse, display failure independence, branch drift, account-bound repository defaults, review startup races, and wrapper claim-before-exec with control credentials stripped.
- Disposable installed-Luvus smoke passed, including native ORCH dock publication/right-sidebar reveal, claim/lease/release, manifest registration, console rendering and repeat-open reuse. No real coding agent, tracker write, commit or quality gate was executed.
- Production module registration updated to 0.7.0. The open Tasks console was saved/closed and reopened; ORCH remained enabled and its right sidebar was revealed.
- Actual agent-driven implementation/review outcomes, Windows/Linux native behavior and real provider repair writes remain unverified. Unit tests use synthetic reviewer execution; sandbox capability probing remains mandatory at real launch.

## Direct Tasks dashboard — 2026-09-06

Supersedes the Tasks popup sections above. Tasks now opens the issue dashboard directly; the temporary picker and its two choices are removed. Existing console identity, clicked repository context and drafts are retained. Plain Open is navigation even while busy, and existing running consoles receive the compatible dashboard navigation payload.

Validation: the full suite passed 184 tests before the final narrow navigation guard; three focused direct-open tests passed afterward. The disposable native smoke exercised the actual hub action with no console, repeated activation, Configuration-to-Issues navigation, and focus from another workspace. Live module refresh retained all 14 existing terminal identities and the server generation; the actual Tasks action revealed Issues in pane 45 and repeated activation reused it. No agents were launched or prompted. Desktop mouse interaction and Windows/Linux native behavior remain unverified.

2026-09-06 tab names: all 187 Tasks tests passed. Naming failures are isolated from launch orchestration; the shared naming logic also passed exact-tab, replaced-terminal, workspace-switch and manual-name checks, with native background naming verified in the Send module smoke. Native Windows was not exercised.


## Grouped handovers and compact PR sidebar — 2026-09-06

- Full unittest/Textual suite passed 211 tests. Subsequent focused checks passed: 24 grouped-handover/provider/navigation tests, 10 handover UX tests and 40 workflow tests. These cover grouped prompt membership, one launch, frozen membership, repository validation, exact PR references, multiple links, ignore/manual restore, stale snapshots, unpublished branches, revision-specific CI, direct issue navigation and keyboard selection.
- Disposable native Luvus smoke passed: console rendering and reuse, cross-workspace navigation, dock registration, absence of the duplicate ORCH dock, and native task claim/lease/release. No coding agent or quality gate was launched.
- Live GitHub account and repository reads succeeded; the selected repositories returned 98 issues. An unpublished branch now reports that state rather than a generic authentication error. The current sidebar also displayed a discovered draft PR with passing CI. Automatic links remain local; issue-reference discovery is bounded to the newest 500 PRs, with a visible notice and manual fallback.
- Retired the older watcher, removed the obsolete ORCH dock through module registration, and gracefully reopened the console after its draft flush. Verified the exact requested issue and its description in the native console. Current work and PR/CI precede open issues. Successful issue refreshes clear obsolete connection warnings; missing local checkouts remain in handover details.
- Compilation and diff whitespace checks passed. Changed source was read directly where graph freshness lagged. Azure PR APIs, real agent launch, remote PR/tracker writes and Windows were not exercised live.

Final live readback: three successive sidebar samples kept Current work / PR / CI above Open issues, with no old checkout or connection warnings. The native Tasks dock was present alongside Files and no ORCH duplicate; a draft PR showed CI passed and its feedback count. Startup now invalidates its saved paint cache so disable/enable republishes the dock.


## Azure DevOps / Jira compatibility review — 2026-09-06

Review found and fixed Jira search omitting description/acceptance material; GitHub-only project filters rejecting Azure/Jira sidebar project links; automatic discovery missing PR title references and Azure native work-item PR relations; and Azure unpublished branches appearing as read failures. Azure AB# text references are scoped to their organization. Native artifact links must match both the code project and repository IDs. Legacy visualstudio.com Azure Repos origins are recognized alongside dev.azure.com and SSH origins.

Supported combinations: Jira Cloud or Azure Boards tickets with a mapped GitHub.com or Azure Repos checkout, including mixed Jira/Azure groups mapped to one code repository. GitHub Issues remain restricted to their own repository. Code authentication is a separately authorized forge connection; Jira does not provide Git hosting. Azure Repos/Pipelines CI retains head/merge revision distinctions. Missing Work Items read permission leaves an explicit partial-discovery notice and retains branch/title/manual-link paths.

Full suite: 217 tests passed in 70.066 seconds. Six new cross-provider tests cover retained Jira fields, project navigation for both trackers, mixed grouped handover preparation and Azure forge selection, Azure native PR relations and wrong-repository rejection, Jira references in Azure PR titles, cross-organization AB# isolation, unpublished branch state, merge CI and Azure remote URL formats. Changed source was inspected directly where graph freshness lagged.

Only GitHub is configured locally. Azure/Jira tests use controlled provider responses and real temporary Git repositories; live authentication, tenant permissions and remote writes remain unverified. Jira Data Center and Azure DevOps Server are outside the implemented provider scope.


## Bounded workflows — 0.8.0

- Local macOS full suite: 235 tests passed, including 18 bounded-run tests with real disposable Git repositories. Coverage includes explicit reports, exact inputs, limits, ownership, stale validation, repair, commit/push and lost-acknowledgement reconciliation.
- Isolated native Luvus smoke passed for the changed module (temporary home, console/dock and existing native contracts). No coding agent was launched.
- `tests/smoke_workflow_github.py --repo kaceper11/luvus-tasks --confirm-test-writes` passed against the private test repository. It created a draft PR, exercised feedback/reply/resolution readback, then closed PR #1 and deleted its branch.
- Azure feedback/reply/resolution adapters passed controlled-response tests. Live Azure authentication/writes and an actual agent completing the report protocol remain unverified.
- All six module repositories were published privately under kaceper11. Baseline CI passed Tasks Linux/macOS but failed existing Windows UI/navigation tests (run 34052744493); this was before the bounded-workflow changes. No Windows compatibility claim follows from local tests.
- Production console/helper reload was not performed: this shell has no inherited Luvus binary/socket/pane identity. Reload the intended module session to activate the new helper code.


## Fresh-review fixes — 0.8.1

- All 239 local macOS tests passed, including expiry during publication, idle remote-read suppression, bounded terminal history with active/paused preservation, workflow Attention, launch preflight and readable approval checks.
- Isolated native Luvus smoke passed with temporary state; production helpers were not reloaded and no real agent/provider writes were performed.
- Repair diagnostics preserve the ends of long output through the final prompt budget. The four review regression tests use disposable Git/SQLite state.
- Previous Windows CI failures and live Azure/real-agent validation remain outside these fixes; updated CI results must be assessed separately.
