> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Luvus Keep Awake

A standalone macOS Luvus module. Prevents idle sleep while any agent in the
selected session is `working`, across all workspaces. The screen can still sleep.

Click the bottom-right **Awake** widget to cycle **Auto → On → Off → Auto**.
On is a manual override; Off allows normal sleep. Both reset to Auto when the
helper restarts. Status refreshes every five seconds, plus command latency.

On battery, sleep prevention stops at **20%** or below, even in On mode. It resumes
when connected to power or above 20%. Idle, done, blocked, and unknown agents do
not keep the Mac awake in Auto mode. No system-wide power settings are changed.

## Install

Requires macOS, Node.js 22.12+, and Luvus 0.13.4+. From the intended Luvus pane:

```sh
luvus module link /path/to/original-module
luvus module info kacper.keep-awake
luvus bar list
```

The module requires Luvus's injected binary, socket, module identity, state,
and config directory variables. It never falls back to another session.
No npm install or build step is needed.

## Sleep and lid behavior

Uses `/usr/bin/caffeinate -i -t 30`, renewing after 20 seconds with fresh agent and
power readings. Disablement, shutdown, low battery, and Off release its own
assertion. Crashes leave at most the remaining 30-second assertion lifetime.
Missing/malformed observations release prevention and show **Awake: unavailable**.
Other apps' assertions are unaffected; release does not force immediate sleep.

Closed-lid use requires Apple's documented docked setup: power, external display,
keyboard, and mouse. This module does not override lid-triggered sleep or enable
closed-lid operation with only a charger. See
[Apple's guidance](https://support.apple.com/en-us/102501).

## Verify and remove

```sh
npm test
pmset -g assertions
luvus module log kacper.keep-awake
luvus module disable kacper.keep-awake
```

The helper uses macOS `lockf` to prevent duplicate workers and stores per-session
mode, diagnostics, and its log under `LUVUS_MODULE_STATE_DIR`. After disablement,
allow a polling interval for cleanup. Use `luvus module unlink kacper.keep-awake`
to unregister it. No agents are stopped by disabling or removing this module.

### Validation performed

- All 10 `npm test` checks pass, including battery boundaries, native locks,
  duplicate helpers, session replacement, and shutdown during a pending read.
- Real macOS assertions were observed with `pmset -g assertions`: creation,
  release, and expiry within 30 seconds after a helper was killed.
- Concurrent CLI starts and Auto/On/Off cycling passed against a temporary
  simulated Luvus session. The TOML manifest parses successfully.
- Live Luvus registration, widget rendering, and physical lid closure remain
  untested: the implementation environment did not supply its session endpoint.
