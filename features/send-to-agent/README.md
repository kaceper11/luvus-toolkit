> Historical standalone-module documentation. Use the [Toolkit README](../../README.md) for current installation, identity and validation.

# Send to agent

A standalone Luvus module for sending a quoted response, a file change, or selected code lines to an existing agent or a fresh conversation. No Tasks or tracker connection is required.

## Setup

Requires Python 3.11+, Git, Luvus 0.13.4+, and at least one installed agent CLI for new conversations.

```sh
cd /path/to/original-module
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
luvus module link .
```

Use your installed Python 3.11+ interpreter if its executable has another name. This checkout already has a prepared `.venv`. Module entrypoints automatically use it, even if `python3` on PATH is older.

Right-click a terminal pane, agent, or workspace and choose **Send to agent…**. Selecting terminal text before right-clicking captures that exact selection. To open a blank composer:

```sh
luvus module pane open personal.luvus-send-to-agent composer
```

Windows entrypoints use `python` and `composer-windows`; create the environment with `py -3.11 -m venv .venv` and install using `.venv\Scripts\python.exe -m pip install -e .`. Native Windows is not verified. WSL uses the Linux entrypoints.

## Compose and send

1. **Context** shows the captured response immediately. Add pasted text or choose a repository file. Select **File content**, **Unstaged change**, or **Staged change**. Optional file line ranges are 1-based and inclusive; a first line alone captures that one line. Changes include the entire diff for the chosen file. For an untracked file, choose File content.
2. **Instruction & recipient** lets you describe what the agent should do. Existing agents show their name, provider, status, and checkout; source-checkout agents appear first. **New agent** starts a fresh conversation in the source checkout, defaults to Codex when available, and lists other installed, host-supported providers. Existing agents sharing the checkout are shown before launch.
3. **Send** submits directly after checking the instruction, context and recipient. Pasted text is included automatically. **Preview** is optional; validation errors appear beside the form, and **Refresh / Retry** reloads available agents.
4. **History** restores locally saved drafts and shows delivery outcomes. **Save & close** (Ctrl+Q) saves without sending. Ctrl+R previews. Tabs, fields, and buttons are keyboard accessible.

Luvus 0.13.4 does not advertise a native file/DIFF line-selection API in the inspected contract. Use the composer's file/change/line picker for those views. Terminal selections are quoted text: the module does not infer response boundaries, file paths, or line numbers from terminal output.

Existing agents in another checkout receive source snapshots and paths; files are not copied. New agents share the source checkout, including its uncommitted changes. The module does not create branches/worktrees, commit, stash, reset, or modify source files. Agent actions after receiving your instruction are controlled by that agent.

## Delivery and recovery

Drafts, selected context, outgoing messages, and outcomes are stored in `drafts.sqlite3` under `LUVUS_MODULE_CONFIG_DIR`, normally `~/.luvus/modules/config/personal.luvus-send-to-agent`. Each menu invocation creates an independent draft. Saved drafts retain their originating Luvus binary/socket/home/session routing; they never switch to the currently focused agent.

Before delivery the module checks the selected file snapshots and agent identity: server generation, terminal ID, pane, provider, name, checkout, and the session ID when Luvus exposes one. A missing or changed target requires reselection. If the host does not expose a session ID, an otherwise invisible conversation reset within the same terminal cannot be independently detected. UHP 0.13.4 also has no compare-and-prompt identity fence; identity checks happen immediately before the atomic prompt call.

A timeout or failure after submission starts leaves a **pending/uncertain** outcome. There is no automatic retry or second launch. Open History, load that record, and inspect its target name/pane. After resolving the operation manually, check the acknowledgement and choose **Close uncertain outcome without retry**. This records your resolution; it does not send anything or mark the original message delivered. **Delivered** means Luvus accepted the prompt, not that the agent finished the work.

Binary/non-UTF-8 content is rejected. Files above 2 MB require a pasted excerpt; included context and the complete message are limited to 200 KB. Changed files must be removed and recaptured before sending. Context text is stored locally in plaintext and only sent to the explicitly chosen agent.

## Development checks

```sh
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python tests/smoke_luvus.py
```

The smoke test uses an isolated Luvus home and a synthetic `codex` executable. It checks real module loading, composer rendering, new/existing agent transport, exact captured selection, and unchanged checkout content without contacting a model service. See [VALIDATION.md](VALIDATION.md) for the tested boundaries.

## Checkout awareness (0.2.0)

Source and recipient details show the branch, dirty/staged/conflict state and ahead/behind counts from local upstream refs. Send captures both worktree identities, including directory identity and branch, then rechecks them immediately before delivery. A changed branch, replaced directory or recipient blocks that attempt with an error. Dirty or behind state is advisory, and intentional cross-repository sends remain supported. No automatic fetch occurs. Older drafts are preserved.

Module tabs use short descriptive names with a small symbol. New agent tabs include their task or instruction. Existing manual names are preserved; background tabs receive their name when focused. Naming is best effort and never retries an agent launch.
