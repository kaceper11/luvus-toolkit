# Validation

## macOS

- 374 Python unittest checks passed across the feature suites and toolkit migration/transport checks.
- 38 Node checks passed, including worker lifetime, quotas, status-line preservation, native locks and namespace routing.
- An isolated Luvus 0.13.4 server accepted the root manifest. Tasks, Project Commands and Send to Agent panes opened; startup/event/module command logs had no failures.
- Original module checkouts and live registrations have not been removed or replaced.

## Required before final cutover and deletion

- Fresh installation from the published repository, including its native build step.
- Native Windows and WSL2 real-Luvus smoke checks, paths/quoting, process shutdown and host power assertion expiry.
- Authenticated checks of each optional provider on the intended platforms. Unsupported upstream tool behavior must be resolved or reported as a blocker, not silently hidden.
- Preview/apply/readback of existing durable state, credential references and external Claude wrapper restoration/reconnection.
- Verify remote backups, unique branches/worktrees and repository metadata before deleting the six old remotes and seven old feature directories.

Platform declarations describe implementation targets, not completed acceptance. Hosted Linux checks alone do not establish WSL2 behavior or physical power-management correctness.
