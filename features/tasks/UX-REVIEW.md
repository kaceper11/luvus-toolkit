# Tasks UI/UX review — 2026-09-06

A second Codex agent reviewed the sidebar and console source, call paths and isolated headless behavior, then reviewed the fixes. The primary implemented and validated the changes. No agents were sent task/PR feedback during this review.

## Findings and changes

| Finding | Adjustment |
|---|---|
| Handover and Handovers looked interchangeable; an empty editing tab gave little guidance | Editing tab is now Draft editor, with a clear explanation and Choose an issue / Browse saved drafts buttons. Opening these routes does not create or launch work. |
| Evidence / PR had enabled actions with no selected handover | Replaced the unusable toolbar with Browse handovers / Choose an issue. The selected task and branch appear above real evidence. |
| Sidebar repeated handovers across active/attention/draft and PR lists | One card per handover; PR status/feedback sits under that card. Empty groups are omitted and hidden records have view-all links. |
| Long repo, branch and error text buried useful information | Compact titles/status rows, ellipses with full content in console, short actionable missing-checkout/connection labels. Mixed-project scopes retain repository-qualified issue labels. |
| Scope and global attention were easy to confuse | Repository scope is prominent, switchable through its context menu. Attention explicitly says across all workspaces. |
| Attention records lacked an actionable reason and ordering | Current input/failure conditions sort before stale observations; each attention card has a reason and a direct link to its selected attention record. |
| Clicking a handover could unexpectedly focus/resume an agent | Default click inspects saved handover details (or opens an unapproved draft). Open / resume agent is an explicit menu choice. |
| PR menus offered comment/failure actions without corresponding data or a live agent | Menus depend on existing feedback/failures. Missing checkouts expose saved details only. Without a linked live agent, feedback is inspected instead of offering an immediate send. |
| Inspecting results could require a successful provider request | Saved PR details open without network. Feedback inspection falls back to retained evidence on provider failure and blocks sending from that offline fallback. |
| Attention details were JSON and Mark reviewed appeared universally usable | Readable reason/source/freshness/navigation explanation; acknowledgment disabled for input, CI, validation and uncertain source conditions. |
| Evidence view expanded every raw CI record | Concise saved PR summary and up to ten evidence entries; full Evidence chooser and Timeline remain available, with explicit count/overflow messaging. |

## Validation and review limits

The full unittest/Textual suite passed 127 tests; targeted workflow checks also cover the final label refinement. Tests include narrow mouse navigation, empty-state recovery without draft creation, cached navigation without network/resume, context-appropriate PR actions, priority/duplicate-card behavior, unavailable checkouts and offline feedback inspection without sending. Compilation and tracked whitespace checks passed. The live refresh completed successfully (module log 982, code 0), and its saved successful dock projection was inspected.

The initial empty-editor regression test switched tabs programmatically while focus remained on an Issues input, which switched the active pane back; the second click then targeted a hidden button. The regression check now uses actual tab clicks and passes. This is not described as a fixed arbitrary Textual focus bug. A separate temporary string-escaping error caught during implementation was corrected before final compilation and suite validation.

Native Luvus was not discoverable as a standalone app through the desktop UI tool, so there is no desktop screenshot claim. The running console was not forcibly restarted; reopen it to load the changed tab/layout code. Sidebar hooks load current source per invocation and the refreshed sidebar uses the new layout.

## Follow-up fixes — 2026-09-06

- GitHub review comments now inherit resolved/outdated state from paginated GraphQL review threads, including replies. Resolved comments disappear from outstanding feedback; outdated alone does not mean resolved. Missing/partial thread data preserves prior evidence as stale. The provider contract follows https://docs.github.com/en/graphql/reference/pulls#pullrequestreviewthread.
- Added a native startup PR/CI reader independent of the console, sharing the existing cache, polling deadlines and notification reservations. An OS lock prevents duplicate readers and releases on crash. The reader checks module enablement/root and server generation before polling and repainting. The selected live session owns the watcher lock; a duplicate startup exited cleanly. In-flight provider calls remain serial and may delay shutdown/refresh.
- Fixed polling and scoped sidebar handling for unprepared drafts with a null checkout. A 250-draft regression check verifies three visible cards, an accurate View all link and preservation of the original records.
- Removed the user-selected `haha` connection and its issue cache; retained handovers/evidence/activity. Bookulum already explicitly selected `github`.
- Fixed the sandbox probe for Codex versions requiring `--permission-profile`. The real macOS probe now passes checkout/sibling write-denial checks. Reviewer launch remains fail-closed; no actual reviewer was launched.

Validation: full suite passed 131 tests in 36.731 seconds; after the final draft-history fix, 40 workflow tests passed in 6.943 seconds. Compilation and manifest parsing passed. The Tasks module registration was refreshed to activate the startup command; existing console panes were preserved. The live Bookulum refresh returned GitHub HTTP 404, retained as an error; a successful authenticated PR/thread response is not claimed.

## Remaining gaps

- Native dock rows provide text/dot/menu presentation, not responsive cards or rich tooltips. Long paths and full source errors remain in the console; large real-world histories and platform-specific desktop behavior need further UI validation.
- The invalid legacy `gh` scope still needs the user’s repository/disable choice. Discussion comments and review summaries have no thread-resolution state; feedback counts remain observations rather than a count of actionable requests.
- Actual reviewer execution and Windows/Linux sandbox enforcement remain unverified. Native agent scheduling remains unavailable; the PR/CI reader does not schedule coding work.

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


## Remaining busy guard paths — 2026-09-06

Evidence, Next attention, expanding/opening attention, Copy and Browser now use the navigation worker instead of the mutation busy guard. Sidebar copy and task-evidence routes use the same classification. Start / Continue (and Handover) resolves a single existing unapproved draft to the edit route before checking the busy guard. Launch/publication and other mutation workflows remain serialized. An isolated interaction test holds the busy flag throughout evidence navigation, draft opening, next-attention navigation and copying, and verifies it is not cleared by those operations. A fresh console was opened in pane 23; older panes were preserved and still need reopening to load new Python code.


## Empty base branch on Start — 2026-09-06

Default-base detection no longer returns empty immediately when origin/HEAD is absent. It honors a configured available branch, then origin/HEAD, then an unambiguous main/master branch (preferring its origin ref). Ambiguous or unconventional defaults still require an explicit choice. Opening a saved draft with an unset, non-manual base suggests this default asynchronously; user-entered or deliberately cleared values are preserved. Target validation rejects a blank task/base branch with an actionable message before Git revision resolution. No refs are fetched or changed by detection.
