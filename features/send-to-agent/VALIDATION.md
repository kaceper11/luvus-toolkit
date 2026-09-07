> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Validation

Validated locally on macOS, Python 3.11.16, Textual 6.12.0, and installed Luvus 0.13.4.

- 17 unit/headless UI tests passed. They cover exact Unicode/multiline capture, reviewed existing/new delivery, target disappearance/replacement, startup failure, timeout persistence, duplicate submissions, file ranges, staged/unstaged/deleted/untracked files, literal Git paths, invalid/binary/symlink paths, snapshot changes, session routing, cancel/save, and review invalidation.
- Real Luvus smoke uses a disposable home and synthetic Codex executable: manifest loads, composer renders, each new/existing prompt is received once, source content remains unchanged, and the open action preserves its selection payload. The disposable server is stopped afterward.
- Context and Review tabs were rendered and visually inspected at 100×38. Headless interaction also ran at 80×30.
- Live capabilities and schema were inspected in an isolated session. Native file/DIFF selections use the composer fallback; terminal selection uses the existing `LUVUS_MODULE_CONTEXT_JSON.selection` contract.

No real Codex/Claude/other provider conversation was launched. Native Windows/Linux and an actual desktop right-click gesture were not tested. The selection action was tested with its captured menu payload. The production Luvus session was not modified: the implementing terminal had `LUVUS_ENV=1` without the required inherited binary/socket/pane fields. Run `luvus module link .` from the intended Luvus session to make the action available there.

## Checkout guards — 0.2.0

19 unit/headless tests passed, including branch drift, worktree-directory replacement, missing approval, dirty checkout and intentional cross-repository behavior. Production registration was updated; both open composers saved and reopened the same draft IDs. The new source/recipient badges rendered in the live composer. No prompt was sent to a real agent during this rollout.

2026-09-06 simplified composer and tab names: 22 unit tests passed. The disposable native Luvus smoke passed composer rendering, new/existing synthetic-agent delivery exactly once, captured text fidelity, background tab naming, manual-name preservation and unchanged checkout. No model service was used. The 90x36 form was visually inspected; the recipient and Send control are visible.
