import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm, mkdir, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { context, missionMetrics, readNotifications, notifyUsage } from '../runtime.mjs';
import { settings, snapshot } from '../model.mjs';
import { copilotData } from '../providers.mjs';
import { readJSON } from '../io.mjs';

const exec = promisify(execFile);
const cli = new URL('../cli.mjs', import.meta.url).pathname;
async function until(fn, timeout = 14000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const result = await fn(); if (result) return result; await new Promise(r => setTimeout(r, 100)); }
  throw new Error('Timed out waiting for helper');
}

test('Helper publishes, honors settings, deduplicates startup, and exits on disable or server exit', { timeout: 35000 }, async t => {
  const dir = await mkdtemp(join(tmpdir(), 'usage-runtime-test-'));
  const root = join(dir, 'state'), configDir = join(dir, 'config'), binary = join(dir, 'luvus'), provider = join(dir, 'codex');
  await mkdir(configDir); await mkdir(root); await writeFile(join(dir, 'socket'), 'test socket identity');
  const preferences = { 'codex-executable': provider, 'claude-enabled': false, 'muse-enabled': false, 'opencode-enabled': false, 'copilot-enabled': false, 'codex-priority': 85 };
  await writeFile(join(configDir, 'settings.json'), JSON.stringify(preferences));
  const env = { ...process.env, LUVUS_MODULE_ID: 'kacper.toolkit', LUVUS_BIN_PATH: binary, LUVUS_SOCKET_PATH: join(dir, 'socket'), LUVUS_PANE_ID: '1', LUVUS_MODULE_STATE_DIR: root, LUVUS_MODULE_CONFIG_DIR: configDir, FIXTURE_DIR: dir };
  const ctx = context(env);
  await writeFile(binary, `#!/usr/bin/env node
const fs=require('node:fs'),p=require('node:path'),args=process.argv.slice(2),root=process.env.FIXTURE_DIR;
fs.appendFileSync(p.join(root,'calls.jsonl'),JSON.stringify(args)+'\\n');
let result={type:'ok'};
if(args[0]==='module'&&args[1]==='info')result={type:'module_info',enabled:!fs.existsSync(p.join(root,'disabled')),runnable:true};
if(args[0]==='module'&&args[1]==='settings')result={settings:Object.entries(JSON.parse(fs.readFileSync(p.join(root,'config/settings.json'),'utf8'))).map(([key,value])=>({key:'ai-usage-'+key,value}))};
if(args[0]==='uhp')result={methods:[]};
console.log(JSON.stringify({result}));
`, { mode: 0o700 });
  await writeFile(provider, `#!/usr/bin/env node
require('node:readline').createInterface({input:process.stdin}).on('line',line=>{
const r=JSON.parse(line);if(r.id===undefined)return;
const result=r.method==='account/rateLimits/read'?{rateLimits:{primary:{usedPercent:85,resetsAt:Math.floor(Date.now()/1000)+3600}}}:r.method==='thread/list'?{data:[],nextCursor:null}:{};
console.log(JSON.stringify({id:r.id,result}));
});
`, { mode: 0o700 });
  t.after(async () => {
    const owner = await readJSON(join(ctx.dir, 'helper.json'));
    if (owner?.pid) try { process.kill(owner.pid, 'SIGTERM'); } catch { /* Already stopped. */ }
    await rm(dir, { recursive: true, force: true });
  });
  await exec(process.execPath, [cli, 'start'], { env });
  const owner = await until(() => readJSON(join(ctx.dir, 'helper.json')));
  await exec(process.execPath, [cli, 'start'], { env });
  assert.equal((await readJSON(join(ctx.dir, 'helper.json'))).pid, owner.pid);
  const cache = await until(async () => { const c = await readJSON(join(ctx.dir, 'usage.json')); return c?.codex?.windows?.length ? c : null; });
  assert.equal(cache.codex.windows[0].usedPercent, 85);
  let calls = (await readFile(join(dir, 'calls.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  const push = calls.filter(c => c[0] === 'bar' && c[1] === 'push').at(-1);
  assert.equal(push[push.indexOf('--priority') + 1], '85');
  assert.match(push[push.indexOf('--content') + 1], /85% used/);
  assert(calls.some(c => c[0] === 'bar' && c[1] === 'remove' && c.includes('ai-usage-claude')));
  assert(!calls.some(c => c[0] === 'ui' && c[1] === 'notification'));
  await writeFile(join(configDir, 'settings.json'), JSON.stringify({ ...preferences, display: 'remaining' }));
  await exec(process.execPath, [cli, 'refresh'], { env });
  await until(async () => (await readFile(join(dir, 'calls.jsonl'), 'utf8')).includes('15% remaining'));
  await writeFile(join(dir, 'disabled'), 'true');
  await until(async () => !(await readJSON(join(ctx.dir, 'helper.json'))));
  await unlink(join(dir, 'disabled'));
  await writeFile(join(ctx.dir, 'notifications.json'), '{broken');
  await writeFile(join(configDir, 'settings.json'), JSON.stringify({ ...preferences, notifications: true }));
  const callsBeforeRestart = (await readFile(join(dir, 'calls.jsonl'), 'utf8')).trim().split('\n').length;
  await exec(process.execPath, [cli, 'start'], { env });
  await until(() => readJSON(join(ctx.dir, 'helper.json')));
  await until(async () => {
    const recent = (await readFile(join(dir, 'calls.jsonl'), 'utf8')).trim().split('\n').slice(callsBeforeRestart).map(JSON.parse);
    assert(!recent.some(c => c[0] === 'ui' && c[1] === 'notification'));
    return recent.some(c => c[0] === 'bar' && c[1] === 'push');
  });
  assert.match(await readFile(join(ctx.dir, 'helper.log'), 'utf8'), /notifications paused/);
  await unlink(join(dir, 'socket'));
  await until(async () => !(await readJSON(join(ctx.dir, 'helper.json'))));
});

test('Mission Control pane metrics stay separate and deduplicate the same pane', () => {
  const row = { kind: 'live', pane: '8', agent: 'OpenCode', usage: { total_tokens: 100, tokens_in: 80, context: 0.5 } };
  const metrics = missionMetrics('opencode', { rows: [row, row, { ...row, kind: 'resumable' }] });
  assert.equal(metrics.filter(m => m.name === 'Total tokens').length, 1);
  assert.equal(metrics.find(m => m.name === 'Context used').value, 50);
  assert(metrics.every(m => m.scope === 'Luvus pane 8 (cached)'));
});

async function notificationFixture(t) {
  const dir = await mkdtemp(join(tmpdir(), 'usage-notification-test-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const binary = join(dir, 'luvus');
  await writeFile(binary, `#!/usr/bin/env node
const fs = require('node:fs'), p = require('node:path');
fs.appendFileSync(p.join(__dirname, 'calls.jsonl'), JSON.stringify(process.argv.slice(2)) + '\\n');
console.log(JSON.stringify(fs.existsSync(p.join(__dirname, 'fail')) ? {error:{message:'fixture failure'}} : {result:{type:'ok'}}));
`, { mode: 0o700 });
  const env = { LUVUS_MODULE_ID: 'kacper.toolkit', LUVUS_BIN_PATH: binary, LUVUS_SOCKET_PATH: join(dir, 'socket'), LUVUS_MODULE_STATE_DIR: dir, LUVUS_MODULE_CONFIG_DIR: dir };
  const ctx = context(env);
  const calls = async () => (await readFile(join(dir, 'calls.jsonl'), 'utf8').catch(e => { if (e.code === 'ENOENT') return ''; throw e; })).trim().split('\n').filter(Boolean).map(JSON.parse);
  const now = Date.now();
  const snap = { ...snapshot('codex', 'fixture'), observedAt: now, windows: [{ id: 'codex:primary', usedPercent: 85, resetsAt: now + 3600000 }] };
  return { ctx, env, dir, calls, now, snap, config: settings({ notifications: true, 'copilot-enabled': true }) };
}

test('Quota alerts persist per account/window and severity across recovery and restarts', async t => {
  const { ctx, env, calls, now, snap, config } = await notificationFixture(t);
  let state = await readNotifications(ctx);
  const observe = (s = snap, c = config) => notifyUsage(ctx, s, c, state, now);
  await observe(snap, settings());
  assert.equal((await calls()).length, 0);
  await observe(); await observe();
  await observe({ ...snap, error: 'Timed out — retrying' });
  await observe({ ...snap, observedAt: now - 3600000 });
  await observe({ ...snap, windows: [{ ...snap.windows[0], usedPercent: 20 }] });
  await observe();
  state = await readNotifications(ctx);
  await observe();
  assert.equal((await calls()).length, 1);
  await observe({ ...snap, windows: [{ ...snap.windows[0], usedPercent: 96 }] });
  state = await readNotifications(ctx);
  await observe();
  await observe({ ...snap, windows: [{ ...snap.windows[0], usedPercent: 99 }] });
  assert.equal((await calls()).length, 2);
  const nextWindow = { ...snap, windows: [{ ...snap.windows[0], resetsAt: now + 7200000 }] };
  await observe(nextWindow); await observe();
  const secondary = { ...snap, windows: [{ ...snap.windows[0], id: 'codex:secondary', usedPercent: 99 }] };
  await observe(secondary);
  await observe({ ...secondary, windows: [{ ...secondary.windows[0], usedPercent: 85 }] });
  await observe({ ...snap, account: 'account-b' }); await observe();
  const unknownReset = { ...snap, windows: [{ ...snap.windows[0], resetsAt: null }] };
  await observe(unknownReset);
  state = await readNotifications(ctx);
  await observe(unknownReset);
  assert.equal((await calls()).length, 6);
  const other = context({ ...env, LUVUS_SOCKET_PATH: env.LUVUS_SOCKET_PATH + '-other' });
  assert.notEqual(ctx.dir, other.dir);
  await notifyUsage(other, snap, config, await readNotifications(other), now);
  const delivered = await calls();
  assert.equal(delivered.length, 7);
  assert.equal(delivered[1][delivered[1].indexOf('--level') + 1], 'error');
  assert.equal(delivered[0][delivered[0].indexOf('--dedupe-key') + 1], 'ai-usage-codex');
});

test('Quota alert eligibility excludes expired, unlimited, missing, invalid and stale observations', async t => {
  const { ctx, calls, now, snap, config } = await notificationFixture(t);
  const state = await readNotifications(ctx);
  const windows = [{ unlimited: true }, { usedPercent: null }, { usedPercent: NaN }, { usedPercent: Infinity }, { usedPercent: -1 }, { usedPercent: '99' }, { resetsAt: now }, { resetsAt: NaN }, { resetsAt: 0 }];
  for (const patch of windows) await notifyUsage(ctx, { ...snap, windows: [{ ...snap.windows[0], ...patch }] }, config, state, now);
  for (const patch of [{ error: 'Unavailable' }, { observedAt: now - 3600000 }, { observedAt: null }, { windows: [] }]) await notifyUsage(ctx, { ...snap, ...patch }, config, state, now);
  await notifyUsage(ctx, snap, settings({ notifications: true, 'codex-enabled': false }), state, now);
  for (const unlimited of [{ isUnlimitedEntitlement: true }, { entitlementRequests: -1 }]) {
    await notifyUsage(ctx, copilotData({ quotaSnapshots: { chat: { remainingPercentage: 0, ...unlimited } } }), config, state, now);
  }
  assert.equal((await calls()).length, 0);
  await notifyUsage(ctx, snap, config, state, now);
  assert.equal((await calls()).length, 1);
});

test('Notification failures retry without losing delivery history or overwriting malformed state', async t => {
  const { ctx, dir, calls, now, snap, config } = await notificationFixture(t);
  const diagnostics = [];
  t.mock.method(console, 'error', text => diagnostics.push(text));
  const state = await readNotifications(ctx);
  await writeFile(join(dir, 'fail'), 'fail');
  await notifyUsage(ctx, snap, config, state, now);
  assert.deepEqual(state.sent, {});
  await unlink(join(dir, 'fail'));
  const file = join(ctx.dir, 'notifications.json');
  await mkdir(file, { recursive: true }); // Force a persistence failure after successful delivery.
  await notifyUsage(ctx, snap, config, state, now);
  assert.equal(state.dirty, true);
  await notifyUsage(ctx, snap, config, state, now);
  assert.equal((await calls()).length, 2);
  await rm(file, { recursive: true });
  await notifyUsage(ctx, snap, config, state, now);
  assert.equal(state.dirty, false);
  await notifyUsage(ctx, snap, config, await readNotifications(ctx), now);
  assert.equal((await calls()).length, 2);
  for (const invalid of ['{broken', 'null', '[]', '{"invalid":"warning"}', JSON.stringify({ [JSON.stringify(['codex', null, 'primary', null])]: 'other' })]) {
    await writeFile(file, invalid);
    const blocked = await readNotifications(ctx);
    assert.equal(blocked, null);
    await notifyUsage(ctx, snap, config, blocked, now);
    assert.equal(await readFile(file, 'utf8'), invalid);
  }
  assert.equal((await calls()).length, 2);
  assert(diagnostics.some(line => line.includes('delivery failed')));
  assert(diagnostics.some(line => line.includes('could not be saved')));
  assert(diagnostics.some(line => line.includes('notifications paused')));
});
