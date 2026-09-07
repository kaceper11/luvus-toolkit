> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Luvus Tasks

Jira Cloud, Azure DevOps Services, and GitHub Issues in a Luvus sidebar and terminal pane. Review a ticket, add context, choose a prompt preset, and launch an agent on a new worktree or existing feature branch.

This is a standalone local module. It does not fork Luvus or run a hosted service. GitHub access uses your existing **`gh` CLI login**.

## Setup

Requires Python 3.11+, Git, and Luvus 0.13.4 or newer with the required UHP methods. Install the agent CLIs you want to use. The module checks both Luvus's agent registry and available executables for Codex, Claude Code, Copilot CLI, OpenCode, and Muse Code.

macOS / Linux / WSL, inside this repository:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python launcher.py doctor
luvus module link .
luvus module pane open personal.luvus-tasks tasks
```

Native Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe launcher.py doctor
luvus module link .
luvus module pane open personal.luvus-tasks tasks-windows
```

Ensure `python3` (Unix) or `python` (Windows) is on Luvus's PATH. The manifest launcher redirects to this repository's `.venv`, so the bootstrap interpreter may be older. Windows users can use `python -m venv .venv` if their `python` is already 3.11+. WSL uses its Linux Python, Git, Luvus, agents, and file paths.

Open **Configuration → Connections / Test connection**. Or run `.venv/bin/python launcher.py configure` directly (use the Windows virtualenv path on Windows). No provider connection or production module installation is performed by installing the Python package.

## Task cockpit (0.4)

The full-tab Textual console has **Issues**, **Handovers**, **Draft editor**, **Attention**, and **Configuration** tabs. Optional ORCH coordination uses native Luvus views. Azure DevOps remains REST/PAT; no Azure CLI is required.

- Click a row to highlight it; click its checkbox column to select/unselect it for batch drafts. Use selectable details, search, connection/repository/saved-view selectors, favorites, and recent issues. Click column headings to sort.
- Use visible buttons or **⋯ Actions** / **F2**. **Ctrl+F** focuses search, **Ctrl+R** refreshes, **Ctrl+P** searches commands, **Ctrl+Q** closes the console. Tab/Shift+Tab and Enter operate forms. The layout stacks at narrow widths.
- Native right-click menus are available on task sidebar rows, workspace rows, agents, and terminal panes (add selected text to a draft). Luvus 0.13.4 owns right-clicks inside terminal panes, so console issue-row actions use the equivalent clickable menu instead.
- Repeated **Open Tasks** actions focus the exact registered console in that session. Other sessions' queued actions remain untouched. Existing numbered-menu panes are not closed during upgrades; `launcher.py legacy` remains available as a compatibility fallback.
- **Handover** is a persistent editor with **Target**, **Context**, and **Prompt** sections, searchable workspace/branch choices, direct context buttons, and **Review & launch**. Changes autosave locally with visible save feedback. **Save & close** preserves the draft; save failures block launch and switching drafts. Configuration forms still warn before discarding unsaved edits.
- Generated prompts follow current instructions/context. Edited prompts are preserved: choose **Regenerate** or **Keep edited prompt** after inputs change. Legacy prompts are treated as custom. Invalid repository references are marked individually without dropping other context.
- Workspace right-click offers **Open Tasks** and **Handover / continue in this worktree**. The latter uses the clicked checkout and asks which issue to use. An exact linked live agent offers a reviewed follow-up or focus instead of starting a second worker. Detached checkouts need an explicit target; default-branch reuse requires acknowledgement.
- The right-hand dashboard groups **Needs attention**, **Active handovers**, **Drafts**, and **Open issues**, using native status dots and contextual actions. It starts scoped to a named workspace repository, including its worktrees. **Use current workspace** updates that scope; **All repositories** widens it. Scope is explicit, not an instant workspace-focus subscription. View-all links retain scope; **All repos** in the cockpit clears it.
- File/image context uses a directory picker or pasted path. Repository references may capture a selected-commit text snapshot and line range. Image paths require explicit acknowledgement; no native image payload or tracker upload is implied.
- Handovers offers an attention filter, resume, draft editing, native DIFF navigation, selected DIFF-note follow-ups, completion checklist, comments/status changes, and explicit uncertainty reconciliation. Missing workers are unavailable, not completed.
- Repository configuration suggests connection mappings from recognized Git origin URLs; mappings require confirmation. Unknown remotes can be mapped manually.
- Settings use an inline editor in the Configuration tab, with Save/Cancel, not a chain of modal overlays. Text inputs use solid borders. Navigation-only settings changes do not refresh the native sidebar.
- Select preview text and press **Ctrl+C**, or use **Copy** / **⋯ Actions → Copy data** for IDs, URLs, rows, complete details and handover prompts. The native pane menu also offers **Tasks: Copy selected text**, using the selection captured from the clicked pane. Clipboard support depends on the terminal; copy actions provide a selectable preview fallback. Password fields are excluded.
- Remove connections with explicit confirmation; cached issues are cleared but histories, worktrees, prompts and attachments remain. Keyring credential deletion is separate and unchecked by default. Prompt settings can clear/restore global instructions or delete presets with replacement defaults. Repository overrides can be removed to inherit global settings.

### Optional ORCH tracking

Direct reviewed agent handover remains the default. Enabling ORCH adds **Link ORCH task** and **Claim linked ORCH task** under handover actions. New tasks can declare dependency IDs and path scopes. Creating/linking a task does not start an agent or claim it. Claiming a verified worker separately requests the declared path leases; a lease conflict leaves the task claimed and does not stop the worker. Use explicit Release to requeue/release leases.

The module never uses native ORCH agent launch (whose built-in briefing can request commits). Linked tracking does **not** bind native branch/merge management. Native task state, dependencies, outputs, and leases remain authoritative. Only linked tasks can be modified through this console. Task boards and orchestration views remain in native Luvus; Tasks adds no second ORCH tab or dock.

**Mark done** is a separate confirmed action and may run an existing native quality gate. Gated completion requires the exact claimed worker and matching checkout; the module rejects active-workspace fallback. Running gates and uncertain writes are not blindly retried. An uncertain task creation is recovered by inspecting native ORCH and explicitly linking the created task. No automatic commit, merge, push, scheduling, task deletion, worker stop, worktree removal, or tracker closure is added.

## Connections

| Provider | Configuration | Authentication | Saved filters |
| --- | --- | --- | --- |
| Jira Cloud | Site URL, email, optional cloud ID and acceptance-criteria custom field | API token in OS credential store or `LUVUS_TASKS_TOKEN_<CONNECTION>` | JQL; blank means assigned to current user |
| Azure DevOps Services | Organization name and project | PAT with Work Items read/write in OS credential store or the same environment-variable convention | Flat WIQL; blank means assigned to current user in this project |
| GitHub.com | User/organization and explicitly selected repositories | Existing local `gh` login; no additional token | GitHub issue search qualifiers; blank means all open issues, regardless of assignee |

Jira Cloud and Azure Boards tickets can map to GitHub or Azure Repos checkouts. Map the ticket project to the code checkout; configure a separate authorized code connection when needed. Grouped tickets must all map to one checkout/repository. The project filter includes Jira projects, Azure projects and GitHub repositories.

PR discovery checks branch names and explicit ticket keys/URLs in PR titles and descriptions. Azure Repos also reads native work-item PR links, scoped to the same organization and exact code repository. These links can discover older PRs outside the bounded scan. Missing Work Items read permission is shown as a partial-discovery notice; branch/title discovery and manual URLs remain available. Azure PR/CI reads require Code read and Build read permissions; native work-item links require Work Items read. Publishing or changing tickets requires the corresponding write permissions. Jira search retains descriptions and the configured acceptance-criteria field in handovers. Contracts: [Jira issue search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/), [Azure work-item relations](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/get-work-item?view=azure-devops-rest-7.1), and [Azure PR artifact identity](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/get-pull-requests?view=azure-devops-rest-7.1).

Supported hosted services are Jira Cloud, Azure DevOps Services (Boards, Repos and Pipelines), and GitHub.com. Jira Data Center and Azure DevOps Server are not implemented.

For **scoped Jira API tokens**, configure the cloud ID; the client uses `api.atlassian.com/ex/jira/<cloud-id>`. Classic tokens use the site URL. Your Jira account must have browse and transition/comment permissions. The module does not bypass workflow rules or organization token policies.

For GitHub:

```sh
gh auth login --hostname github.com
gh auth status --hostname github.com
```

GitHub configuration displays the locally authenticated user, then lets you choose that user or an accessible organization and explicitly check multiple repositories. Search, select/clear visible results and refresh discovery without automatically selecting newly discovered repositories. One owner per connection; add another connection for another owner. Refresh/cache operate per selected repository, so a failed repository does not erase successful results from others. Account changes require connection review before refresh or writes.

Legacy single-repository connections remain supported. The original repository retains its numeric IDs and write markers; additional repositories use repository-qualified IDs, preventing same-number collisions without rewriting history. Invalid legacy scopes require explicit repair; stored issue evidence is offered for confirmation, never silently guessed. GitHub's blank default is **Open issues** (`is:open`), including unassigned issues. Existing untouched “Assigned to me” defaults receive this label when loaded; non-empty custom queries remain unchanged. Cache keys use the effective query so the old mixed-state default cache is not reused. Example filters: `is:open label:bug`, `is:open assignee:@me`, `is:closed`. Omit `repo:` because explicit selections supply scope. The 1,000-match search limit is checked per repository. GitHub Projects board fields remain separate from issue open/closed status.

Tokens are never written into config.json. Credential storage uses `keyring`; if no secure backend exists, use the documented environment variable instead. Blank tokens are rejected. In headless Linux/WSL a desktop secret service may be unavailable. Tracker environment tokens and `GH_TOKEN`/`GITHUB_TOKEN` are removed from the agent shell environment. Agent shells skip startup profiles to prevent re-exporting tracker tokens; configure agent executables on Luvus's inherited PATH. This is not an OS sandbox: agents still run under your user account, and can access tools/credentials otherwise available to that account (including gh's own credential store).

## Task workflow

When no draft is open, **Draft editor** explains its purpose and offers **Choose an issue** and **Browse saved drafts**. **Handovers** is the history/list; **Draft editor** edits one draft. These navigation buttons do not create a draft or start an agent.

1. Browse assigned tasks or saved filters, or paste a ticket URL/ID. Ambiguous IDs require selecting the connection. Only configured provider hosts/repositories receive authenticated requests.
2. Inspect task details and original ticket URL. Refresh is manual and on opening; cached results are labelled. Sidebar rows focus the console and select the clicked ticket.
3. Optionally change status. Jira shows its available transitions; Azure respects its work-item rules; GitHub offers close as completed/not planned or reopen. All changes require explicit confirmation and a readback. GitHub's stale-data check is best effort, not an atomic revision guard.
4. Choose **Handover**, repository, agent, and either a new branch/worktree or existing local branch. New worktrees use the reviewed commit. Existing checkouts are reused without switching branches or stashing changes. Dirty checkouts require acknowledgement.
5. Add context, choose a preset, edit the complete prompt, and confirm launch.

The sidebar shows ticket state separately from agent state. Cached tracker data is not evidence of a live provider connection. Agent status is read from Luvus and updated by its status event hook; missing state is shown as unavailable.

## Instructions and context

Configure global instructions and repository overrides, plus **Investigate**, **Plan**, **Implement**, and **Review** presets. Global branch defaults and per-repository overrides support `{type}`, `{key}`, `{number}`, `{repo}`, and `{slug}`; the new default is `feature/{number}-{slug}`. Existing configured patterns are preserved. You can configure the base and worktree parent folder, agent, preset and validation instructions. A repository instruction override replaces global instructions, including when deliberately empty.

**Repository path** means the local Git checkout. Handover offers existing Luvus workspaces by name, branch and root path, filtered to the GitHub issue's repository, with a validated manual path option. Terminal working directories are not treated as workspace roots. Missing mappings no longer default to this module's repository. Target edits show a branch/path/commit preview; clear the base field to choose a verified local or remote-tracking branch. No automatic fetch occurs. Invalid/unborn bases preserve the draft and explain which checkout needs attention. Context can go back to Target without losing text/attachments; repository references are revalidated. The reviewed commit remains pinned and GitHub repository mismatches are blocked before review and checked again at launch; a matching `origin` is required, even for saved drafts.

The opening prompt contains effective instructions, preset, validation instructions, handover notes, ticket details, and selected context. This is an opening **user prompt**, not an override of the agent's system prompt. Per-launch edits do not change defaults or repository instruction files.

The context builder supports:

- Extra text, with editable labels.
- Parent/subtasks/linked issues and selected comments. GitHub parent/sub-issues in another repository require a configured connection for that repository. Arbitrary URLs and other context can be added as text.
- Repository-relative file references and optional line ranges; reference-only is the default. Text snapshots are read from the selected checkout or the selected commit before a new checkout exists.
- Explicit local files, images, and screenshots, entered as paths. Files are copied into private module-owned handover storage; originals are preserved. No images or files are automatically uploaded to a tracker.

Preview, reorder, or remove context before launch. Binary files cannot be embedded as text. Missing files and invalid line ranges are errors. Paths are local to the machine running Luvus; remote SSH users must place attachments on that host.

Luvus 0.13.4's generic agent prompt interface has no image payload field. Images are supplied by local path, with explicit acknowledgement that the agent may not read them automatically. The module does not claim native image attachment support. For large material, prefer file references: prompts are limited to 262,144 characters and the UHP frame limit. Nothing is silently truncated.

## History, recovery, completion, and write-back

Every draft stores its prompt/context, target, and launch progress. **History → Edit draft** resumes an unapproved draft. **Resume** focuses the exact live terminal or restores a known native session when available. Unknown sessions offer a fresh handover; the module does not guess conversation identity.

Launch retries reuse successful worktree/terminal steps. Uncertain terminal creation, agent startup, or prompt delivery requires inspection and explicit reconciliation in history. A pending prompt is never automatically resent. After a process crash, operation lock files may remain; only remove the named lock after verifying no task pane is performing that operation.

Completion review shows original criteria/context, current Git changes, and available recent agent output. Agent-reported checks are not marked independently verified; the module does not execute repository validation commands itself. Completion does not automatically close tickets.

**Publish reviewed comment/link** accepts a progress/completion comment and optional HTTPS branch/PR URL. The exact destination and comment are previewed, confirmed, published, and read back. A visible `luvus-tasks` marker prevents accidental duplicate submissions and helps reconcile uncertain network outcomes. The module never posts automatically, commits, pushes, or merges. PR creation is a separate explicitly reviewed workflow described below.

**Remove history** deletes the selected prompt/context record and its module-owned attachment copies. This is permanent. Original files, Git worktrees, agent sessions, and published comments remain. Published-comment deduplication records are retained separately.

## Evidence, PR/CI and attention (source version 0.5.0)

Select a handover to see its branch and compact PR/CI status. **More…** contains artifacts, logs, summary and history. Summary and Timeline reuse its existing SQLite history. Add artifact associates a local output file without copying/uploading it; Evidence offers details, Open, Reveal, removal of the reference, and reviewed follow-up. File size/mtime detect changed or missing outputs. Creation time is labeled as association time when the original creation time is unknown.

**More…** offers authorized PR selection, refresh, provider browser, selected feedback, job output, reviewed PR creation, validation, independent review, two-approach drafts/comparison, ownership warnings and repository bundles. GitHub uses the configured `gh` account and explicit repository scope. Azure uses the configured organization/project/PAT and exact origin repository. Jira issues may link to either supported forge through a separately selected authorized connection. No repository is automatically selected when repairing an invalid GitHub connection.

PR publication previews destination, source/base branches and remote commits, title/body, and draft state. The source branch must already be published, with its remote head matching local HEAD. Remote refs are rechecked before creation; this is a best-effort guard, not an atomic provider transaction. A pending/uncertain creation cannot be blindly repeated, even with a changed title. Select the actual PR to reconcile it. Publishing never pushes a branch or closes a ticket.

Checks and runs retain their revision; Azure merge-build evidence is distinguished from source-head evidence, and policies with no verified revision stay unknown. A failed or partial refresh preserves the previous snapshot as stale. PRs are discovered automatically by exact branch and explicit issue references. Multiple PR links are saved locally, without tracker edits. Saved authorized forge identities also support reads after a checkout disappears. Discovery scans up to 500 PRs for issue references and labels that limit when reached; older PRs can be linked manually. Link / Open PRs supports refresh, manual URLs, ignoring suggestions and restoring ignored links. A unique exact-branch match becomes the primary PR; related PRs never silently replace it. With no open PR, branch CI is still checked. Checks run on console opening/refresh, every minute while the console is running (any tab), and on native dock lifecycle events. Up to four distinct checkout/branch/selection groups are processed per pass; identical handovers share reads. Successful reads are throttled for 60 seconds, failures for five minutes. Larger queues can take additional passes; manual PR refresh remains available. Attention treats PR observations older than two minutes as stale. Logs save at most a 2 MiB excerpt, expose omitted-byte counts (a lower bound when length is unknown), and link to the complete provider output; the preview is limited to 16,000 characters. GitHub log downloading uses a temporary file and 60-second timeout; the full temporary download can exceed the saved excerpt limit.

**Sidebar PR / CI** shows a short branch status, linked PR count/state, current-head CI, feedback count and stale state. Click it for PR links and discovery controls. Detailed feedback, job logs and reviewed follow-ups remain under More. Agent messages still require review and confirmation; automatic discovery never sends messages or edits PRs.

**Attention** combines linked handovers and unlinked agents across all workspaces in the selected Luvus session. Native menu actions and the command palette provide Attention and Find work. Native metadata search is reused for workspace/pane/file/output results; Tasks adds issue/branch/agent/artifact associations without another index. The installed native search API has no custom result registration, so Tasks results appear through Find work rather than being injected into native global search.

Opening input requests never approves them. Replayed observations are deduplicated; acknowledging finished work applies only to that observed occurrence. Source failures remain unknown. Optional CI/validation/review notifications default off, reserve delivery before sending, and do not duplicate native agent or AI Usage notifications. A lost notification reply may suppress a retry; inspect Attention as the authoritative list. Host state uses lifecycle hooks and a 15-second console refresh; a session-owned module startup reader also checks PR/CI with the console closed. It processes one due checkout group per five-second tick, shares the existing success throttle/failure backoff with console and event reads, and stops on module disable, removal, or session-generation change. Provider latency can delay reads and shutdown until the in-flight request finishes. Stale navigation identities are rejected.

Validation/setup runs belong to **Project Commands**, not Tasks. Configure the bridge described in [CONTRACTS.md](CONTRACTS.md). Commands must exist for the exact checkout; new, not-yet-created worktrees have no automatically inherited setup configuration. Setup is unselected by default during launch review. Selected setup must finish successfully with current evidence before an agent can start. Refresh/reconcile or explicitly retry its run through Evidence; successful worktree creation is retained. A setup command that changes fingerprinted source/config cannot satisfy the current-evidence gate until reconciled with the producer. Native ORCH completion/gates continue through the existing ORCH workflow; their reports do not become independently verified checkout validation merely by being displayed.

Checkout fingerprints include HEAD, file mode and tracked/untracked contents, bounded to 64 MiB per checkout. Oversized files, submodules, unsafe paths or changing snapshots produce unknown freshness. Ignored files, dependencies, external services and the wider environment are outside this fingerprint; it is evidence of observed source state, not a reproducibility guarantee. Evidence details recompute local freshness; saved timestamps/statuses are not a fresh test run.

Independent review freezes the selected base/diff, working-tree source, criteria and validation evidence into the handover's private storage. A separate Codex execution must pass an actual read-only sandbox probe; unsupported flags or a failed probe disable launch. There is no prompt-only fallback. The current macOS probe passes after selecting the explicit permission profile required by this Codex installation. No reviewer was launched; end-to-end reviewer execution remains unverified. A completed reviewer process means findings were produced, not that its recommendations or the implementation are correct. Findings use the existing artifact/follow-up route; changed source invalidates freshness.

Compare two approaches prepares two independent drafts pinned to the same current HEAD, each with its own worktree and reviewed launch. Uncommitted implementation changes are not the shared base. Comparison shows diff statistics and recorded validation, with no automatic winner or merge. Ownership warnings use native declared task paths/leases; possible glob overlap is not a confirmed Git conflict or prevention of external edits.

Bundles store only the task/repository/worktree relationships in Tasks. CLI Launcher opens/reuses workspaces through its versioned contract, checks each branch, and reports partial failures. Layout/service starts are not requested by Tasks. Configure both ends explicitly; no integration was enabled during this implementation. Scheduling only reports native compatibility. The inspected Luvus 0.13.4 server did not advertise automation; no scheduler or upgrade was added.

## Storage

Luvus supplies `LUVUS_MODULE_CONFIG_DIR`. Outside a module invocation the fallback is the same native directory: `<LUVUS_HOME>/modules/config/personal.luvus-tasks` (default `~/.luvus/...`). `config.json`, `tasks.sqlite3`, attachments, and managed worktrees live there, never in the target repository. The old standalone-only `module-config` directory is left untouched; if you configured 0.1 outside Luvus, preserve it and reconcile its data manually rather than overwriting native module configuration. On Unix the module creates private directories and files; Windows inherits the current user's directory ACLs. Back up this directory if you need handover history.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/smoke_luvus.py
```

The second command is opt-in: it starts and stops a disposable Luvus server in a temporary home, registers the module there, and verifies the pane/sidebar. It does not install into your production session, access trackers, or launch coding agents. CI runs unit tests on macOS, Linux, and Windows.

Implemented unit coverage includes provider pagination/auth/network failures, status rules and stale updates, Git worktree safety, prompt/context behavior, attachment retention/deletion, duplicate launch/write-back protection, and GitHub CLI argument handling.

Live Jira/Azure transitions and comments require designated test tickets and credentials. Native Windows/WSL and real agent prompt/image behavior require their respective environments. See `VALIDATION.md` for checks actually run.

## References

- [Luvus modules](https://luvus.dev/docs/extend/writing-modules/) and [UHP methods](https://luvus.dev/docs/uhp/methods/)
- [Jira search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/) and [issue transitions](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/)
- [Azure WIQL](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/wiql/query-by-wiql?view=azure-devops-rest-7.1) and [work-item updates](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/update?view=azure-devops-rest-7.1)
- [gh api](https://cli.github.com/manual/gh_api) and [GitHub sub-issues](https://docs.github.com/en/rest/issues/sub-issues)


## Tasks panel navigation

The right sidebar puts **Current work · PR / CI** first, grouped by repository, then **Open issues** below. Issue titles open the exact issue in Tasks; branch rows open the saved handover. Short status rows show pending work and PR/CI, with links to larger lists. Missing local checkouts are explained in handover details instead of repeated warnings in the sidebar. The top Browse issues menu switches scope and opens configuration.

Select issues with their checkbox, Space, or **Select / deselect**. **Group selected** opens a searchable multi-issue picker. Its selection survives filtering; a group becomes one handover, agent and branch/worktree. Different trackers may share a group when every issue maps to the same code repository. Group membership can be edited before approval; approved or started handovers are frozen. Existing single-issue handovers remain supported. Follow-up phases inherit the group.

**Handovers** shows issue/group, branch and compact PR/CI columns. Select a row for full details, use **Link / Open PRs** for linked PRs, or **More…** for artifacts, logs, history and other advanced actions. **Draft editor** prepares one reviewed handover. Attention retains its dedicated cross-workspace view.

Known limits: native sidebar rows have a fixed text/menu layout, so complete paths, revisions and long error details remain in the console. GitHub resolved-thread detection is not complete through the existing REST comment adapter; cached feedback is not a guaranteed list of actionable unresolved discussions. Automatic polling and platform/reviewer limitations remain as documented above.

### Finding and continuing work

Find work searches saved/cached issues and handovers with connection/repository-qualified labels. Native results default to workspace/tab/agent navigation; include files/output explicitly when needed. Partial results are labelled. Issue results open inside Tasks.

Handover / continue in this worktree offers saved work even when its agent has stopped, validates the clicked checkout and branch, and only offers repository-matching issues. If no issues match, configure the connection’s repository mapping and refresh it. It never assigns an unrelated issue or starts an agent merely by opening the shortcut. After updating, close and reopen the Tasks console to load the new code.


## Draft and navigation reliability — 2026-09-06

- Draft editors open from saved data immediately; agent/workspace/branch discovery runs afterward without changing the active tab. Opening another draft retains the previous editor buffer. Incomplete drafts autosave; failed saves remain visible and do not block navigation. Save and Back to handovers are explicit actions.
- Tab changes clear stale focus before focusing the destination. Sidebar shortcuts remain responsive during provider work, and the latest pending navigation wins. Late operations retain their original issue/handover identity and cannot reopen an abandoned form or editor. Each newly opened console has its own shortcut inbox.
- Find work displays local matches before native search completes. Tables retain scroll and selection across unchanged refreshes; filtering out a selected row clears its action target. Checked issues survive filters with visible/hidden counts and explicit clearing. Copy confirmation is nonmodal.
- Interrupted text forms retain their input for the current console session. Exit requires explicit discard if a save failed or interrupted form text remains. These temporary form buffers are not crash-persistent. Saved unavailable agent choices remain visible. Drafts without a checkout render and open safely.

Validation includes isolated Textual interaction tests for delayed discovery, tab focus, failed-save draft switching, busy shortcuts, progressive search, action identity, long-list scrolling, hidden selections, interrupted forms and independent console inboxes. Desktop mouse behavior and Windows/Linux behavior remain unverified; no real agent launch or tracker write is part of these checks.


## Productivity workflows (source 0.6.0)

The four planned implementation stages are available in the source module. Existing provider scopes, approved prompts, worktrees and write journals remain intact. All launches, follow-ups and publications use a reviewed action.

| Capability | Where to find it |
| --- | --- |
| Start / Continue | Primary Issues button, sidebar menus, command palette; reuse an existing draft or choose a saved worker. Issue actions retains New handover. Configured, valid targets go directly to review; select Edit details instead of launching to change them. |
| Archive / Restore | Handovers toolbar and sidebar archive menu; Archived filter and Find work's Include archived option. Eligible unused drafts and reviewed completed work can be archived. Active/unknown workers and unresolved operations block archival. Nothing is deleted. |
| Context packs | Draft editor → Context → Context packs. Save selected notes/repository references, apply, rename, edit notes, or delete. Edit references in a draft and replace the pack. Applied context is independent of subsequent pack edits. |
| Next phase | Handovers → Next phase. Select evidence and editable instructions; reuse the exact live conversation or create a linked draft in the same checkout. Selected files copied to new handovers have independent storage. Independent read-only review remains a separate workflow. |
| Batch draft editing | Issue actions → Batch edit selected drafts, or command palette. Review every selected issue, including hidden selections; apply chosen agent/preset/notes/pack, preview targets, and save valid items. Launch review remains per task. |
| Up next | Issues → Up next; add from Issue actions or the palette. Move tasks up/down, remove, or explicitly Start / Continue. Native dependency observations and worker identity are shown; no scheduler starts agents. |
| Attention triage | Source and Current/Snoozed selectors, Expand / Collapse groups, Snooze/Unsnooze. Review/feedback occurrences can be snoozed for 15 minutes, 1 hour, or 1 day; changed signatures resurface. Input, uncertainty, CI and validation conditions cannot be snoozed. |
| Quick capture | Issues → Capture / Captures or the native pane menu Tasks: Capture selected text as task. Local autosave works without a connection. Publish or link an actual tracker issue before Start / Continue. |

When an Issues or Handovers table has focus, **s** starts/continues; in Handovers **f** prepares follow-up, **e** opens evidence, and **a** archives/restores. **]** moves to the next attention item. **F6** reviews the active draft. Text inputs and modal forms keep their normal keys. All actions are also available from **Ctrl+P**.

Issue opening renders cache first, then refreshes provider/query groups progressively. **Refresh repo** refreshes the selected issue's exact connection/project scope; **Refresh** refreshes all configured scopes. Failed groups retain cached data. Search, sorting and navigation consume existing attention snapshots; checkout hashing runs outside the UI thread and is shared per checkout within an attention pass. Restoring an archive schedules a normal PR refresh; archived targets are excluded from routine polling.

Capture publication supports GitHub's existing gh account, Jira Cloud create metadata, and Azure work-item types. Required scalar fields and single-choice values are supported; unsupported multi-value/custom fields offer provider-browser completion followed by explicit issue linking. No attachment is uploaded. Pending or uncertain creation cannot be repeated, including with another destination. A returned issue identity survives readback failure; use Link an existing / created issue to reconcile. Publication checks reviewed content and connection identity before sending.

New storage is additive: captures have their own SQLite table; queue, snoozes and repository packs use preferences; archive and phase links are optional handover metadata. Existing records load with no archive/phase flags. Pack v1 contains notes and repository references, not binary attachments or live tracker comments.

To load this source version, save drafts and reopen the Tasks console. Relink the module from this repository (`luvus module link .`) to register the new native capture actions and version. Existing production panes were not forcibly restarted during validation.

A controlled search-path benchmark is runnable with `.venv/bin/python tests/benchmark_productivity.py`. It reconstructs the previous synchronous attention path and uses the same current renderer for both samples; it does not measure desktop interaction latency or compare complete released builds.

## Tasks context action

Right-click a workspace, pane, or agent and choose **Tasks** to open the issue dashboard directly. The action reuses the verified console terminal and switches to its workspace/tab. It returns to Issues and focuses the issue list, preserving drafts and the clicked repository context. Starting or continuing work happens inside the dashboard. Existing dock/bar/CLI action IDs remain available.

GitHub setup recovery: if an older connection stores only a username/organisation, use **Configuration → Repair invalid connection**. A single invalid connection opens directly in setup; a recognised saved owner goes straight to repository selection. Review and save the selected repositories to refresh automatically. Cancelling leaves the connection unchanged.

## ORCH and checkout awareness (0.7.0)

With ORCH enabled, reviewed launches create/reuse a native task and claim it for the exact worker before prompt delivery. Optional dependency IDs and path scopes are reviewed at launch; scopes are never inferred as whole-repository leases. Failures preserve the existing task and launch stage. Old delivered handovers are not automatically claimed. Next phases in a new handover receive their own task; same-conversation follow-ups reuse the existing task.

Independent reviews claim a separate native task through the trusted wrapper before starting the read-only reviewer. A completed review run does not approve implementation. Reviewed implementation completion uses native task completion and its configured gate. Uncertain review creation/completion can be inspected and reconciled from Evidence; retries require confirming that the old reviewer has stopped.

Native Luvus owns ORCH presentation. Tasks retains optional task/lease coordination without an extra orchestration tab or sidebar.

Handovers and launch reviews show branch, dirty/staged/conflict state and ahead/behind counts from local refs. Git reads run outside rendering; stale observations are labeled. Sends recheck the checkout/branch and terminal identity. Dirty/behind state is advisory; existing dirty-worktree acknowledgements still apply. No fetch, commit or merge happens automatically.

Configuration → Connection health lists incomplete GitHub scopes and unresolved PR connections. Select PR connection shows the account and matched repository; Save and continue can remember the choice for that repository. A changed account, origin or authorized scope invalidates the choice. Background polling records the condition without opening dialogs. Cancelling repair preserves history and existing selections.

Module tabs use short descriptive names with a small symbol. New agent tabs include their task or instruction. Existing manual names are preserved; background tabs receive their name when focused. Naming is best effort and never retries an agent launch.


## Configurable bounded workflows (0.8.0)

In **Configuration → Workflow recipes**, create, edit, duplicate or delete a recipe. Recipes configure instructions, validation command IDs, additional repair attempts (0–10), agent-step and total deadlines, and publication permissions. A repository can remember its preferred recipe. Approved runs retain their own immutable recipe and input snapshot.

On a launched handover, choose **More → Address PR feedback workflow…**. Select current GitHub or Azure Repos feedback, choose a recipe and checks, then review the exact PR, checkout, HEAD, worker, comments and permissions. The checkout must be clean and the matching worker idle. Validation uses the configured Project Commands bridge (0.1.2 or newer); command definitions must be reviewed there first.

The fixed sequence is address feedback → validate → bounded repair → permitted publication. Commit, push, reply and resolve are off by default and reviewed per run. Push requires commit; resolving requires push and replies. Without publication permission, changes and draft replies remain local for review. The runner never force-pushes or merges. These permissions control runner actions; they are not an OS sandbox around the coding agent.

The existing session-owned Tasks helper advances runs while the console is closed, as long as Luvus and the enabled helper remain running. **Workflow runs** exposes state, journals, drafts, the worker pane, pause, resume/reconcile and cancel. Paused runs reserve their checkout and worker until resumed or cancelled. Pause/cancel prevent subsequent steps; an already-running command or agent must be stopped through its owner if needed.

Success requires an explicit versioned agent report covering every selected feedback item and the complete changed-file set. Idle status alone never advances a run. Source, PR head, selected feedback, worker identity and command evidence are rechecked. Disagreements, stale inputs, repeated failures, exhausted limits, deadlines and ambiguous writes pause the run. Resume retains the original deadline and approvals and reconciles recorded operations; it never blindly resends a prompt or reply. A changed native session/worker requires manual review and a new run. Freshness is based on observed checkout inputs, not a hermetic build snapshot.

After updating linked source, reopen the Tasks console and reload its module helper in the intended Luvus session. An already-running Python helper does not load the new runner automatically.


### Workflow review fixes (0.8.1)

The approval screen now shows a readable summary and checks for a recently active helper in the selected session and a compatible Project Commands evidence API. At least one validation command must be selected. Reload the Tasks helper after updating so it can publish its heartbeat.

Running workflows appear in Attention with phase, attempt and remaining minutes. Open the item for workflow controls and access to its worker. Remote PR/feedback reads are deferred while waiting for an agent report, then refreshed before the next phase. Deadlines are checked again immediately before dispatch/publication; already-started operations may finish.

Workflow history is paginated (50 per page). Configuration → Workflow recipes → Finished-run history limit sets how many completed/cancelled runs to retain (default 200, range 1–5000). Old finished runs and their workflow evidence/timeline entries are removed; active and paused runs are retained. CLI `workflow list` accepts `--offset` and `--limit` (up to 200). The helper queries one running row through a state/time index instead of loading terminal history.

Project Commands 0.1.3 supplies bounded redacted diagnostics and the beginning/end of long logs. Repair prompts preserve both ends when distributing the context budget across failed checks.
