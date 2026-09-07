import { executable } from '../../toolkit_core/executable.mjs';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, readFile, writeFile, rename } from 'node:fs/promises';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';

const exec = promisify(execFile);
export async function command(file, args, options = {}) {
  const { input, ...rest } = options;
  [file, args] = executable(file, args);
  const task = exec(file, args, { timeout: 15000, maxBuffer: 32 * 1024 * 1024, ...rest });
  task.child.stdin.on('error', () => {});
  task.child.stdin.end(input);
  const { stdout } = await task;
  return stdout;
}
export async function readJSON(path, fallback = null) {
  try { return JSON.parse(await readFile(path, 'utf8')); }
  catch (e) { if (e.code === 'ENOENT') return fallback; throw e; }
}
export async function writeJSON(path, value) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const tmp = `${path}.${randomUUID()}.tmp`;
  await writeFile(tmp, JSON.stringify(value), { mode: 0o600 });
  await rename(tmp, path);
}
// Never forward a provider's raw stderr/error text: it may contain credentials or prompts.
export function failure(e) {
  if (e.code === 'ENOENT') return 'Not installed — configure executable';
  if (e.code === -32601) return 'Update required — method unavailable';
  if (e.code === 'ETIMEDOUT' || e.killed) return 'Timed out — retrying';
  if (/auth|login|sign.?in|401|403|unauthorized/i.test(e.message ?? '')) return 'Sign in using the tool';
  return 'Unavailable — check tool version and login';
}

// Both official servers use newline-delimited JSON-RPC over stdio. No agent turns are started.
export class Rpc {
  constructor(file, args, { timeout = 15000 } = {}) {
    this.timeout = timeout;
    this.pending = new Map();
    this.sequence = 0;
    this.buffer = '';
    [file, args] = executable(file, args);
    this.child = spawn(file, args, { stdio: ['pipe', 'pipe', 'ignore'] });
    this.child.stdin.on('error', e => this.abort(e));
    this.child.on('error', e => this.abort(e));
    this.child.on('exit', () => this.abort(new Error('Provider exited')));
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', chunk => {
      this.buffer += chunk;
      if (this.buffer.length > 32 * 1024 * 1024) return this.abort(new Error('Response too large'));
      let end;
      while ((end = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, end); this.buffer = this.buffer.slice(end + 1);
        if (!line.trim()) continue;
        let data;
        try { data = JSON.parse(line); } catch { this.abort(new Error('Invalid provider JSON')); return; }
        if (data.method) { // Decline any server-initiated request rather than performing it.
          if (data.id !== undefined) this.child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: data.id, error: { code: -32601, message: 'Read-only client' } }) + '\n');
          continue;
        }
        const p = this.pending.get(data.id);
        if (!p) continue;
        clearTimeout(p.timer); this.pending.delete(data.id);
        if (data.error) p.reject(Object.assign(new Error(data.error.message), { code: data.error.code }));
        else p.resolve(data.result);
      }
    });
  }
  request(method, params = {}) {
    if (this.error) return Promise.reject(this.error);
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.abort(Object.assign(new Error('Provider timeout'), { code: 'ETIMEDOUT' })), this.timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }
  notify(method) { this.child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method }) + '\n'); }
  abort(error) {
    this.error = error;
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(error); }
    this.pending.clear();
    if (this.child.exitCode === null) this.child.kill();
  }
  close() { this.abort(new Error('Closed')); }
  async initialize() {
    await this.request('initialize', { clientInfo: { name: 'luvus_ai_usage', title: 'Luvus AI Usage', version: '0.1.0' } });
    this.notify('initialized');
  }
}
