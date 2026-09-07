> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Luvus AI Usage

Five configurable Luvus Bar widgets for **Codex, Claude Code, OpenCode, Muse Code, and GitHub Copilot**. Only Codex is enabled by default. Click a widget for quota windows, exact reset times, source freshness, and local statistics.

Example (illustrative):

```text
Codex 5h 24% used · resets in 2h 10m
```

The compact representation preserves the percentage or token count and reset countdown. Luvus handles theme colors, top/bottom placement, and narrow-window overflow. Account limits and context-window occupancy are different measurements and are displayed separately.

The Codex widget prefers the main Codex allowance. Spark and other separate model limits remain in details; they are used in the bar only when no main Codex allowance is returned.

## Install

Requires **macOS**, **Node.js 22.12+**, and **Luvus 0.13.4+**. Individual tools are optional; missing ones show setup guidance and can be hidden. Copilot uses the pinned official SDK and your installed Copilot runtime, not an automatically downloaded runtime.

```sh
cd /path/to/original-module
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run doctor
```

Then, **inside the intended Luvus pane**:

```sh
node /path/to/original-module/cli.mjs install
```

Installation checks the inherited binary, socket and pane, reads live capabilities/schema, and links only this module. It does not select another server, restart Luvus, change login credentials, or start agent turns. If those variables are missing, run the command in a normal terminal pane belonging to the desired Luvus session.

For a separately distributed checkout, the equivalent native command is `luvus module link /absolute/path/to/luvus-ai-usage` after installing dependencies. The module ID is `kacper.ai-usage`.

## Configure

Use **Settings → Modules → AI Usage**:

| Setting | Default | Behavior |
|---|---|---|
| Show each tool | Codex on; others off | Disables that collector and clears its widget when off |
| Executable per tool | Tool name on PATH | Accepts an executable path, including paths containing spaces; not a shell command |
| Bar priority per tool | Codex 90; others 60 | Higher-priority widgets resist overflow longer |
| Quota display | Used | Switch to percentage remaining |
| Bar density | Standard | Compact shortens usage labels; reset countdowns remain visible |
| Account refresh | 5 minutes | Configurable 1–60 minutes; failures back off to at most one hour |
| Local period | 7 days | Select sessions active within 1–365 days; totals remain session-lifetime totals |
| Warning / critical | 80% / 95% | Percentage **used**, regardless of display mode; critical cannot be below warning |
| Notifications | Off | Once per severity per quota window, including across helper restarts |

Use **Settings → Layout → Luvus Bar** to place each widget at Top, Bottom, or Off. Placement Off hides a widget; the module’s provider toggle stops its collector. Preferences survive reinstalls.

The helper updates countdowns locally every minute. It checks settings/module health every five seconds, responds to refresh actions, and debounces agent events. Polling never fabricates new Claude quota data: that feed updates only when Claude publishes a status line.

Notifications follow the widget's selected quota window. A warning can escalate to critical; critical suppresses subsequent lower warnings for that window. Errors, stale periods, usage dips, and helper restarts do not rearm delivery. Unlimited entitlements, missing/invalid usage, and expired windows cannot alert. Turning notifications off does not consume alerts or erase previous deliveries.

Delivery history is saved in `sessions/<endpoint-hash>/notifications.json` under the module state directory, keyed by provider, available account discriminator, window ID, and observed reset time. Unknown resets remain deduplicated until a different identity is observed; elapsed time alone never rearms an alert. Providers without an account discriminator cannot distinguish account changes with identical window identifiers. History is local to each Luvus session and retained without automatic pruning.

Failed notification delivery is retried on a later refresh. A save failure retains delivery history in memory and pauses further notifications until saving succeeds. Unreadable or malformed history pauses notifications for that helper run while widgets keep working; diagnostics go to `helper.log`. Repair the state before restarting the helper through your normal module workflow. A crash between notification acceptance and saving its history can still repeat delivery.

Click a widget to open **Usage details**. Inside details:

- **1–5**: select an enabled provider; **↑/↓**, **j/k**, **Page Up/Page Down**, **Space**: scroll.
- **r**: refresh; **c**: connect Claude’s status line when viewing Claude; **q / Escape**: close.

The module adds no right-click menu entries. Refresh and optional Claude setup remain available through the CLI:

```sh
luvus module run kacper.ai-usage refresh
luvus module run kacper.ai-usage claude-connect
luvus module run kacper.ai-usage claude-disconnect
```

## Provider compatibility

| Provider | Supported data | Collection and limitations |
|---|---|---|
| Codex | All returned quota windows/reset timestamps, plan/credits/earned resets, daily and lifetime activity, streaks, local tokens/cache/reasoning/context | Official `codex app-server` account reads; local token-only projections from paths returned by `thread/list`. Current CLI 0.153.4 live-tested. API-key-only authentication may not expose ChatGPT quota/activity. |
| Claude Code | Reported 5-hour/7-day/gateway limits, model, context, tokens, prompt cache, session cost/duration/lines changed | Official status-line JSON via a reversible wrapper. Quota fields require a compatible version and eligible account, after an API response. No independent background quota endpoint. Current official docs identify v2.1.251+ for those fields. |
| OpenCode | Root-session input/output/reasoning/cache tokens, recorded cost and models | `session list --format json` and `export`. CLI listing excludes child sessions. No OpenCode account quota is inferred. |
| Muse Code | Session-lifetime token totals, cache/reasoning, model/context, cost estimate when sufficient counters and catalog prices exist | Read-only MSP `session/list`, `view/page`, `model/list`; no session resume or write lease. Muse 1.0.3 live-tested. No verified supported account quota/reset interface. |
| Copilot | All returned entitlement categories, usage/remaining/reset, unlimited/overage state | Official SDK `account.getQuota`, current login. SDK 1.0.13 pinned. Session events from other running CLI processes are not accessible on this connection; experimental accumulated-session RPCs are not used. |

When supported by the running server, details also show **Luvus Mission Control’s cached live-pane statistics**. These remain separate: Mission Control does not expose native session IDs for reliable cross-source deduplication, so its numbers are never added to local session totals. Its original observation time is unavailable.

Unknown, zero, unlimited, estimated and stale are distinct states. A passed reset says **awaiting update**, not “0% used.” A transient failure preserves the previous sample with a stale marker; sign-in failure clears the old account values. Account changes replace snapshots rather than adding allowances.

Local session totals cover sessions active in the selected period, including their earlier calls. They are **not exact calendar-period spend**. Subscriptions, API-equivalent costs, catalog estimates and Copilot request units are not interchangeable. Muse costs use current catalog prices and may differ from historical invoices. Cache and reasoning tokens follow each provider’s accounting convention instead of being blindly added.

Muse per-model token totals are omitted when any contributing event lacks a valid count. Explicit zero remains zero; the separately reported cumulative session total is preserved.

Bounds: 200 recent Codex/Muse sessions; 100 OpenCode root sessions; 10,000 view events per Muse session; last 2 MiB of each Codex transcript for the latest cumulative token event; 60-second collector deadline. Truncation/failed session reads are labeled. Missing cost inputs do not become zero dollars.

## Claude connection and removal

Connecting installs a wrapper in `CLAUDE_CONFIG_DIR/settings.json` (normally `~/.claude/settings.json`). It retains the previous status-line object and passes the original input/output through unchanged. Only a normalized usage projection is saved. No transcript text, prompts, API keys or OAuth tokens are copied into module state.

To remove the module **and restore the previous Claude status line**:

```sh
luvus module run kacper.ai-usage uninstall
```

This unlinks the module without deleting your source checkout. If the status line was changed externally, restoration refuses to overwrite it; resolve that change before unlinking. Directly running `luvus module unlink` bypasses Claude restoration, so disconnect Claude first.

To pause:

```sh
luvus module disable kacper.ai-usage
luvus module enable kacper.ai-usage
```

The helper exits within one health-check cycle after disable/unlink/server exit, stopping its collector processes. Each Luvus session gets a distinct helper/cache. Claude’s normalized feed is shared within this module’s state root. Disabling pauses the helper but leaves the optional Claude wrapper connected until explicitly disconnected.

## Troubleshooting and validation

- Run `npm run doctor` for executable versions and missing session identifiers.
- Run `luvus module log kacper.ai-usage` for startup/action errors. Background diagnostics live under the module state directory’s `sessions/<endpoint-hash>/helper.log`.
- Missing stats: use the tool’s normal login, check version compatibility, or connect Claude. No login is initiated automatically.
- Existing settings changed: use the native settings UI and then Refresh; no restart is needed.
- `npm test` covers accounting, missing/expired/unlimited/stale states, persistent notification suppression and failure recovery, reversible Claude configuration, sanitized output, protocol failures, and helper lifecycle using a simulated Luvus server.
- `npm run preview` prints an illustrative compact bar without contacting providers.

Earlier live provider verification covered Codex and Muse, successful module linking/startup, and Codex bar publication. Claude, OpenCode and Copilot use fixtures and documented contracts; authenticated live checks require those tools. The 2026-09-06 alert/Muse fixes were verified offline only; current registration, settings, server capabilities, and interactive behavior were not rechecked. Other providers are hidden by default.

Primary references: [Luvus modules](https://luvus.dev/docs/extend/writing-modules/), [Luvus Bar](https://luvus.dev/docs/guides/bar/), [Codex app-server](https://learn.chatgpt.com/docs/app-server), [Claude status line](https://code.claude.com/docs/en/statusline), [OpenCode CLI](https://opencode.ai/docs/cli/), [Copilot usage](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/usage-and-billing). Muse’s reference is the schema embedded in its installed binary (`muse schema generate-json-schema`).

Module tabs use short descriptive names with a small symbol. New agent tabs include their task or instruction. Existing manual names are preserved; background tabs receive their name when focused. Naming is best effort and never retries an agent launch.
