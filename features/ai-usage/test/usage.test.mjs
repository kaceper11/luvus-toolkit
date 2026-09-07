import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm, mkdir, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { settings, snapshot, bar, countdown, details } from '../model.mjs';
import { codexData, claudeData, opencodeSession, museSession, copilotData, collectClaude, collectOpenCode } from '../providers.mjs';
import { configureClaude } from '../claude.mjs';
import { Rpc, failure } from '../io.mjs';
import { mergeObservation, context, enabledModule } from '../runtime.mjs';

const exec = promisify(execFile);
const now = Date.now(), config = settings();
async function temporary(t) { const dir = await mkdtemp(join(tmpdir(), 'ai-usage-test-')); t.after(() => rm(dir, { recursive: true, force: true })); return dir; }

test('Codex multi-bucket quotas do not duplicate the legacy bucket, and omit absent metrics', () => {
  const bucket = { primary: { usedPercent: 0, resetsAt: Math.floor(now / 1000) + 100 }, planType: 'pro' };
  const s = codexData({ rateLimits: bucket, rateLimitsByLimitId: { codex: bucket, other: { secondary: { usedPercent: 75 } } } });
  assert.equal(s.windows.length, 2); assert.equal(s.windows[0].usedPercent, 0);
  assert.equal(s.windows[1].resetsAt, null);
  assert(!s.metrics.some(m => m.name === 'Lifetime tokens'));
});
test('Codex defaults to the main allowance even when Spark is exhausted', () => {
  const s = codexData({ rateLimitsByLimitId: { codex: { primary: { usedPercent: 25 } }, special: { limitName: 'GPT-5.3-Codex-Spark', primary: { usedPercent: 100 } } } });
  assert.match(bar(s, config).content[0].text, /Codex 25% used/);
  assert.doesNotMatch(bar(s, config).compact[0].text, /Spark/);
  assert.match(details(s, config), /GPT-5.3-Codex-Spark/);
  s.windows[0].resetsAt = now - 1;
  assert.match(bar(s, config, now).content[0].text, /awaiting update/);
  assert.doesNotMatch(bar(s, config, now).content[0].text, /Spark/);
});
test('Countdown rounds upward and never implies a passed window has recovered', () => {
  assert.equal(countdown(now + 7800000, now), '2h 10m');
  assert.equal(countdown(now + 1, now), '1m');
  assert.equal(countdown(now - 1, now), 'awaiting update');
  assert.equal(countdown(null, now), 'reset unknown');
  const s = { ...snapshot('codex', 'test'), windows: [{ usedPercent: 100, resetsAt: now - 1 }] };
  assert.match(bar(s, config, now).content[0].text, /awaiting update/);
  assert.notEqual(bar(s, config, now).content[0].tone, 'error');
});
test('Remaining display, warning thresholds and stale data are distinct', () => {
  const s = { ...snapshot('codex', 'test'), windows: [{ usedPercent: 85, resetsAt: now + 100000 }] };
  assert.match(bar(s, settings({ display: 'remaining' }), now).content[0].text, /15% remaining/);
  assert.equal(bar(s, config, now).content[0].tone, 'warning');
  s.observedAt = now - 3600000;
  assert.match(bar(s, config, now).content[0].text, /stale/);
  assert.equal(bar(s, config, now).content[0].tone, 'muted');
});
test('Copilot unlimited and zero entitlement are not confused; credit units are not dollars', () => {
  const s = copilotData({ quotaSnapshots: { chat: { entitlementRequests: -1 }, premium_interactions: { entitlementRequests: 0, usedRequests: 0, remainingPercentage: 0, overage: 3 } } });
  assert.equal(s.windows[0].unlimited, true);
  assert.equal(s.windows[1].unlimited, false);
  assert.equal(s.windows[1].limit, 0);
  assert.equal(s.metrics[0].unit, 'requests');
});
test('Claude captures only stats, preserves unknown values, and does not count cache twice', () => {
  const s = claudeData({ session_id: 'a', transcript_path: '/secret', prompt: 'PRIVATE', context_window: { total_input_tokens: 100, total_output_tokens: 20, current_usage: { cache_read_input_tokens: 60 } }, rate_limits: { five_hour: { used_percentage: 0 } }, cost: { total_cost_usd: 0 } });
  assert.equal(s.sessions[0].metrics.find(m => m.name === 'Total tokens').value, 120);
  assert.equal(s.windows[0].usedPercent, 0);
  assert(!JSON.stringify(s).includes('PRIVATE')); assert(!JSON.stringify(s).includes('/secret'));
  assert.equal(s.sessions[0].metrics.find(m => m.name === 'Cost').value, 0);
});
test('OpenCode deduplicates messages and uses its normalized cache convention', () => {
  const message = { info: { id: 'm', role: 'assistant', modelID: 'model', providerID: 'provider', tokens: { input: 10, output: 5, reasoning: 3, cache: { read: 20, write: 2 } }, cost: 0.01 } };
  const s = opencodeSession({ info: { id: 'session' }, messages: [message, message] });
  assert.equal(s.metrics.find(m => m.name === 'Total tokens').value, 37);
  assert.equal(s.metrics.find(m => m.name === 'Reasoning tokens').value, 3);
  assert.equal(s.metrics.find(m => m.name === 'Cost').value, 0.01);
});
test('Muse cumulative usage is not summed repeatedly and child rollups are not doubled', () => {
  const event = (cursor, total) => ({ method: 'session/tokenUsage', params: { viewCursor: cursor, modelId: 'm', cumulative: { promptTokens: total - 10, outputTokens: 10, totalTokens: total }, promptTokens: 20, usage: { cachedTokens: 10, outputTokens: 5, reasoningTokens: 2 } } });
  const e = event('1', 30), e2 = event('2', 60);
  const s = museSession({ sessionId: 's' }, [e, e, e2, { method: 'item/completed', params: { viewCursor: '3', item: { usage: { inputTokens: 9999 } } } }], [{ modelId: 'm', cost: { input: '2', output: '4', cached: '1', currency: 'USD' } }]);
  assert.equal(s.metrics.find(m => m.name === 'Total tokens').value, 60);
  const cost = s.metrics.find(m => m.name === 'Cost'); assert.equal(cost.value, 0.0001); assert.equal(cost.estimated, true);
});
test('Muse unknown price or missing cache convention is not presented as free', () => {
  const s = museSession({ sessionId: 's' }, [{ method: 'session/tokenUsage', params: { viewCursor: '1', cumulative: { totalTokens: 12 }, usage: { outputTokens: 2 } } }]);
  assert(!s.metrics.some(m => m.name === 'Cost'));
});
test('Muse per-model tokens preserve unknown, partial, zero and complete totals', () => {
  for (const [counts, expected] of [[[undefined], undefined], [[5, undefined], undefined], [[null], undefined], [[-1], undefined], [[NaN], undefined], [[0], 0], [[5, 7], 12]]) {
    const events = counts.map((totalTokens, index) => ({ method: 'session/tokenUsage', params: { viewCursor: String(index), modelId: 'model', totalTokens, cumulative: { totalTokens: 42 } } }));
    const s = museSession({ sessionId: 's' }, [...events, events[0]]);
    assert.equal(s.metrics.find(m => m.name === 'model · Total tokens')?.value, expected);
    assert.equal(s.metrics.find(m => m.name === 'Total tokens').value, 42);
  }
});
test('Settings clamp invalid thresholds and intervals', () => {
  const s = settings({ 'refresh-minutes': -1, 'period-days': 'bad', 'warning-percent': 90, 'critical-percent': 50, 'codex-enabled': 'false' });
  assert.equal(s['refresh-minutes'], 1); assert.equal(s['period-days'], 7);
  assert.equal(s['critical-percent'], 90); assert.equal(s['codex-enabled'], false);
});
test('Authentication failures clear old account values, transient errors retain stale snapshots', () => {
  const old = { ...snapshot('codex', 'test'), windows: [{ usedPercent: 20 }] };
  assert.equal(mergeObservation(old, { error: 'Timed out — retrying' }).observedAt, old.observedAt);
  assert.equal(mergeObservation(old, { error: 'Sign in using the tool' }).windows, undefined);
});
test('Terminal control sequences are stripped from details', () => {
  const s = { ...snapshot('muse', 'test'), sessions: [{ id: '\x1b[2J', model: '\u202eEvil', metrics: [] }] };
  assert(!details(s, config).includes('\x1b')); assert(!details(s, config).includes('\u202e'));
});
test('Missing tools and errors never expose raw credentials', () => {
  assert.match(failure({ code: 'ENOENT' }), /Not installed/);
  assert(!failure(new Error('bad token SECRET')).includes('SECRET'));
});
test('Missing Luvus context fails closed and unknown module responses are rejected', () => {
  assert.throws(() => context({}), /Missing LUVUS_BIN_PATH/);
  assert.equal(enabledModule({ enabled: false }), false);
  assert.throws(() => enabledModule({}), /Unsupported/);
});
test('Claude connection and removal preserve unrelated settings and renderer bytes', async t => {
  const dir = await temporary(t), file = join(dir, 'settings.json'), root = join(dir, 'state');
  const original = { permissions: { allow: ['Read'] }, statusLine: { type: 'command', command: 'cat', padding: 2 } };
  await writeFile(file, JSON.stringify(original));
  await configureClaude('connect', root, file);
  const connected = JSON.parse(await readFile(file, 'utf8'));
  assert.equal(connected.statusLine.padding, 2);
  const input = '{"session_id":"session","context_window":{"total_input_tokens":5,"total_output_tokens":1}}';
  const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
  const child = spawn(process.execPath, [cli, 'claude-feed', root], { stdio: ['pipe', 'pipe', 'pipe'] });
  let output = ''; child.stdout.on('data', c => { output += c; }); child.stdin.end(input);
  await new Promise(resolve => child.on('exit', resolve)); assert.equal(output, input);
  connected.extra = 'user edit'; await writeFile(file, JSON.stringify(connected));
  await configureClaude('disconnect', root, file);
  assert.deepEqual(JSON.parse(await readFile(file, 'utf8')), { ...original, extra: 'user edit' });
  const feed = await collectClaude(config, root); assert.equal(feed.sessions.length, 1);
});
test('Claude removal never overwrites a subsequently changed status line', async t => {
  const dir = await temporary(t), file = join(dir, 'settings.json');
  await configureClaude('connect', dir, file);
  await writeFile(file, JSON.stringify({ statusLine: { type: 'command', command: 'new-renderer' } }));
  await assert.rejects(configureClaude('disconnect', dir, file), /changed externally/);
  assert.match(await readFile(file, 'utf8'), /new-renderer/);
});
test('OpenCode empty stdout is a valid empty session list', async t => {
  const dir = await temporary(t), file = join(dir, 'opencode');
  await writeFile(file, '#!/bin/sh\nexit 0\n', { mode: 0o700 });
  const s = await collectOpenCode(settings({ 'opencode-executable': file }));
  assert.equal(s.sessions.length, 0); assert(!s.error);
});
test('RPC rejects malformed responses and kills a timed-out process', async () => {
  const bad = new Rpc(process.execPath, ['-e', 'process.stdin.on("data",()=>console.log("not json"))']);
  await assert.rejects(bad.request('initialize'), /Invalid/); bad.close();
  const hung = new Rpc(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { timeout: 40 });
  await assert.rejects(hung.request('initialize'), /timeout/); hung.close();
});

test('Only Codex is enabled by default and both bar sizes retain an explicit reset countdown', () => {
  assert.deepEqual(Object.entries(settings()).filter(([k, v]) => k.endsWith('-enabled') && v).map(([k]) => k), ['codex-enabled']);
  const s = { ...snapshot('codex', 'test'), windows: [{ usedPercent: 25, resetsAt: now + 7800000 }] };
  for (const density of ['standard', 'compact']) {
    const rendered = bar(s, settings({ density }), now);
    assert.match(rendered.content[0].text, /resets in 2h 10m/);
    assert.match(rendered.compact[0].text, /resets in 2h 10m/);
  }
});
