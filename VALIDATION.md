# Validation

## macOS

- 375 Python unittest checks passed across the feature suites and toolkit migration/transport checks.
- 39 Node checks passed, including worker lifetime, quotas, status-line preservation, native locks and namespace routing.
- An isolated Luvus 0.13.4 server accepted the root manifest. Tasks, Project Commands and Send to Agent panes opened; startup/event/module command logs had no failures.
- A fresh installation from public commit `5b9df1f` passed its native build and doctor checks in a disposable home.
- The native power assertion helper expired after parent exit within 40 seconds; macOS also reported the owned PreventUserIdleSystemSleep assertion.
- Read-only authenticated Codex collection returned three quota windows and 50 recent local sessions. Muse collection returned 11 local sessions. OpenCode and Copilot executables were unavailable on this workstation; no credentials were transferred to CI.
- Local macOS cutover completed: nine durable files migrated, three SQLite databases passed integrity checks, and repeat migration found no outstanding copies. Known Launcher connections now use Toolkit entrypoints.
- Toolkit is the sole enabled, runnable module. Doctor, Git refresh and usage refresh passed; the Tasks interface loaded the migrated issues and handover history without command-log failures.
- Seven legacy registrations and local source directories, plus six legacy GitHub repositories, were removed after source-hash, Git backup and branch/worktree checks. Private source/state backups and existing project worktrees were retained. No Claude status-line restoration record was present.

## Cross-platform CI

- All macOS, Linux and Windows test matrix jobs passed on commit `a9078f8`, together with the native Windows installation job ([run](https://github.com/kaceper11/luvus-toolkit/actions/runs/34111907854)).
- Native Windows power assertion lifetime passed with the standard-library Windows API helper.
- A fresh native Windows installation from the public repository passed its build, doctor, Git refresh, Tasks/Commands/Send pane checks, module logs and shutdown cleanup on commit `ce2fc39` ([run](https://github.com/kaceper11/luvus-toolkit/actions/runs/34110731917)).
- The Windows suites include all 239 Tasks tests and all 39 Node checks. Subsequent test-harness changes settle layout after readiness checks, retain fatal crash diagnostics and report abnormal subprocess exit codes.
- Full WSL2 and cross-platform authenticated-provider acceptance remain pending. The manual WSL2 workflow installs a disposable distribution and validates its actual kernel before testing.
- The hosted WSL2 runner booted Ubuntu during setup development, but the final workflow and its retry timed out installing the distribution before module tests could run ([run](https://github.com/kaceper11/luvus-toolkit/actions/runs/34111907803)). Run the manual workflow again when distribution setup is available, or validate on an accessible WSL2 computer. This is not a passing module acceptance result.

## Remaining platform acceptance

- Fresh WSL2 installation, real-Luvus smoke checks, paths/quoting, process shutdown and host power assertion expiry.
- Authenticated checks of each optional provider on the intended platforms. Unsupported upstream tool behavior must be resolved or reported as a blocker, not silently hidden.

Platform declarations describe implementation targets, not completed acceptance. Hosted Linux checks alone do not establish WSL2 behavior or physical power-management correctness.
