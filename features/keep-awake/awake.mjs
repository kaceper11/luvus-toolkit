import { assertionCommand, powerStatus as portablePower } from '../../toolkit_core/power.mjs';
import { argsFor, featureInfo } from '../../toolkit_core/runtime.mjs';
import { spawn, execFile } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { promisify } from 'node:util';

export const MODULE_ID = 'kacper.keep-awake';
export const MODES = ['auto', 'on', 'off'];
const exec = promisify(execFile);

export async function command(file, args) {
  const { stdout } = await exec(file, args, {
    timeout: 2000, maxBuffer: 1024 * 1024, env: { ...process.env, LC_ALL: 'C' },
  });
  return stdout;
}

export async function luvus(ctx, ...args) {
  const original = args;
  args = await argsFor('keep-awake', args);
  const data = JSON.parse(await command(ctx.binary, args));
  if (data.error) throw new Error(data.error.code ?? 'Luvus request failed');
  if (!data.result || typeof data.result !== 'object') throw new Error('Invalid Luvus response');
  return original[0] === 'module' && original[1] === 'info' ? featureInfo(ctx, 'keep-awake', data.result) : data.result;
}

export function powerStatus(text) {
  const source = /^Now drawing from '(AC Power|Battery Power)'\s*$/m.exec(text)?.[1];
  const battery = /^\s*-InternalBattery[^\n]*?\s(\d+)%;/m.exec(text);
  const percent = battery ? Number(battery[1]) : null;
  if (!source || percent === null || percent > 100) throw new Error('Unavailable battery status');
  return { pluggedIn: source === 'AC Power', percent };
}

export function workingCount(data) {
  if (!Array.isArray(data?.agents)) throw new Error('Invalid agent list');
  const panes = new Set();
  let count = 0;
  for (const agent of data.agents) {
    if (!agent || typeof agent.pane !== 'string' || !agent.pane ||
        typeof agent.status !== 'string' || !agent.status || panes.has(agent.pane)) {
      throw new Error('Invalid or duplicate agent');
    }
    panes.add(agent.pane);
    if (agent.status === 'working') count++;
  }
  return count;
}

export function decision(mode, count, power) {
  if (!MODES.includes(mode)) throw new Error('Invalid mode');
  if (mode === 'off') return { awake: false, text: 'Awake: off' };
  if (!power.pluggedIn && power.percent <= 20) return { awake: false, text: 'Awake: low battery' };
  if (mode === 'on') return { awake: true, text: 'Awake: manual' };
  return { awake: count > 0, text: count ? `Awake: ${count} working` : 'Awake: idle' };
}

// Only owned child handles are signaled. No stored PID is ever used to stop a process.
export function assertion({ launch = spawn, now = () => performance.now(), nativeCommand = assertionCommand } = {}) {
  let child = null, started = 0;
  const stop = () => { child?.kill('SIGTERM'); child = null; };
  return {
    stop,
    get pid() { return child?.pid ?? null; },
    async update(awake) {
      if (!awake) { stop(); return; }
      if (child && child.exitCode === null && child.signalCode === null && now() - started < 20000) return;
      const [file, args] = nativeCommand();
      const next = launch(file, args, { stdio: 'ignore' });
      // Keep an error listener after spawn too; process errors must not crash the helper.
      next.on('error', () => {});
      try {
        await new Promise((resolve, reject) => { next.once('spawn', resolve); next.once('error', reject); });
      } catch (error) { stop(); throw error; }
      stop();
      child = next;
      started = now();
    },
  };
}

export async function observe(ctx, { call = luvus, power = () => portablePower(powerStatus, command) } = {}) {
  const info = await call(ctx, 'module', 'info', MODULE_ID);
  if (typeof info.enabled !== 'boolean' || typeof info.runnable !== 'boolean') throw new Error('Invalid module status');
  if (!info.enabled || !info.runnable) return null;
  const results = await Promise.allSettled([call(ctx, 'agent', 'list'), power()]);
  const error = results.find(result => result.status === 'rejected');
  if (error) throw error.reason;
  return { count: workingCount(results[0].value), power: typeof results[1].value === 'string' ? powerStatus(results[1].value) : results[1].value };
}
