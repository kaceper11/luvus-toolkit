#!/usr/bin/env node
import { python, moduleId } from '../../toolkit_core/runtime.mjs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { PROVIDERS, defaults, settings, snapshot, details, clean } from './model.mjs';
import { collectors } from './providers.mjs';
import { readJSON, failure, command } from './io.mjs';
import { context, start, daemon, requestRefresh, luvus, readSettings, MODULE_ID } from './runtime.mjs';
import { configureClaude, claudeFeed } from './claude.mjs';

export async function doctor() {
  console.log(`Luvus AI Usage 0.1.0 · Node ${process.versions.node}`);
  for (const key of ['LUVUS_BIN_PATH', 'LUVUS_SOCKET_PATH', 'LUVUS_PANE_ID']) console.log(`${key}: ${process.env[key] ? 'present' : 'missing'}`);
  for (const provider of Object.keys(PROVIDERS)) {
    try { const version = await command(defaults[`${provider}-executable`], ['--version']); console.log(`${PROVIDERS[provider]}: ${clean(version.trim().split('\n')[0])}`); }
    catch (e) { console.log(`${PROVIDERS[provider]}: ${failure(e)}`); }
  }
}

async function overlay(ctx, initial) {
  if (process.env.LUVUS_PANE_ID) await command(python, [fileURLToPath(new URL('./tab_titles.py', import.meta.url)), process.env.LUVUS_PANE_ID, `◔ ${PROVIDERS[initial] ?? initial} usage`]).catch(() => {});
  const config = await readSettings(ctx);
  const names = Object.keys(PROVIDERS).filter(p => config[`${p}-enabled`] || p === initial);
  if (!names.length) names.push('codex');
  let provider = names.includes(initial) ? initial : 'codex', offset = 0, message = '', drawing = false;
  const render = async () => {
    if (drawing) return; drawing = true;
    try {
      const config = await readSettings(ctx), cache = await readJSON(join(ctx.dir, 'usage.json'), {});
      const text = details(cache[provider] ?? snapshot(provider, 'Waiting for collection'), config);
      if (!process.stdin.isTTY) { process.stdout.write(text + '\n'); return; }
      const width = Math.max(20, (process.stdout.columns ?? 80) - 2);
      const lines = text.split('\n').flatMap(line => {
        const chars = [...line]; const wrapped = [];
        do { wrapped.push(chars.splice(0, width).join('')); } while (chars.length);
        return wrapped;
      });
      const height = Math.max(1, (process.stdout.rows ?? 24) - 5);
      offset = Math.min(offset, Math.max(0, lines.length - height));
      process.stdout.write('\x1b[2J\x1b[H' + `AI Usage  ${names.map((p, i) => `${i + 1}:${p === provider ? '[' + PROVIDERS[p] + ']' : PROVIDERS[p]}`).join(' ')}\n\n` + lines.slice(offset, offset + height).join('\n') + `\n\n${message || `↑↓/PgUp/PgDn scroll${names.length > 1 ? ` · 1–${names.length} tool` : ''} · r refresh${provider === 'claude' ? ' · c connect Claude' : ''} · q close`}`.slice(0, width + 2));
    } finally { drawing = false; }
  };
  await render(); if (!process.stdin.isTTY) return;
  process.stdin.setRawMode(true); process.stdin.resume();
  try {
    await new Promise(resolve => {
      const timer = setInterval(() => render().catch(() => {}), 5000);
      const key = async data => {
        const k = data.toString();
        if (['q', '\x03', '\x1b'].includes(k)) { clearInterval(timer); process.stdin.off('data', key); resolve(); return; }
        if (/^[1-5]$/.test(k) && names[Number(k) - 1]) { provider = names[Number(k) - 1]; offset = 0; }
        if (k === '\x1b[B' || k === 'j') offset++;
        if (k === '\x1b[A' || k === 'k') offset = Math.max(0, offset - 1);
        if (k === '\x1b[6~' || k === ' ') offset += Math.max(1, (process.stdout.rows ?? 24) - 5);
        if (k === '\x1b[5~') offset = Math.max(0, offset - Math.max(1, (process.stdout.rows ?? 24) - 5));
        try {
          if (k === 'r') { await requestRefresh(ctx); message = 'Refresh requested.'; }
          if (k === 'c' && provider === 'claude') message = await configureClaude('connect', ctx.root);
          await render();
        } catch (e) { message = clean(e.message); await render(); }
      };
      process.stdin.on('data', key);
    });
  } finally { process.stdin.setRawMode(false); process.stdin.pause(); process.stdout.write('\x1b[0m\x1b[2J\x1b[H'); }
}

async function main() {
  const [action, argument] = process.argv.slice(2);
  if (action === 'doctor') return doctor();
  if (action === 'claude-feed') {
    if (!argument) throw new Error('Missing module state directory');
    return claudeFeed(argument);
  }
  if (action === 'collect-worker') {
    if (!collectors[argument]) throw new Error('Unknown provider');
    let value;
    try { value = await collectors[argument](settings(JSON.parse(process.env.LUVUS_USAGE_CONFIG_JSON ?? '{}')), process.env.LUVUS_USAGE_ROOT); }
    catch (e) { value = { ...snapshot(argument, 'Collector'), error: failure(e) }; }
    if (process.send) process.send(value, () => process.disconnect());
    else console.log(JSON.stringify(value));
    return;
  }
  if (action === 'preview') {
    const { bar } = await import('./model.mjs');
    const data = { ...snapshot('codex', 'Illustrative preview'), windows: [{ id: 'example', label: 'Example window', usedPercent: 24, resetsAt: Date.now() + 7800000 }] };
    console.log(bar(data, settings()).content[0].text);
    console.log('\nRun this module through Luvus for live widgets; npm run doctor checks prerequisites.');
    return;
  }
  if (action === 'install') throw new Error('Install the complete luvus-toolkit repository through Luvus.');
  const ctx = context();
  if (action === 'start') return start(ctx);
  if (action === 'daemon') return daemon(ctx);
  if (action === 'refresh' || action === 'event') return requestRefresh(ctx, action === 'refresh');
  if (action === 'details') {
    const provider = process.env.LUVUS_MODULE_BAR_VALUE;
    const selected = Object.hasOwn(PROVIDERS, provider) ? provider : 'codex';
    const opened = await luvus(ctx, 'module', 'pane', 'open', MODULE_ID, `details-${selected}`, '--placement', 'tab');
    await command(python, [fileURLToPath(new URL('./tab_titles.py', import.meta.url)), String(opened.pane), `◔ ${PROVIDERS[selected]} usage`]).catch(() => {});
    return opened;
  }
  if (action === 'overlay') return overlay(ctx, argument);
  if (action === 'claude-connect' || action === 'claude-disconnect') {
    const message = await configureClaude(action === 'claude-connect' ? 'connect' : 'disconnect', ctx.root);
    console.log(message); await luvus(ctx, 'ui', 'toast', message); return;
  }
  if (action === 'uninstall') {
    await configureClaude('disconnect', ctx.root);
    console.log(JSON.stringify(await luvus(ctx, 'module', 'settings', moduleId, 'ai-usage-enabled', 'false'))); return;
  }
  throw new Error('Usage: node cli.mjs doctor | preview | install (or run a declared action from Luvus)');
}
if (process.argv[1] === fileURLToPath(import.meta.url)) main().catch(e => { console.error(clean(e.message)); process.exitCode = 1; });
