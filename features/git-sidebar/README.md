> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Git Sidebar for Luvus

A clickable Git sidebar and accent-colored top-right toolbar, with Lazygit for advanced operations.
Requires Luvus 0.13.4+, Git, Python 3.9+, and Lazygit. Message generation uses
your existing Codex login and configured model. No Python packages are needed.

## Install

From a shell targeting your intended Luvus session:

```sh
brew install lazygit
luvus module link /path/to/original-module
```

The module declares its dock on the right. If that sidebar is hidden, enable it
in Luvus Settings → Layout. Module commands use the inherited binary and socket;
they never silently connect to another session.

## Use

- The top-right toolbar shows the repository, changed/staged counts, and a
  spaced **Review changes** button. It never stages, commits, pulls, or pushes.
  Clean repositories show **Working tree clean**. Narrow layouts omit the
  repository name first. Bar placement remains configurable through Luvus.
- **Review changes** expands the relevant file group and opens the selected
  file's native diff (or the first changed/staged file). Use **Stage selected
  file** or the file's context menu to stage only that file. Bulk Git operations are available in Lazygit.
- **More actions → Review branch…** reviews committed branch changes even when
  the working tree is clean. Select a local or remote-tracking base; `origin/HEAD`
  is suggested first when available. Nothing is fetched automatically.
  The pane shows the repository, branch, base commit, merge base, reviewed HEAD,
  changed-file count, added/deleted text lines, and binary-file count. Staged,
  unstaged, untracked, and conflicted local files are counted separately and
  excluded from the comparison. Press `v` for the patch, `b` to select another
  base, or Enter to close. `q` leaves a long patch's pager.
  Summary and patch use the same pinned merge-base → HEAD comparison; later
  commits cannot change it. Reopen review for current changes. This screen
  never stages, commits, sends feedback, or modifies the checkout.
  Detached HEAD is supported; unborn HEAD, unrelated histories, and multiple
  merge bases show an explanation. Binary files have counts, not text patches.
- Files and actions are grouped into **Changed files**, **Staged files**,
  with the primary controls and expandable **More actions** above the files.
  Arrow-and-bracket labels identify clickable actions.
  File dots use the theme's working/staged colors, with letter status markers.
  Click a group heading to collapse it. Lists show four files per page; use
  **Next files** or its right-click menu to navigate. **More actions** has a
  right-click menu for currently available Git operations.
- Outside a Git repository, the sidebar shows an empty state with guidance and
  **Open repository…** and Refresh. Open repository shows clickable existing
  Git workspaces directly in the sidebar; click a project to focus it. **Back**
  closes the chooser. **Enter another path…** opens the optional text prompt for
  a repository that is not already a workspace, with validation and confirmation.
  It never initializes or clones repositories.
- Click a file to preview its diff and full path without changing Git. Use the
  selected-file action in the sidebar or right-click **Stage/Unstage file**.
  File clicks use Luvus's native diff preview with the exact staged, worktree,
  or untracked layer. If unavailable, right-click **Text preview (fallback)**;
  press `q` to leave its pager, then Enter to close or `s`/`u` to stage/unstage.
- **Commit N files…** opens a guided screen listing staged files and the saved
  message. Press `g` to generate from the staged diff, `e` to open `$VISUAL`,
  `$EDITOR`, or nano, `c` to commit, or Enter to cancel. In nano, Ctrl+O saves
  and Ctrl+X exits. Drafts survive cancellation and failure, separately per
  worktree. Changed staged contents require another review through Edit or
  Generate. Push remains a separate action.
- Generation uses your configured Codex provider and login. Diffs above 200 KB
  require a smaller commit or a manually written message. Existing drafts
  survive generation failure. Progress is shown without raw Codex logs.
- **More actions…** expands eligible Git commands immediately below the controls. Conflicts get their own section and a **Resolve in Lazygit** action.
- **Changes**, **Branches**, **Commits**, and **Stashes** open the corresponding Lazygit view in a named tab. Repeated clicks reuse the same session, checkout and view without resetting its selection. Startup checks the actual Lazygit process.
- Pull, push, merge and stage-all remain callable by their legacy action IDs but are omitted from the visible shortcuts; use Lazygit for these operations.

Operations retain ordinary Git hooks, signing and credential helpers. They never
automatically force-push, reset, stash, resolve conflicts, or retry failed commands.
Successful operations close their temporary pane. Failures stay open for review.
Lazygit can remain open while you use the sidebar. The sidebar and toolbar show persistent working/result indicators, including
loading repositories, opening a diff, staging, and generating messages. Click a
finished status row to dismiss it. Refresh checks whether a working process still
exists, so an exited process does not leave a permanent working indicator.
Notifications supplement these indicators. Failure to display feedback never
changes a Git result.

Both surfaces refresh from the live active workspace after sidebar operations and supported Luvus events. **Luvus
0.13.4 does not emit a focus event for every workspace switch**, so use **Refresh**
or right-click the workspace → **Refresh Git sidebar** if needed. A stale
workspace action refreshes the dock and asks you to select the action again,
without modifying either repository. The session watcher described below handles workspace switches.

## Verification

```sh
python3 test_git_sidebar.py
```

This uses temporary repositories and a local bare remote. It covers unusual
filenames, renames, staging, linked worktrees, cancellation, concurrent staged
changes, mocked AI success/failure, push/pull/merge, conflicts, and pane handoff.
It does not contact external Git servers or invoke a real AI model.
Branch-review checks additionally cover pinned revisions, an advancing base,
binary files, renamed paths containing tabs/newlines, local-change exclusion,
cancellation, detached/unborn HEAD, invalid comparisons, and secure paging.

Version 0.7.0 was checked on macOS with the disposable-repository suite and a
disposable live Luvus workspace: base selection, summary, patch display, and
closing the review were observed, with a clean checkout afterward. Linux
interactive behavior has not been verified. No new cross-module interface,
cleanup, checkpoint storage, or validation display is included.

## Troubleshooting and removal

```sh
luvus module info personal.git-sidebar
luvus module log personal.git-sidebar
luvus module disable personal.git-sidebar
luvus module unlink personal.git-sidebar
```

Disabling removes the dock and stops new hooks. Unlinking preserves these source
files. Runtime files live in the state directory Luvus supplies, outside your
project repositories.

An interrupted pane launch can leave `pending-*.json` in that state directory.
Inspect the pending action and any open Git pane before removing that one file
to clear the launch guard. A restored pane never replays a consumed action.
Commit drafts are stored under `draft-*/message.txt` in that state directory.
Incoming/outgoing counts reflect the last fetch; **Check & pull changes** checks
the remote even when the displayed incoming count is zero.

The stock module dock supports colored status dots and menus, but not arbitrary
button backgrounds or per-row bold styling. The toolbar uses native accent-colored
badges. File/conflict lists remain paginated because the dock clips long lists.

## Reload and rollback

After a manifest change, disable, unlink, and link this module again (do not restart
the Luvus server). Let the old watcher exit before linking. Keep its state directory
to preserve drafts. Version 0.4.0 also
accepts the previous view-state files and old pending preview requests.
To roll back, restore the saved source files and relink the module; leave drafts
in place. Source backups are outside the project under
`~/.local/state/luvus-git-sidebar-backups/`.

Version 0.6.0 stores Lazygit pane references per repository/session and checks
terminal identity before reuse. Closed panes are replaced on the next click.
Existing Git panes from older versions are left untouched because they may hold
unfinished operations or drafts. Guided commit/path-entry panes are explicitly
focused after launch too.

## Workspace refresh (0.6.1)

The restored Git sidebar checks the active workspace once per second because Luvus 0.13.4 does not emit a workspace-switch event. It repaints only when that path changes; existing Git events still refresh file status. One session-scoped watcher runs while the module is enabled and exits on disable, unlink, version change, or server disconnect. After relinking, disable/enable the module to run its startup hook.

## Context menu hub (2026-09-06)

Workspace right-click **Git…** directly opens or focuses Lazygit Changes for the clicked checkout. The sidebar retains separate Changes, Branches, Commits and Stashes shortcuts. Opening validates the captured checkout identity.

## Checkout awareness (0.8.0)

The toolbar now includes branch and local ahead/behind counts; the right sidebar includes a pane-by-pane checkout summary alongside descriptive module tab names. Status collection deduplicates identical checkout roots. Commit review shows the checkout and local-ref counts. The staged approval additionally pins the Git worktree directory identity, so a replacement checkout cannot reuse an earlier approval. Older saved commit messages remain intact; refresh their review through Edit or Generate. Counts never trigger an automatic fetch.

Validation: the disposable Git regression scripts passed staging, unusual paths, worktrees, commit guards, branch comparison, push/pull/merge/conflict handling and pane handoff; the menu unittest also passed. Production registration was updated and the sidebar refreshed. No real repository commit was made by this rollout.

Module tabs use short descriptive names with a small symbol. New agent tabs include their task or instruction. Existing manual names are preserved; background tabs receive their name when focused. Naming is best effort and never retries an agent launch.
