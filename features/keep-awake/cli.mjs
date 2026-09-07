#!/usr/bin/env node
import { python, root, sessionIdentity, sameSession } from '../../toolkit_core/runtime.mjs';
import { fork, spawn } from 'node:child_process';
import { mkdir, open, readFile, writeFile, rename, stat } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { MODULE_ID, MODES, luvus, observe, decision, assertion } from './awake.mjs';

const cli = fileURLToPath(import.meta.url);
export function context(env = process.env) {
  for (const key of ['LUVUS_BIN_PATH', 'LUVUS_SOCKET_PATH', 'LUVUS_MODULE_STATE_DIR', 'LUVUS_MODULE_CONFIG_DIR']) {
    if (!env[key]) throw new Error(`Missing ${key}; run through the intended Luvus module`);
  }
  if (env.LUVUS_MODULE_ID !== 'kacper.toolkit') throw new Error('Run through the Keep Awake module');
  const session = createHash('sha256').update(env.LUVUS_SOCKET_PATH).digest('hex').slice(0, 24);
  return { binary: env.LUVUS_BIN_PATH, socket: env.LUVUS_SOCKET_PATH, dir: join(env.LUVUS_MODULE_STATE_DIR, session) };
}

async function readJSON(path) { return JSON.parse(await readFile(path, 'utf8')); }
async function writeJSON(path, data) {
  const temp = `${path}.${randomUUID()}.tmp`;
  await writeFile(temp, JSON.stringify(data), { mode: 0o600 });
  await rename(temp, path);
}

// The child owns a native kernel lock; closing its pipe releases it on every OS.
export async function lock(path, seconds = 0) {
  const child = spawn(python, [join(root, 'toolkit_core/locks.py'), path, String(seconds)], {stdio: ['pipe','pipe','pipe']});
  let ready = false;
  const result = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Lock helper timed out')); }, (seconds + 3) * 1000);
    child.on('error', error => {clearTimeout(timer);reject(error);});
    child.stdout.on('data', chunk => {
      output += chunk;
      if (output.includes('\n')) { clearTimeout(timer);ready = output.trim() === 'ready';resolve(ready); }
    });
    child.on('exit', () => {clearTimeout(timer);if(!ready)resolve(false);});
    child.stdin.on('error', () => {});
  });
  if (!result) { child.stdin.end(); return null; }
  return {close: async () => {
    if (child.exitCode !== null) return;
    await new Promise(resolve => {child.once('exit', resolve);child.stdin.end();});
  }};
}

export async function start(ctx) {
  await mkdir(ctx.dir, { recursive: true, mode: 0o700 });
  const guard = await lock(join(ctx.dir, 'startup.lock'), 5);
  if (!guard) throw new Error('Another startup is pending; retry');
  let log;
  try {
    log = await open(join(ctx.dir, 'helper.log'), 'a', 0o600);
    const child = fork(cli, ['daemon'], { detached: true, stdio: ['ignore', log.fd, log.fd, 'ipc'] });
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Helper startup timed out')), 5000);
        const done = error => { clearTimeout(timer); error ? reject(error) : resolve(); };
        child.once('message', message => done(message.ready ? null : new Error(message.error ?? 'Helper failed')));
        child.once('error', done);
        child.once('exit', code => done(new Error(`Helper exited during startup (${code})`)));
      });
    } catch (error) { child.kill('SIGTERM'); throw error; }
    finally { if (child.connected) child.disconnect(); child.unref(); }
  } finally { await log?.close(); await guard.close(); }
}

export async function daemon(ctx, { read = observe, lease = assertion(), call = luvus, interval = 5000, signal } = {}) {
  await mkdir(ctx.dir, { recursive: true, mode: 0o700 });
  const owner = await lock(join(ctx.dir, 'helper.lock'));
  if (!owner) { process.send?.({ ready: true }); return; }
  const abort = new AbortController();
  const stop = () => { abort.abort(); lease.stop(); };
  process.once('SIGTERM', stop); process.once('SIGINT', stop);
  signal?.addEventListener('abort', stop, { once: true });
  let lastText = '';
  try {
    const identity = await sessionIdentity(ctx);
    await writeJSON(join(ctx.dir, 'mode.json'), 'auto');
    process.send?.({ ready: true });
    while (!abort.signal.aborted) {
      let state, mode = 'auto';
      if (!await sameSession(ctx, identity)) break;
      try {
        const data = await read(ctx);
        if (data === null) break;
        mode = await readJSON(join(ctx.dir, 'mode.json'));
        state = decision(mode, data.count, data.power);
        if (abort.signal.aborted) break;
        await lease.update(state.awake);
      } catch {
        lease.stop();
        state = { awake: false, text: 'Awake: unavailable' };
      }
      if (abort.signal.aborted) break;
      await writeJSON(join(ctx.dir, 'status.json'), { pid: process.pid, mode, ...state, assertionPid: lease.pid, updatedAt: Date.now() });
      if (state.text !== lastText) {
        const content = JSON.stringify([{ type: 'text', text: state.text, action: 'cycle' }]);
        try {
          await call(ctx, 'bar', 'push', '--id', 'awake', '--content', content, '--compact-content', content);
          lastText = state.text;
        } catch { /* Retry painting; assertion renewal still requires fresh observations. */ }
      }
      await delay(interval, undefined, { signal: abort.signal }).catch(() => {});
    }
  } finally {
    stop();
    process.off('SIGTERM', stop); process.off('SIGINT', stop);
    signal?.removeEventListener('abort', stop);
    await writeJSON(join(ctx.dir, 'status.json'), { pid: null, mode: 'auto', awake: false, text: 'Awake: off', assertionPid: null, updatedAt: Date.now() }).catch(() => {});
    await owner.close();
  }
}

async function main() {
  const ctx = context();
  const action = process.argv[2];
  if (action === 'daemon') return daemon(ctx);
  if (!['start', 'cycle'].includes(action)) throw new Error('Expected start or cycle');
  await start(ctx);
  if (action === 'cycle') {
    const guard = await lock(join(ctx.dir, 'action.lock'), 5);
    if (!guard) throw new Error('Another click is pending; retry');
    try {
      const mode = await readJSON(join(ctx.dir, 'mode.json'));
      if (!MODES.includes(mode)) throw new Error('Invalid mode');
      const next = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
      await writeJSON(join(ctx.dir, 'mode.json'), next);
      console.log(`Keep Awake: ${next} (updates within five seconds)`);
    } finally { await guard.close(); }
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch(error => {
    process.send?.({ error: error.message });
    console.error(error.message);
    process.exitCode = 1;
  });
}
