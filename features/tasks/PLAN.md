> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Luvus Tasks implementation plan

Accepted scope, including the subsequent GitHub Issues addition.

## Accepted usability expansion (0.3)

Use existing gh authentication; explicitly select multiple repositories under one user/organization with searchable checkboxes and per-repository filtering/cache isolation. Preserve legacy identity/write journals, reject out-of-scope operations, require invalid-scope repair and account-switch review. Keep Azure REST/PAT and existing optional ORCH semantics.

Inline Configuration editors replace modal chains. Solid input borders, selectable previews, clipboard actions and a native clicked-pane selection action improve interaction without modifying Luvus core. Distinguish row highlight from batch checkboxes. Confirm connection removal with optional separate credential deletion; preserve histories/worktrees/context. Add preset deletion and instruction reset/inheritance controls.

Choose a local checkout from live workspace roots or manually; show remote-match hints. Global/per-repository branch templates support type/key/number/repo/slug and an optional worktree parent. Preview/validate target refs, handle unborn repositories, preserve drafts/context on Back, and pin the reviewed commit. Do not fetch, switch branches, launch agents or write trackers implicitly.

Additional future workflow ideas: reusable context bundles, duplicate-draft suggestions and issue changes since a previous handover. These are deferred, not silently implemented.

## Accepted cockpit expansion (0.2)

Replace the default numbered menu with a full-tab Textual cockpit: Issues, Handovers, Configuration, and opt-in Orchestration. Reuse provider and launch operations. Add mouse/keyboard forms, searchable and sortable issue lists, saved views, favorites/recent issues, native context actions, batch drafts, session-scoped console routing, draft recovery, context pickers, editable prompt previews, and explicit confirmation of effects.

Keep direct handover as default and Azure DevOps REST/PAT unchanged. ORCH support creates/links tracking tasks, exposes native dependencies/leases/output, claims verified workers, and allows reviewed completion/release. No native automatic agent briefing, commit, merge, push, or scheduling. Reuse native DIFF for review and selected feedback. Reconcile uncertain actions instead of blind retry. Preserve existing histories and live panes.

Host constraints: inside-console right clicks use equivalent clickable action menus; native ORCH board opening is a documented shortcut rather than a nonexistent UHP method. Drag/drop images, scheduled work, automatic reviewer pipelines, and native branch/merge management remain deferred. Keep the legacy menu only as an explicit fallback.

## Repository and module

Create an independent Git repository at `/path/to/original-module`. Implement a Python 3.11+ Luvus module with a sidebar and portable keyboard-driven task pane. Support macOS, Linux, WSL, and native Windows through platform-specific manifest entrypoints. Use stdlib HTTP/JSON/subprocess/SQLite and keyring; no hosted service or Luvus core fork. Remote repository creation/publication and production-session installation remain separate rollout actions.

## Providers and configuration

- Jira Cloud: API token, site/email, optional scoped-token cloud ID, configurable acceptance-criteria field, assigned-to-me and saved JQL queries.
- Azure DevOps Services: organization/project, PAT, assigned-to-me and saved flat WIQL queries.
- GitHub.com Issues: `owner/repository`, existing authenticated gh CLI, assigned-to-me and saved search qualifiers. All GitHub requests go through gh, including comments and status changes.
- Multiple named connections, URL/ID lookup with ambiguous-ID picker, complete pagination or an explicit limit error, cached results and last refresh time.
- Global prompt instructions, repository overrides, editable Investigate/Plan/Implement/Review presets, repository mappings, preferred agent/base branch/branch naming/validation instructions.
- OS credential storage or named tracker environment variables; never store tokens in project files. Do not collect a separate GitHub token.

## Context and reviewed handover

- Compose effective instructions, preset, validation guidance, extra notes, ticket details, and selected context into an editable opening user prompt.
- Select parent/subtasks/related issues and comments; add arbitrary text, repository file references or text snapshots with line ranges, local files, images, and screenshots.
- Display the context inventory and allow preview, reordering, and removal. Copy explicit attachments into private handover storage; preserve originals and do not upload them to trackers.
- Resolve file references against the selected checkout/commit. Reject missing files, invalid ranges, or binary text snapshots. Never silently truncate context.
- On hosts without generic image payload support, disclose local-path delivery and require acknowledgement before launch.
- Review repository, agent, target branch/worktree, complete prompt, and attachments. Require Codex, Claude Code, and Copilot launch support; enable OpenCode/Muse when installed and recognized by Luvus.
- Reuse existing branch checkouts or create an explicit worktree from the reviewed commit. No force checkout, reset, stash, or implicit changes to dirty checkouts.
- Launch in a dedicated terminal with explicit cwd and stable identity; wait for readiness before prompting. Persist stages and never blindly retry ambiguous mutations.

## Status, history, completion, and write-back

- Keep tracker state separate from live agent state. Manual tracker transitions use Jira workflow metadata, Azure revision checks, and GitHub open/closed reasons; verify with readback.
- Persist submitted prompts, context, worktree, agent identity, and known native session IDs. Support editing unapproved drafts, resuming exact sessions, explicitly starting fresh, and reviewed follow-ups.
- Completion review shows original criteria/context, Git changes, and available agent output. Label reported vs independently observed evidence; do not infer completion from agent idleness.
- Preview and explicitly confirm comments and branch/PR links, publish through the provider, then read back. Record markers to reconcile timeouts and prevent duplicates.
- Explicit history deletion removes only selected module-owned snapshots/attachments. Preserve original files, worktrees, sessions, and tracker data.

## Verification and acceptance

Unit-test configuration/prompt precedence, providers and pagination, errors and stale results, related context/files/images, worktree decisions, duplicate prevention, startup/delivery uncertainty, session identity, and reviewed writes. Run CI on macOS/Linux/Windows. Smoke-test manifest/sidebar/pane in an isolated Luvus home. Use designated live test tickets and installed agent CLIs for end-to-end validation; report unperformed checks honestly.

Historical pre-0.5 deferrals (PR creation is superseded below): self-hosted trackers/GitHub Enterprise, GitHub Projects field editing, board editing, automatic tracker synchronization/closure, native image transport absent from Luvus, automatic attachment uploads, PR creation, commits, pushes, and merges.

## Grouped implementation (0.5 source, 2026-09-06)

The approved grouped increment adds PR/CI for GitHub and Azure, reviewed PR creation, shared attention/search, task evidence/artifacts/timeline/summaries, Project Commands integration, read-only reviewer capability checks, isolated approach drafts, Launcher bundle contracts and declared ownership warnings. Scheduling remains a compatibility check only. See README.md and CONTRACTS.md for final behavior and limitations; VALIDATION.md records checks actually run. Live installation and unsupported reviewer execution are not claimed complete.
