import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { powerStatus, workingCount, decision, assertion, observe } from './awake.mjs';
import { context, lock, daemon as runDaemon } from './cli.mjs';

const identityOf = async ctx => {const s=await stat(ctx.socket);return `${s.ino}:${s.birthtimeMs}`;};
const isCurrent = async (ctx, value) => identityOf(ctx).then(current => current === value).catch(() => false);
const daemon = (ctx, options) => runDaemon(ctx, {identityOf, isCurrent, ...options});

const battery = (percent, pluggedIn = false) => `Now drawing from '${pluggedIn ? 'AC Power' : 'Battery Power'}'\n -InternalBattery-0 (id=123)\t${percent}%; discharging; 1:00 remaining present: true\n`;
const data = (count = 1, percent = 80, pluggedIn = false) => ({ count, power: { percent, pluggedIn } });

test('power parsing rejects missing/invalid readings and handles charge transitions', () => {
  assert.deepEqual(powerStatus(battery(20)), { percent: 20, pluggedIn: false });
  assert.deepEqual(powerStatus(battery(100, true)), { percent: 100, pluggedIn: true });
  for (const text of ['', 'Now drawing from \'AC Power\'', battery(101), battery(-1), battery(20).replace('Battery Power', 'UPS Power')]) {
    assert.throws(() => powerStatus(text));
  }
});

test('aggregation includes all workspaces and only working agents', () => {
  const agents = ['working', 'idle', 'done', 'blocked', 'unknown', 'working'].map((status, i) => ({ pane: String(i), workspace: String(i % 2), status }));
  assert.equal(workingCount({ agents }), 2);
  assert.equal(workingCount({ agents: [] }), 0);
  for (const value of [{}, { agents: null }, { agents: [{}] }, { agents: [agents[0], agents[0]] }]) assert.throws(() => workingCount(value));
});

test('Auto/On/Off and exact battery boundary', () => {
  assert.equal(decision('auto', 2, data().power).text, 'Awake: 2 working');
  assert.equal(decision('auto', 0, data().power).awake, false);
  assert.equal(decision('on', 0, data().power).text, 'Awake: manual');
  assert.equal(decision('off', 2, data().power).awake, false);
  for (const mode of ['auto', 'on']) {
    assert.equal(decision(mode, 1, data(1, 20).power).text, 'Awake: low battery');
    assert.equal(decision(mode, 1, data(1, 19).power).awake, false);
    assert.equal(decision(mode, 1, data(1, 21).power).awake, true);
    assert.equal(decision(mode, 1, data(1, 5, true).power).awake, true);
  }
  assert.throws(() => decision('bad', 1, data().power));
});

test('bounded assertions renew without dropping the old lease first; failures release it', async () => {
  let time = 0, fail = false;
  const children = [];
  const lease = assertion({ nativeCommand: () => ['/usr/bin/caffeinate',['-i','-t','30']], now: () => time, launch(file, args) {
    assert.equal(file, '/usr/bin/caffeinate');
    assert.deepEqual(args, ['-i', '-t', '30']);
    const child = Object.assign(new EventEmitter(), { pid: children.length + 1, exitCode: null, signalCode: null, kills: 0 });
    child.kill = () => { child.kills++; child.signalCode = 'SIGTERM'; };
    children.push(child);
    queueMicrotask(() => child.emit(fail ? 'error' : 'spawn', fail ? new Error('spawn failed') : undefined));
    return child;
  } });
  await lease.update(true);
  time = 19999;
  await lease.update(true);
  assert.equal(children.length, 1);
  time = 20000;
  const renew = lease.update(true);
  assert.equal(children[0].kills, 0);
  await renew;
  assert.equal(children[0].kills, 1);
  assert.equal(lease.pid, 2);
  time = 40000; fail = true;
  await assert.rejects(lease.update(true));
  assert.equal(children[1].kills, 1);
  assert.equal(lease.pid, null);
  fail = false;
  await lease.update(true);
  await lease.update(false);
  assert.equal(children.at(-1).kills, 1);
});

test('observation rejects failed commands and malformed responses; disabled stops before polling', async () => {
  let info = { enabled: true, runnable: true }, agents = { agents: [{ pane: '1', status: 'working' }] };
  let queries = 0;
  const call = async (ctx, kind) => { queries++; return kind === 'module' ? info : agents; };
  const power = async () => battery(80);
  assert.deepEqual(await observe({}, { call, power }), data());
  agents = {};
  await assert.rejects(observe({}, { call, power }));
  agents = { agents: [] };
  await assert.rejects(observe({}, { call, power: async () => { throw new Error('timeout'); } }));
  info = { enabled: false, runnable: true }; queries = 0;
  assert.equal(await observe({}, { call, power }), null);
  assert.equal(queries, 1);
});

test('context requires module identity and never invents a session', () => {
  assert.throws(() => context({}));
  const env = { LUVUS_MODULE_ID: 'kacper.toolkit', LUVUS_BIN_PATH: '/bin/luvus', LUVUS_SOCKET_PATH: '/session/a', LUVUS_MODULE_STATE_DIR: '/state', LUVUS_MODULE_CONFIG_DIR: '/config' };
  assert.notEqual(context(env).dir, context({ ...env, LUVUS_SOCKET_PATH: '/session/b' }).dir);
  assert.throws(() => context({ ...env, LUVUS_MODULE_ID: 'another-module' }));
});

test('native locks exclude duplicate owners and release after SIGKILL', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'awake-lock-'));
  let first, second, child;
  try {
    const path = join(dir, 'helper.lock');
    first = await lock(path);
    assert.ok(first);
    assert.equal(await lock(path), null);
    await first.close(); first = null;
    child = spawn(process.execPath, ['--input-type=module', '-e', `import { lock } from ${JSON.stringify(new URL('./cli.mjs', import.meta.url).href)}; const owner = await lock(${JSON.stringify(path)}); if (!owner) process.exit(1); console.log('ready'); setInterval(() => {}, 1000);`], { stdio: ['ignore', 'pipe', 'inherit'] });
    await new Promise((resolve, reject) => { child.stdout.once('data', resolve); child.once('error', reject); child.once('exit', code => reject(new Error(`Premature exit ${code}`))); });
    assert.equal(await lock(path), null);
    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill('SIGKILL'); await exited;
    second = await lock(path);
    assert.ok(second);
  } finally { child?.kill(); await first?.close(); await second?.close(); await rm(dir, { recursive: true, force: true }); }
});

test('helper resets mode, handles transitions/errors, paints changes only, and cleans up', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'awake-loop-'));
  const socket = join(dir, 'session.sock');
  await writeFile(socket, 'test endpoint');
  await writeFile(join(dir, 'mode.json'), JSON.stringify('off'));
  let step = 0, awake = false, stops = 0;
  const paints = [], updates = [];
  const lease = { pid: null, stop() { awake = false; stops++; }, async update(value) { awake = value; updates.push(value); } };
  try {
    await daemon({ dir, socket }, {
      interval: 1, lease,
      async read() {
        step++;
        if (step === 1) assert.equal(JSON.parse(await readFile(join(dir, 'mode.json'))), 'auto');
        if (step <= 2) return data();
        if (step === 3) return data(0);
        if (step === 4) { await writeFile(join(dir, 'mode.json'), JSON.stringify('on')); return data(0); }
        if (step === 5) return data(0, 20);
        if (step === 6) throw new Error('offline');
        if (step === 7) { await writeFile(join(dir, 'mode.json'), JSON.stringify('off')); return data(); }
        return null;
      },
      async call(ctx, ...args) { paints.push(JSON.parse(args[args.indexOf('--content') + 1])[0].text); },
    });
    assert.deepEqual(paints, ['Awake: 1 working', 'Awake: idle', 'Awake: manual', 'Awake: low battery', 'Awake: unavailable', 'Awake: off']);
    assert.deepEqual(updates, [true, true, false, true, false, false]);
    assert.equal(awake, false);
    assert.ok(stops >= 2);
    assert.equal(JSON.parse(await readFile(join(dir, 'status.json'))).pid, null);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('shutdown during an in-flight read never reacquires an assertion', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'awake-abort-'));
  const socket = join(dir, 'session.sock');
  await writeFile(socket, 'test endpoint');
  const controller = new AbortController();
  let acquisitions = 0, stops = 0;
  try {
    await daemon({ dir, socket }, {
      signal: controller.signal,
      lease: { stop() { stops++; }, async update() { acquisitions++; } },
      async read() { controller.abort(); await delay(1); return data(); },
    });
    assert.equal(acquisitions, 0);
    assert.ok(stops >= 1);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('duplicate daemon starts preserve the current override; a replaced session stops its owner', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'awake-duplicate-'));
  const socket = join(dir, 'session.sock');
  await writeFile(socket, 'original endpoint');
  let reads = 0, stopped = false, ready;
  const observed = new Promise(resolve => { ready = resolve; });
  const controller = new AbortController();
  const running = daemon({ dir, socket }, {
    interval: 1, signal: controller.signal,
    lease: { stop() { stopped = true; }, async update() {} },
    async read() { reads++; ready(); return data(0); },
    async call() {},
  });
  try {
    await observed;
    await writeFile(join(dir, 'mode.json'), JSON.stringify('off'));
    await daemon({ dir, socket }, { async read() { assert.fail('Duplicate helper polled'); } });
    assert.equal(JSON.parse(await readFile(join(dir, 'mode.json'))), 'off');
    assert.ok(reads > 0);
    await rm(socket);
    await writeFile(socket, 'new endpoint');
    await running;
    assert.equal(stopped, true);
  } finally { controller.abort(); await running; await rm(dir, { recursive: true, force: true }); }
});
