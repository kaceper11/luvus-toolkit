# Validation

## macOS

- 375 Python unittest checks passed across the feature suites and toolkit migration/transport checks.
- 39 Node checks passed, including worker lifetime, quotas, status-line preservation, native locks and namespace routing.
- An isolated Luvus 0.13.4 server accepted the root manifest. Tasks, Project Commands and Send to Agent panes opened; startup/event/module command logs had no failures.
- A fresh installation from public commit `5b9df1f` passed its native build and doctor checks in a disposable home.
- The native power assertion helper expired after parent exit within 40 seconds; macOS also reported the owned PreventUserIdleSystemSleep assertion. Windows/WSL2 host checks remain pending.
- Real durable configuration copied successfully to an isolated destination, with known Launcher connections rewritten to Toolkit. Production cutover remains pending.
- Original module checkouts and live registrations have not been removed or replaced.

## Required before final cutover and deletion

- Repeat fresh installation on Windows and WSL2, including the native build step.
- Native Windows and WSL2 real-Luvus smoke checks, paths/quoting, process shutdown and host power assertion expiry.
- Authenticated checks of each optional provider on the intended platforms. Unsupported upstream tool behavior must be resolved or reported as a blocker, not silently hidden.
- Preview/apply/readback of existing durable state, credential references and external Claude wrapper restoration/reconnection.
- Verify remote backups, unique branches/worktrees and repository metadata before deleting the six old remotes and seven old feature directories.

Platform declarations describe implementation targets, not completed acceptance. Hosted Linux checks alone do not establish WSL2 behavior or physical power-management correctness.
