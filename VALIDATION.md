# Validation

## macOS

- 375 Python unittest checks passed across the feature suites and toolkit migration/transport checks.
- 39 Node checks passed, including worker lifetime, quotas, status-line preservation, native locks and namespace routing.
- An isolated Luvus 0.13.4 server accepted the root manifest. Tasks, Project Commands and Send to Agent panes opened; startup/event/module command logs had no failures.
- A fresh installation from public commit `5b9df1f` passed its native build and doctor checks in a disposable home.
- The native power assertion helper expired after parent exit within 40 seconds; macOS also reported the owned PreventUserIdleSystemSleep assertion.
- Read-only authenticated Codex collection returned three quota windows and 50 recent local sessions. Muse collection returned 11 local sessions. OpenCode and Copilot executables were unavailable on this workstation; no credentials were transferred to CI.
- Real durable configuration copied successfully to an isolated destination, with known Launcher connections rewritten to Toolkit. Production cutover remains pending.
- Original module checkouts and live registrations have not been removed or replaced.

## Cross-platform CI

- macOS and Linux test matrices have passed.
- Native Windows power assertion lifetime passed with the standard-library Windows API helper.
- A fresh native Windows installation from the public repository passed its build, doctor, Git refresh, Tasks/Commands/Send pane checks, module logs and shutdown cleanup on commit `ce2fc39` ([run](https://github.com/kaceper11/luvus-toolkit/actions/runs/34110731917)).
- The full Windows Python suite remains under validation. The first run with a sufficient time budget completed all 239 Tasks tests with one batch-confirmation UI readiness failure; the targeted test was corrected and passes locally. The other feature suites and all 39 Node checks passed.
- Full WSL2 and cross-platform authenticated-provider acceptance remain pending. The manual WSL2 workflow installs a disposable distribution and validates its actual kernel before testing.

## Required before final cutover and deletion

- Fresh WSL2 installation, real-Luvus smoke checks, paths/quoting, process shutdown and host power assertion expiry; complete Windows regression checks.
- Authenticated checks of each optional provider on the intended platforms. Unsupported upstream tool behavior must be resolved or reported as a blocker, not silently hidden.
- Preview/apply/readback of existing durable state, credential references and external Claude wrapper restoration/reconnection.
- Verify remote backups, unique branches/worktrees and repository metadata before deleting the six old remotes and seven old feature directories.

Platform declarations describe implementation targets, not completed acceptance. Hosted Linux checks alone do not establish WSL2 behavior or physical power-management correctness.
