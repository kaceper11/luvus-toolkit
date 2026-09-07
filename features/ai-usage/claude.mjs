import { readFile, unlink, mkdir, open } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { readJSON, writeJSON } from './io.mjs';
import { claudeData } from './providers.mjs';

const quote = text => `'${text.replaceAll("'", "'\\''")}'`;
export const settingsPath = () => join(process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), '.claude'), 'settings.json');
export async function configureClaude(mode, root, file = settingsPath()) {
  const backupPath = join(root, 'claude-statusline-backup.json');
  if (mode !== 'connect' && !(await readJSON(backupPath))) return 'Claude status line is not connected.';
  await mkdir(dirname(file), { recursive: true, mode: 0o700 });
  // Serialize this module's setup actions. An exact-byte comparison protects external edits.
  const lock = await open(`${file}.luvus-ai-usage.lock`, 'wx', 0o600);
  try {
    const before = await readFile(file, 'utf8').catch(e => { if (e.code === 'ENOENT') return ''; throw e; });
    const config = before ? JSON.parse(before) : {};
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Invalid Claude settings');
    const backup = await readJSON(backupPath);
    if (config.statusLine != null && (config.statusLine.type !== 'command' || typeof config.statusLine.command !== 'string')) throw new Error('Unsupported existing Claude status line; leaving it untouched');
    const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
    const wrapper = { ...config.statusLine, type: 'command', command: `${quote(process.execPath)} ${quote(cli)} claude-feed ${quote(root)}` };
    if (mode === 'connect') {
      if (backup && JSON.stringify(config.statusLine) === JSON.stringify(backup.wrapper)) return 'Claude status line already connected.';
      if (backup) throw new Error('Claude status line changed after setup; disconnect before reconnecting');
      await writeJSON(backupPath, { file, previous: config.statusLine ?? null, wrapper });
      config.statusLine = wrapper;
    } else {
      if (!backup) return 'Claude status line is not connected.';
      if (backup.file !== file || JSON.stringify(config.statusLine) !== JSON.stringify(backup.wrapper)) throw new Error('Claude status line was changed externally; leaving it untouched');
      if (backup.previous === null) delete config.statusLine; else config.statusLine = backup.previous;
    }
    const current = await readFile(file, 'utf8').catch(e => { if (e.code === 'ENOENT') return ''; throw e; });
    if (current !== before) throw new Error('Claude settings changed during setup; retry');
    await writeJSON(file, config);
    if (mode !== 'connect') await unlink(backupPath);
    return mode === 'connect' ? 'Claude connected. Start or resume Claude Code to publish usage.' : 'Previous Claude status line restored.';
  } finally { await lock.close(); await unlink(`${file}.luvus-ai-usage.lock`); }
}

export async function claudeFeed(root) {
  const chunks = []; let length = 0;
  for await (const chunk of process.stdin) {
    length += chunk.length;
    if (length > 1024 * 1024) throw new Error('Status line input too large');
    chunks.push(chunk);
  }
  const input = Buffer.concat(chunks).toString('utf8');
  // Preserve the user's renderer even when our statistics cannot be parsed or saved.
  const backup = await readJSON(join(root, 'claude-statusline-backup.json'));
  try {
    const data = claudeData(JSON.parse(input));
    if (data.sessions[0]?.id) {
      const id = createHash('sha256').update(data.sessions[0].id).digest('hex');
      await writeJSON(join(root, 'claude', `${id}.json`), data);
    }
  } catch { /* A broken feed must never break the user's status line. */ }
  if (backup?.previous?.type === 'command' && backup.previous.command) {
    const child = spawn(process.platform === 'win32' ? (process.env.CLAUDE_CODE_GIT_BASH_PATH || 'bash.exe') : '/bin/sh', ['-c', backup.previous.command], { stdio: ['pipe', 'inherit', 'inherit'] });
    child.stdin.on('error', () => {});
    child.stdin.end(input);
    const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', resolve); });
    process.exitCode = code ?? 1;
  }
}
