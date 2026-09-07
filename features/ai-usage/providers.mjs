import { executable } from '../../toolkit_core/executable.mjs';
import { readdir, open } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { Rpc, command, readJSON, failure } from './io.mjs';
import { snapshot, metric, number, clean } from './model.mjs';

const add = (list, name, value, unit = '', scope = 'account', estimated = false) => {
  if (number(value) !== null || typeof value === 'string' && value.length) list.push(metric(name, typeof value === 'string' ? clean(value) : value, unit, scope, estimated));
};
const timestamp = v => typeof v === 'number' ? number(v) === null ? null : v * 1000 : Number.isFinite(Date.parse(v)) ? Date.parse(v) : null;
const sessionMetric = (s, name, value, unit = 'tokens', estimated = false) => add(s.metrics, name, value, unit, 'session', estimated);
function totals(out) {
  for (const name of ['Total tokens', 'Input tokens', 'Output tokens', 'Cached tokens', 'Cache read tokens', 'Cache write tokens', 'Reasoning tokens', 'Cost']) {
    const found = out.sessions.flatMap(s => s.metrics.filter(m => m.name === name));
    const units = [...new Set(found.map(m => m.unit))];
    for (const unit of units) {
      const group = found.filter(m => m.unit === unit);
      add(out.metrics, name, group.reduce((sum, m) => sum + m.value, 0), unit, 'local sessions', group.some(m => m.estimated));
    }
  }
  add(out.metrics, 'Sessions', out.sessions.length, '', 'local');
}

export function codexData(rate, usage = {}) {
  const out = snapshot('codex', 'Codex app-server (current login)');
  if (rate.accountId) out.account = createHash('sha256').update(rate.accountId).digest('hex').slice(0, 16);
  const buckets = rate.rateLimitsByLimitId && Object.keys(rate.rateLimitsByLimitId).length ? rate.rateLimitsByLimitId : rate.rateLimits ? { codex: rate.rateLimits } : {};
  for (const [id, b] of Object.entries(buckets)) {
    for (const key of ['primary', 'secondary']) {
      const w = b[key]; if (!w) continue;
      out.windows.push({ id: `${id}:${key}`, bucketLabel: clean(b.limitName ?? id), label: `${clean(b.limitName ?? id)} · ${w.windowDurationMins ? `${w.windowDurationMins} min` : key}`, usedPercent: number(w.usedPercent), resetsAt: timestamp(w.resetsAt), windowMinutes: number(w.windowDurationMins) });
    }
    add(out.metrics, `${id} plan`, b.planType);
    add(out.metrics, `${id} credits`, b.credits?.balance, 'credits');
    if (b.credits?.unlimited === true) add(out.metrics, `${id} credits`, 'Unlimited');
    if (b.rateLimitReachedType) add(out.metrics, `${id} limit state`, b.rateLimitReachedType);
  }
  add(out.metrics, 'Available earned resets', rate.rateLimitResetCredits?.availableCount);
  for (const credit of rate.rateLimitResetCredits?.credits ?? []) if (credit.expiresAt) add(out.metrics, 'Earned reset expires', new Date(credit.expiresAt * 1000).toISOString());
  for (const [key, title, unit] of [['lifetimeTokens', 'Lifetime tokens', 'tokens'], ['peakDailyTokens', 'Peak daily tokens', 'tokens'], ['longestRunningTurnSec', 'Longest turn', 'seconds'], ['currentStreakDays', 'Current streak', 'days'], ['longestStreakDays', 'Longest streak', 'days']]) add(out.metrics, title, usage.summary?.[key], unit);
  for (const day of [...(usage.dailyUsageBuckets ?? [])].sort((a, b) => String(b.startDate).localeCompare(String(a.startDate)))) add(out.metrics, `Tokens ${clean(day.startDate)}`, day.tokens, 'tokens');
  return out;
}

// Read only token events, never retain the transcript. Last cumulative value includes cached input already.
export async function codexSession(thread) {
  const s = { id: thread.id, project: thread.cwd, model: thread.model, updatedAt: thread.updatedAt * 1000, metrics: [] };
  if (!thread.path) return s;
  const file = await open(thread.path, 'r');
  try {
    const { size } = await file.stat();
    // ponytail: read the last 2 MiB; show unavailable if the token event is older, add indexed reads if needed.
    const start = Math.max(0, size - 2 * 1024 * 1024);
    const buffer = Buffer.alloc(size - start);
    await file.read(buffer, 0, buffer.length, start);
    const lines = buffer.toString('utf8').split('\n'); if (start) lines.shift();
    for (const line of lines.reverse()) {
      let event; try { event = JSON.parse(line); } catch { continue; }
      if (event.type !== 'event_msg' || event.payload?.type !== 'token_count' || !event.payload.info) continue;
      const info = event.payload.info, t = info.total_token_usage;
      if (!t) continue;
      for (const [key, name] of [['total_tokens', 'Total tokens'], ['input_tokens', 'Input tokens'], ['output_tokens', 'Output tokens'], ['cached_input_tokens', 'Cached tokens'], ['reasoning_output_tokens', 'Reasoning tokens']]) sessionMetric(s, name, t[key]);
      sessionMetric(s, 'Context window', info.model_context_window);
      sessionMetric(s, 'Last call tokens', info.last_token_usage?.total_tokens);
      break;
    }
  } finally { await file.close(); }
  return s;
}

export async function collectCodex(config) {
  const rpc = new Rpc(config['codex-executable'], ['app-server']);
  try {
    await rpc.initialize();
    const rate = await rpc.request('account/rateLimits/read');
    let usage = {}, usageError;
    try { usage = await rpc.request('account/usage/read'); } catch (e) { usageError = failure(e); }
    const out = codexData(rate, usage);
    if (usageError) out.notes.push(`Account token history: ${usageError}`);
    try {
      let cursor; const seen = new Set();
      do {
        const page = await rpc.request('thread/list', { cursor, limit: 100, sortKey: 'updated_at', modelProviders: [], useStateDbOnly: true });
        for (const t of page.data ?? []) {
          if (t.updatedAt * 1000 < Date.now() - config['period-days'] * 86400000) { cursor = null; break; }
          if (!seen.has(t.id)) { seen.add(t.id); out.sessions.push(await codexSession(t).catch(() => ({ id: t.id, metrics: [] }))); }
        }
        if ((page.data ?? []).some(t => t.updatedAt * 1000 < Date.now() - config['period-days'] * 86400000)) break;
        cursor = page.nextCursor;
      } while (cursor && seen.size < 200);
      if (cursor) out.notes.push('Local history limited to 200 recent sessions; totals are partial.');
    } catch (e) { out.notes.push(`Local sessions: ${failure(e)}`); }
    totals(out);
    out.notes.push('Local totals cover recorded, non-archived sessions active in the selected period; they are not account billing totals.');
    return out;
  } finally { rpc.close(); }
}

export function claudeData(input) {
  const out = snapshot('claude', 'Claude Code official status-line feed');
  for (const [id, w] of Object.entries(input.rate_limits ?? {})) {
    if (w && (number(w.used_percentage) !== null || w.resets_at)) out.windows.push({ id, label: id.replaceAll('_', ' '), usedPercent: number(w.used_percentage), resetsAt: timestamp(w.resets_at) });
  }
  const s = { id: clean(input.session_id), project: clean(input.workspace?.current_dir), model: clean(input.model?.display_name ?? input.model?.id), updatedAt: out.observedAt, metrics: [] };
  const context = input.context_window ?? {};
  sessionMetric(s, 'Input tokens', context.total_input_tokens);
  sessionMetric(s, 'Output tokens', context.total_output_tokens);
  if (number(context.total_input_tokens) !== null && number(context.total_output_tokens) !== null) sessionMetric(s, 'Total tokens', context.total_input_tokens + context.total_output_tokens);
  sessionMetric(s, 'Context used', context.used_percentage, '%');
  sessionMetric(s, 'Context window', context.context_window_size);
  sessionMetric(s, 'Last call cache read', context.current_usage?.cache_read_input_tokens);
  sessionMetric(s, 'Last call cache write', context.current_usage?.cache_creation_input_tokens);
  for (const [key, name, unit] of [['total_cost_usd', 'Cost', 'USD'], ['total_duration_ms', 'Duration', 'ms'], ['total_api_duration_ms', 'API duration', 'ms'], ['total_lines_added', 'Lines added', 'lines'], ['total_lines_removed', 'Lines removed', 'lines']]) sessionMetric(s, name, input.cost?.[key], unit);
  for (const [key, value] of Object.entries(input.prompt_cache ?? {})) if (typeof value === 'number') sessionMetric(s, `Prompt cache ${key}`, value, '');
  add(s.metrics, 'Effort', input.effort?.level, '', 'session');
  if (typeof input.fast_mode === 'boolean') add(s.metrics, 'Fast mode', String(input.fast_mode), '', 'session');
  if (typeof input.thinking?.enabled === 'boolean') add(s.metrics, 'Thinking', String(input.thinking.enabled), '', 'session');
  if (s.id) out.sessions.push(s);
  out.notes.push('Quota is the last value reported by a Claude session, not an independent account poll. Session cost is Claude’s reported API-equivalent cost, not a subscription charge.');
  return out;
}
export async function collectClaude(config, root) {
  const dir = join(root, 'claude');
  const names = await readdir(dir).catch(e => { if (e.code === 'ENOENT') return []; throw e; });
  const feeds = (await Promise.all(names.filter(n => /^[a-f0-9]{64}\.json$/.test(n)).map(n => readJSON(join(dir, n))))).filter(Boolean).sort((a, b) => b.observedAt - a.observedAt);
  if (!feeds.length) {
    const out = snapshot('claude', 'Claude Code official status-line feed');
    out.error = 'Setup required — connect Claude status line';
    out.notes.push('Run the module’s “Connect Claude status line” action, then use Claude Code. Existing status-line output is preserved.');
    return out;
  }
  const out = { ...feeds[0], metrics: [], sessions: feeds.filter(f => f.observedAt >= Date.now() - config['period-days'] * 86400000).flatMap(f => f.sessions) };
  totals(out); return out;
}

export function opencodeSession(data) {
  const info = data.info ?? {};
  const s = { id: info.id, project: info.directory, updatedAt: info.time?.updated, metrics: [] };
  const messages = [...new Map((data.messages ?? []).filter(m => m.info?.role === 'assistant').map(m => [m.info.id, m.info])).values()];
  for (const [name, get, unit] of [
    ['Input tokens', m => m.tokens?.input, 'tokens'], ['Output tokens', m => m.tokens?.output, 'tokens'],
    ['Reasoning tokens', m => m.tokens?.reasoning, 'tokens'], ['Cache read tokens', m => m.tokens?.cache?.read, 'tokens'],
    ['Cache write tokens', m => m.tokens?.cache?.write, 'tokens'], ['Cost', m => m.cost, 'USD'],
  ]) {
    const known = messages.map(get).filter(v => number(v) !== null);
    if (known.length) sessionMetric(s, name, known.reduce((a, b) => a + b, 0), unit);
  }
  // OpenCode normalizes input separately from cache reads/writes. Reasoning is part of output.
  const tokenMetrics = ['Input tokens', 'Output tokens', 'Cache read tokens', 'Cache write tokens'];
  if (tokenMetrics.every(name => s.metrics.some(m => m.name === name))) sessionMetric(s, 'Total tokens', s.metrics.filter(m => tokenMetrics.includes(m.name)).reduce((a, m) => a + m.value, 0));
  s.model = [...new Set(messages.map(m => `${m.providerID ?? '?'}/${m.modelID ?? '?'}`))].join(', ');
  for (const model of [...new Set(messages.map(m => `${m.providerID ?? '?'}/${m.modelID ?? '?'}`))]) {
    const calls = messages.filter(m => `${m.providerID ?? '?'}/${m.modelID ?? '?'}` === model);
    for (const [label, get, unit] of [['Input', m => m.tokens?.input, 'tokens'], ['Output', m => m.tokens?.output, 'tokens'], ['Cost', m => m.cost, 'USD']]) {
      const values = calls.map(get).filter(v => number(v) !== null);
      if (values.length) sessionMetric(s, `${model} · ${label}`, values.reduce((a, b) => a + b, 0), unit);
    }
  }
  return s;
}
export async function collectOpenCode(config) {
  const out = snapshot('opencode', 'OpenCode session list/export');
  const listing = await command(config['opencode-executable'], ['session', 'list', '--format', 'json']);
  const sessions = listing.trim() ? JSON.parse(listing) : [];
  if (!Array.isArray(sessions)) throw new Error('Invalid session list');
  const recent = [...new Map(sessions.filter(s => (s.time?.updated ?? s.updated) >= Date.now() - config['period-days'] * 86400000).map(s => [s.id, s])).values()];
  for (const s of recent.slice(0, 100)) {
    try { out.sessions.push(opencodeSession(JSON.parse(await command(config['opencode-executable'], ['export', s.id])))); }
    catch { out.notes.push(`Could not read session ${clean(s.id)}; totals are partial.`); }
  }
  if (recent.length > 100) out.notes.push('Limited to 100 sessions; totals are partial.');
  totals(out);
  out.notes.push('Root-session lifetime totals for sessions active in the selected period; the CLI listing excludes child sessions. Recorded model cost is not necessarily your invoice. Provider account allowances are shown under their own tools, not duplicated here.');
  return out;
}

export function museSession(info, events, catalog = []) {
  const s = { id: info.sessionId, project: info.workspaceRoot, model: info.modelId, updatedAt: Date.parse(info.updatedAt), metrics: [] };
  const unique = [...new Map(events.map(e => [e.params?.viewCursor, e])).values()];
  const usage = unique.filter(e => e.method === 'session/tokenUsage').map(e => e.params);
  const last = usage.at(-1);
  if (last) {
    sessionMetric(s, 'Total tokens', last.cumulative?.totalTokens);
    sessionMetric(s, 'Input tokens', last.cumulative?.promptTokens);
    sessionMetric(s, 'Output tokens', last.cumulative?.outputTokens);
    for (const [key, name] of [['cachedTokens', 'Cached tokens'], ['cacheReadTokens', 'Cache read tokens'], ['cacheWriteTokens', 'Cache write tokens'], ['reasoningTokens', 'Reasoning tokens']]) {
      const known = usage.map(u => u.usage?.[key]).filter(v => number(v) !== null);
      if (known.length) sessionMetric(s, name, known.reduce((a, b) => a + b, 0));
    }
    const amounts = [];
    for (const u of usage) {
      const price = catalog.find(m => m.modelId === u.modelId)?.cost;
      const cached = u.usage?.cacheReadTokens ?? u.usage?.cachedTokens;
      if (!price || number(cached) === null || cached > u.promptTokens || number(u.promptTokens) === null || number(u.usage?.outputTokens) === null) continue;
      if (![price.input, price.output, price.cached].every(v => v !== null && v !== '' && Number.isFinite(Number(v)) && Number(v) >= 0)) continue;
      amounts.push({ currency: price.currency ?? 'currency unknown', amount: ((u.promptTokens - cached) * Number(price.input) + cached * Number(price.cached) + u.usage.outputTokens * Number(price.output)) / 1e6 });
    }
    if (amounts.length === usage.length && new Set(amounts.map(a => a.currency)).size === 1) sessionMetric(s, 'Cost', amounts.reduce((a, v) => a + v.amount, 0), amounts[0].currency, true);
    sessionMetric(s, 'Model calls', usage.length, 'calls');
    const durations = usage.map(u => u.durationMs).filter(v => number(v) !== null);
    if (durations.length) sessionMetric(s, 'Measured model time', durations.reduce((a, b) => a + b, 0), 'ms');
    for (const model of [...new Set(usage.map(u => u.modelId).filter(Boolean))]) {
      const calls = usage.filter(u => u.modelId === model);
      if (calls.every(u => number(u.totalTokens) !== null)) sessionMetric(s, `${model} · Total tokens`, calls.reduce((a, u) => a + u.totalTokens, 0));
    }
  }
  const completed = unique.filter(e => e.method === 'turn/completed');
  if (completed.length) sessionMetric(s, 'Completed turns', completed.length, 'turns');
  const firstToken = completed.map(e => e.params.timeToFirstTokenMs).filter(v => number(v) !== null);
  if (firstToken.length) sessionMetric(s, 'Mean time to first token', firstToken.reduce((a, b) => a + b, 0) / firstToken.length, 'ms');
  const context = unique.filter(e => e.method === 'session/contextUsage').at(-1)?.params;
  sessionMetric(s, 'Context tokens', context?.usedTokens);
  sessionMetric(s, 'Context window', context?.windowTokens);
  add(s.metrics, 'Context pressure', context?.pressure, '', 'session');
  return s;
}
export async function collectMuse(config) {
  const rpc = new Rpc(config['muse-executable'], ['serve']);
  try {
    await rpc.initialize();
    const out = snapshot('muse', 'Muse MSP read-only session history');
    const models = await rpc.request('model/list').catch(() => ({}));
    let cursor; const seen = new Set();
    do {
      const page = await rpc.request('session/list', { limit: 100, ...(cursor ? { cursor } : {}), updatedAfter: new Date(Date.now() - config['period-days'] * 86400000).toISOString() });
      for (const s of page.sessions) {
        if (seen.has(s.sessionId)) continue; seen.add(s.sessionId);
        const events = []; let viewCursor;
        do {
          const p = await rpc.request('view/page', { sessionId: s.sessionId, limit: 1000, direction: 'forward', ...(viewCursor ? { cursor: viewCursor } : {}) });
          events.push(...p.events); viewCursor = p.nextCursor;
        } while (viewCursor && events.length < 10000);
        if (viewCursor) out.notes.push(`Session ${clean(s.sessionId)} exceeded 10,000 events; totals are partial.`);
        out.sessions.push(museSession(s, events, models.models ?? []));
      }
      cursor = page.nextCursor;
    } while (cursor && seen.size < 200);
    if (cursor) out.notes.push('Limited to 200 sessions; totals are partial.');
    totals(out);
    out.notes.push('Account quota/reset is unavailable through verified supported interfaces. Local totals cover each session’s own calls; child rollups are not added again. Cost estimates use current catalog prices, not historical billing.');
    return out;
  } finally { rpc.close(); }
}

export function copilotData(data) {
  const out = snapshot('copilot', 'Official Copilot SDK account.getQuota');
  for (const [id, w] of Object.entries(data.quotaSnapshots ?? {})) {
    out.windows.push({ id, label: id.replaceAll('_', ' '), usedPercent: typeof w.remainingPercentage === 'number' && Number.isFinite(w.remainingPercentage) ? Math.max(0, 100 - w.remainingPercentage) : null, unlimited: w.isUnlimitedEntitlement === true || w.entitlementRequests === -1, used: number(w.usedRequests), limit: number(w.entitlementRequests), unit: 'requests', resetsAt: timestamp(w.resetDate) });
    add(out.metrics, `${id} overage`, w.overage, 'requests');
    if (typeof w.usageAllowedWithExhaustedQuota === 'boolean') add(out.metrics, `${id} usage after exhaustion`, String(w.usageAllowedWithExhaustedQuota));
  }
  out.notes.push('Account quota covers the current Copilot login. Session events from other running CLI processes are not available on this connection; experimental session metrics are not called.');
  return out;
}
export async function collectCopilot(config) {
  // Require an explicitly installed runtime; never download or start a bundled agent implicitly.
  await command(config['copilot-executable'], ['--version']);
  const { CopilotClient, RuntimeConnection } = await import('@github/copilot-sdk');
  const [path, args] = executable(config['copilot-executable'], []);
  const client = new CopilotClient({ connection: RuntimeConnection.forStdio({ path, args }), useLoggedInUser: true, logLevel: 'none' });
  try { await client.start(); return copilotData(await client.rpc.account.getQuota({})); }
  finally { await client.stop(); }
}

export const collectors = { codex: collectCodex, claude: collectClaude, opencode: collectOpenCode, muse: collectMuse, copilot: collectCopilot };
