import { argsFor, featureInfo, featureSettings, sessionIdentity, sameSession, stopTree, python, root } from '../../toolkit_core/runtime.mjs';
import { spawn, fork } from 'node:child_process';
import { mkdir, open, unlink, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { command, readJSON, writeJSON, failure } from './io.mjs';
import { PROVIDERS, snapshot, settings, bar, stale, primaryWindow, metric, number, clean } from './model.mjs';

export const MODULE_ID = 'kacper.ai-usage';
const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
export function context(env = process.env) {
  for (const key of ['LUVUS_BIN_PATH', 'LUVUS_SOCKET_PATH', 'LUVUS_MODULE_STATE_DIR', 'LUVUS_MODULE_CONFIG_DIR']) {
    if (!env[key]) throw new Error(`Missing ${key}; run this action through the intended Luvus session`);
  }
  const root = env.LUVUS_MODULE_STATE_DIR;
  if (env.LUVUS_MODULE_ID !== 'kacper.toolkit') throw new Error('Run this action through the AI Usage module');
  const id = createHash('sha256').update(env.LUVUS_SOCKET_PATH).digest('hex').slice(0, 24);
  return { root, dir: join(root, 'sessions', id), binary: env.LUVUS_BIN_PATH, socket: env.LUVUS_SOCKET_PATH, configDir: env.LUVUS_MODULE_CONFIG_DIR };
}
export async function luvus(ctx, ...args) {
  const original = args;
  args = await argsFor('ai-usage', args);
  const result = JSON.parse(await command(ctx.binary, args, { timeout: 5000 }));
  if (result.error) throw Object.assign(new Error(result.error.message ?? 'Luvus request failed'), { code: result.error.code });
  const value = result.result ?? result;
  return original[0] === 'module' && original[1] === 'info' ? featureInfo(ctx, 'ai-usage', value) : value;
}
export async function readSettings(ctx) {
  const stored = await readJSON(join(ctx.configDir, 'settings.json'), {});
  return settings({ ...stored, ...await featureSettings(ctx, 'ai-usage') });
}
const isAlive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };
export async function start(ctx) {
  await mkdir(ctx.dir, { recursive: true, mode: 0o700 });
  const lockPath = join(ctx.dir, 'starting.lock');
  let lock;
  try { lock = await open(lockPath, 'wx', 0o600); }
  catch (e) { if (e.code === 'EEXIST') return; throw e; }
  try {
    const codeStamp = (await Promise.all(['cli.mjs', 'runtime.mjs', 'model.mjs', 'providers.mjs', 'io.mjs', 'claude.mjs'].map(name => stat(new URL(name, import.meta.url))))).map(s => s.mtimeMs).join(':');
    const existing = await readJSON(join(ctx.dir, 'helper.json'));
    if (existing && isAlive(existing.pid)) {
      const args = process.platform === 'win32'
        ? await command(python, [join(root, 'toolkit_core/process.py'), String(existing.pid)]).then(raw => (JSON.parse(raw)?.argv ?? []).join(' ')).catch(() => '')
        : await command('/bin/ps', ['-p', String(existing.pid), '-o', 'args=']).catch(() => '');
      if (args.includes(`${cli} daemon`)) {
        if (existing.codeStamp === codeStamp) return;
        process.kill(existing.pid, 'SIGTERM');
        const deadline = Date.now() + 5000;
        while (isAlive(existing.pid) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
        if (isAlive(existing.pid)) throw new Error('Previous usage helper is still stopping; retry Refresh');
      }
    }
    const log = await open(join(ctx.dir, 'helper.log'), 'w', 0o600);
    try {
      const child = spawn(process.execPath, [cli, 'daemon'], { detached: true, stdio: ['ignore', log.fd, log.fd] });
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      await writeJSON(join(ctx.dir, 'helper.json'), { pid: child.pid, codeStamp });
      child.unref();
    } finally { await log.close(); }
  } finally { await lock.close(); await unlink(lockPath); }
}
export async function requestRefresh(ctx, forced = true) {
  await writeJSON(join(ctx.dir, 'refresh.json'), { at: Date.now(), forced });
  await start(ctx);
}
export function collectIsolated(provider, config, root, children = new Set(), timeout = 60000) {
  return new Promise(resolve => {
    const child = fork(cli, ['collect-worker', provider], {
      detached: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
      env: { ...process.env, LUVUS_USAGE_CONFIG_JSON: JSON.stringify(config), LUVUS_USAGE_ROOT: root },
    });
    children.add(child);
    let complete = false;
    const finish = result => {
      if (complete) return; complete = true;
      clearTimeout(timer); children.delete(child);
      // Also stop a stuck SDK runtime or RPC subprocess belonging to this collector.
      stopTree(child);
      resolve(result);
    };
    const timer = setTimeout(() => finish({ ...snapshot(provider, 'Collector'), error: 'Timed out — retrying' }), timeout);
    child.on('message', result => finish(result));
    child.on('error', e => finish({ ...snapshot(provider, 'Collector'), error: failure(e) }));
    child.on('exit', () => finish({ ...snapshot(provider, 'Collector'), error: 'Collector exited — retrying' }));
  });
}
export function enabledModule(result) {
  const info = result.module ?? result;
  if (typeof info.enabled !== 'boolean') throw new Error('Unsupported module.info response; update compatibility adapter');
  return info.enabled && info.runnable !== false;
}
export function missionMetrics(provider, response) {
  const entries = (response.rows ?? []).filter(row => row.kind === 'live' && String(row.agent).toLowerCase().replaceAll(' ', '').includes(provider) && row.usage);
  const metrics = [];
  for (const row of [...new Map(entries.map(row => [row.pane, row])).values()]) {
    const scope = `Luvus pane ${clean(row.pane)} (cached)`;
    for (const [key, label, unit] of [['tokens_in', 'Input tokens', 'tokens'], ['tokens_out', 'Output tokens', 'tokens'], ['cache_tokens', 'Cache tokens', 'tokens'], ['total_tokens', 'Total tokens', 'tokens'], ['cost_usd', 'Cost', 'USD']]) if (number(row.usage[key]) !== null) metrics.push(metric(label, row.usage[key], unit, scope));
    if (number(row.usage.context) !== null) metrics.push(metric('Context used', row.usage.context * 100, '%', scope));
    if (row.usage.model) metrics.push(metric('Model', clean(row.usage.model), '', scope));
  }
  return metrics;
}
export function mergeObservation(previous, next) {
  if (!next.error || !previous || next.error.startsWith('Sign in') || next.error.startsWith('Not installed')) return next;
  return { ...previous, error: next.error };
}
export async function readNotifications(ctx) {
  try {
    const sent = await readJSON(join(ctx.dir, 'notifications.json'), {});
    if (!sent || typeof sent !== 'object' || Array.isArray(sent)) throw new Error('Invalid notification state');
    for (const [key, level] of Object.entries(sent)) {
      const identity = JSON.parse(key);
      if (!Array.isArray(identity) || identity.length !== 4 || !Object.hasOwn(PROVIDERS, identity[0]) ||
          !(identity[1] === null || typeof identity[1] === 'string') || typeof identity[2] !== 'string' ||
          !(identity[3] === null || number(identity[3]) !== null) || !['warning', 'error'].includes(level)) throw new Error('Invalid notification state');
    }
    return { sent, dirty: false };
  } catch {
    console.error('Usage notifications paused: cannot read notification state. Widgets remain available.');
    return null;
  }
}
export async function notifyUsage(ctx, snap, config, notifications, now = Date.now()) {
  if (!notifications) return;
  const save = async () => {
    try { await writeJSON(join(ctx.dir, 'notifications.json'), notifications.sent); notifications.dirty = false; }
    catch { console.error('Usage notification state could not be saved; retrying before further notifications.'); }
  };
  if (notifications.dirty) { await save(); if (notifications.dirty) return; }
  if (!config.notifications || !config[`${snap.provider}-enabled`] || snap.error || number(snap.observedAt) === null || stale(snap, config, now)) return;
  const w = primaryWindow(snap, now);
  if (!w || typeof w.id !== 'string' || w.unlimited || number(w.usedPercent) === null ||
      (w.resetsAt != null && (number(w.resetsAt) === null || w.resetsAt <= now))) return;
  const level = w.usedPercent >= config['critical-percent'] ? 'error' : w.usedPercent >= config['warning-percent'] ? 'warning' : null;
  if (!level) return;
  const key = JSON.stringify([snap.provider, typeof snap.account === 'string' ? snap.account : null, w.id, w.resetsAt ?? null]);
  if (notifications.sent[key] === 'error' || notifications.sent[key] === level) return;
  try {
    await luvus(ctx, 'ui', 'notification', 'push', '--text', `${PROVIDERS[snap.provider]}: ${Math.round(w.usedPercent)}% used`, '--level', level, '--dedupe-key', `ai-usage-${snap.provider}`);
  } catch { console.error('Usage notification delivery failed; retrying on the next refresh.'); return; }
  // ponytail: retain window identities indefinitely; prune only with a provider-backed retention rule if this file grows materially.
  notifications.sent[key] = level;
  notifications.dirty = true;
  await save();
}
export async function daemon(ctx) {
  const identity = await sessionIdentity(ctx);
  const children = new Set(), running = new Map(), lastAttempt = {}, retries = {}, pendingRefresh = {}, disabled = new Set();
  const notifications = await readNotifications(ctx);
  let stopped = false, changed = true, lastPaint = 0, lastRequest = 0;
  let previousConfig = {};
  let cache = await readJSON(join(ctx.dir, 'usage.json'), {});
  const capabilities = await luvus(ctx, 'uhp', 'capabilities');
  const hasMission = JSON.stringify(capabilities).includes('"mission.snapshot"');
  if (hasMission) await luvus(ctx, 'uhp', 'schema');
  const stop = () => {
    stopped = true;
    for (const child of children) stopTree(child);
  };
  process.once('SIGTERM', stop); process.once('SIGINT', stop);
  try {
    while (!stopped) {
      if (!await sameSession(ctx, identity)) break;
      if (!enabledModule(await luvus(ctx, 'module', 'info', MODULE_ID))) break;
      const config = await readSettings(ctx);
      if (JSON.stringify(config) !== JSON.stringify(previousConfig)) changed = true;
      const request = await readJSON(join(ctx.dir, 'refresh.json'), {});
      const requested = request.at > lastRequest;
      if (requested) lastRequest = request.at;
      for (const provider of Object.keys(PROVIDERS)) {
        if (requested) pendingRefresh[provider] = Math.min(pendingRefresh[provider] ?? Infinity, request.forced ? 10000 : 60000);
        if (config[`${provider}-executable`] !== previousConfig[`${provider}-executable`] || config[`${provider}-enabled`] !== previousConfig[`${provider}-enabled`] || config['period-days'] !== previousConfig['period-days']) { lastAttempt[provider] = 0; retries[provider] = 0; }
        if (!config[`${provider}-enabled`]) {
          if (running.has(provider)) { stopTree([...children].find(child => child.pid === running.get(provider).pid)); }
          if (!disabled.has(provider)) await luvus(ctx, 'bar', 'remove', '--id', provider);
          disabled.add(provider); delete pendingRefresh[provider];
          continue;
        }
        disabled.delete(provider);
        const elapsed = Date.now() - (lastAttempt[provider] ?? 0);
        const interval = provider === 'claude' ? 60000 : config['refresh-minutes'] * 60000;
        if (!running.has(provider) && (elapsed >= Math.min(3600000, interval * 2 ** (retries[provider] ?? 0)) || elapsed >= (pendingRefresh[provider] ?? Infinity))) {
          lastAttempt[provider] = Date.now();
          delete pendingRefresh[provider];
          running.set(provider, {});
          const before = new Set(children);
          const promise = collectIsolated(provider, config, ctx.root, children).then(next => {
            retries[provider] = next.error ? Math.min(4, (retries[provider] ?? 0) + 1) : 0;
            cache[provider] = mergeObservation(cache[provider], next);
            running.delete(provider); changed = true;
          });
          running.set(provider, { pid: [...children].find(c => !before.has(c))?.pid, promise });
        }
      }
      previousConfig = config;
      if (changed || requested || Date.now() - lastPaint >= 60000) {
        let mission = {};
        if (hasMission) {
          try {
            const raw = JSON.parse(await command(ctx.binary, ['uhp', 'proxy'], { timeout: 5000, input: JSON.stringify({ id: 'ai-usage', method: 'mission.snapshot', params: { scope: 'all' } }) + '\n' }));
            mission = raw.result ?? {};
          } catch { /* Optional enrichment must not stop account collectors. */ }
        }
        for (const provider of Object.keys(PROVIDERS).filter(p => config[`${p}-enabled`])) {
          const snap = cache[provider] ?? snapshot(provider, 'Waiting for first collection');
          // Mission Control omits native session IDs. Keep pane stats separate instead of summing duplicates.
          snap.metrics = snap.metrics.filter(m => !m.scope?.startsWith('Luvus pane ')).concat(missionMetrics(provider, mission));
          const content = bar(snap, config);
          await luvus(ctx, 'bar', 'push', '--id', provider, '--priority', String(config[`${provider}-priority`]), '--content', JSON.stringify(content.content), '--compact-content', JSON.stringify(content.compact));
          await notifyUsage(ctx, snap, config, notifications);
        }
        await writeJSON(join(ctx.dir, 'usage.json'), cache);
        changed = false; lastPaint = Date.now();
      }
      // One timer for health checks; countdown rendering and network refresh have separate cadences.
      await new Promise(resolve => {
        const done = () => { clearTimeout(timer); process.off('SIGTERM', done); process.off('SIGINT', done); resolve(); };
        const timer = setTimeout(done, 5000);
        process.once('SIGTERM', done); process.once('SIGINT', done);
      });
    }
  } finally {
    stop();
    const owner = await readJSON(join(ctx.dir, 'helper.json'));
    if (owner?.pid === process.pid) await unlink(join(ctx.dir, 'helper.json')).catch(() => {});
  }
}
