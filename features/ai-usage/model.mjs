export const PROVIDERS = { codex: 'Codex', claude: 'Claude', opencode: 'OpenCode', muse: 'Muse', copilot: 'Copilot' };
export const defaults = {
  'display': 'used', 'density': 'standard', 'refresh-minutes': 5, 'period-days': 7,
  'warning-percent': 80, 'critical-percent': 95, 'notifications': false,
  ...Object.fromEntries(Object.keys(PROVIDERS).flatMap(p => [[`${p}-enabled`, p === 'codex'], [`${p}-executable`, p], [`${p}-priority`, p === 'codex' ? 90 : 60]])),
  'claude-executable': 'claude',
};
export function settings(raw = {}) {
  const s = { ...defaults, ...raw };
  for (const [key, value] of Object.entries(defaults)) {
    if (typeof value === 'boolean') s[key] = s[key] === true || s[key] === 'true';
    if (typeof value === 'number') s[key] = Number.isFinite(Number(s[key])) ? Number(s[key]) : value;
  }
  s['refresh-minutes'] = Math.max(1, Math.min(60, s['refresh-minutes']));
  s['period-days'] = Math.max(1, Math.min(365, s['period-days']));
  s['warning-percent'] = Math.max(1, Math.min(100, s['warning-percent']));
  s['critical-percent'] = Math.max(s['warning-percent'], Math.min(100, s['critical-percent']));
  for (const p of Object.keys(PROVIDERS)) s[`${p}-priority`] = Math.max(0, Math.min(100, s[`${p}-priority`]));
  return s;
}
export const number = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null;
export const clean = v => String(v ?? '').replace(/[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/g, '').slice(0, 240);
export const metric = (name, value, unit = '', scope = 'account', estimated = false) => ({ name, value, unit, scope, estimated });
export function snapshot(provider, source) { return { provider, source, observedAt: Date.now(), windows: [], metrics: [], sessions: [], notes: [] }; }
export function countdown(reset, now = Date.now()) {
  if (!Number.isFinite(reset)) return 'reset unknown';
  const mins = Math.ceil((reset - now) / 60000);
  if (mins <= 0) return 'awaiting update';
  if (mins >= 1440) return `${Math.floor(mins / 1440)}d ${Math.floor(mins % 1440 / 60)}h`;
  return mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
}
export function compactNumber(n) { return Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(n); }
export function stale(snap, config, now = Date.now()) { return now - snap.observedAt > Math.max(600000, config['refresh-minutes'] * 120000); }
export function primaryWindow(snap, now = Date.now()) {
  const main = snap.provider === 'codex' ? snap.windows.filter(w => w.id?.startsWith('codex:')) : [];
  const windows = main.length ? main : snap.windows;
  const usable = windows.filter(w => !w.unlimited && number(w.usedPercent) !== null && (!w.resetsAt || w.resetsAt > now));
  return usable.sort((a, b) => b.usedPercent - a.usedPercent)[0] ?? windows[0];
}
export function bar(snap, config, now = Date.now()) {
  const w = primaryWindow(snap, now);
  const bucket = w?.bucketLabel && w.bucketLabel !== 'codex' ? /spark/i.test(w.bucketLabel) ? 'Spark' : clean(w.bucketLabel).slice(0, 24) : '';
  const name = `${PROVIDERS[snap.provider]}${bucket ? ` ${bucket}` : ''}`;
  const status = !snap.error ? 'waiting' : snap.error.startsWith('Setup required') ? 'connect' : snap.error.startsWith('Not installed') ? 'not installed' : snap.error.startsWith('Sign in') ? 'sign in' : snap.error.startsWith('Timed out') ? 'timeout' : 'unavailable';
  let value = status, tone = 'muted', short = status === 'not installed' ? 'missing' : status;
  if (w) {
    const expired = w.resetsAt && w.resetsAt <= now;
    const used = number(w.usedPercent);
    value = w.unlimited ? 'unlimited' : expired ? 'awaiting update' : used === null ? 'usage unknown' : `${Math.round(config.display === 'remaining' ? Math.max(0, 100 - used) : used)}% ${config.display}`;
    short = value.replace(' used', '').replace(' remaining', ' left');
    const window = w.windowMinutes ? w.windowMinutes >= 1440 ? `${w.windowMinutes / 1440}d` : w.windowMinutes >= 60 ? `${w.windowMinutes / 60}h` : `${w.windowMinutes}m` : w.id === 'five_hour' ? '5h' : w.id === 'seven_day' ? '7d' : '';
    if (window) { const label = window === '7d' ? 'Weekly:' : window; value = `${label} ${value}`; short = `${label} ${short}`; }
    if (w.resetsAt && !expired) { const reset = ` · resets in ${countdown(w.resetsAt, now)}`; value += reset; short += reset; }
    if (!expired && !w.unlimited && used !== null) tone = used >= config['critical-percent'] ? 'error' : used >= config['warning-percent'] ? 'warning' : 'normal';
  } else {
    const tokens = snap.metrics.find(m => m.name === 'Total tokens');
    if (tokens) { value = `${compactNumber(tokens.value)} tok`; short = value; tone = 'normal'; }
  }
  if (stale(snap, config, now) || snap.error && snap.metrics.length + snap.windows.length > 0) { value += ' · stale'; short += ' · stale'; tone = 'muted'; }
  const segment = text => ({ type: 'text', text, tone, action: 'details', value: snap.provider });
  return { content: [segment(`${name} ${config.density === 'compact' ? short : value}`)], compact: [segment(`${name} ${short}`)] };
}
export function details(snap, config, now = Date.now()) {
  const lines = [`${PROVIDERS[snap.provider]} usage`, '', `Source: ${snap.source}`, `Updated: ${new Date(snap.observedAt).toLocaleString()}${stale(snap, config, now) ? ' (stale)' : ''}`];
  if (snap.error) lines.push(snap.error);
  for (const w of snap.windows) {
    lines.push('', `${clean(w.label)}: ${w.unlimited ? 'unlimited' : w.usedPercent == null ? 'usage unknown' : `${w.usedPercent.toFixed(1)}% used`}`);
    if (w.used != null) lines.push(`  ${w.used} / ${w.limit ?? '?'} ${w.unit ?? ''}`);
    if (w.resetsAt) lines.push(`  Resets ${new Date(w.resetsAt).toLocaleString()} (${countdown(w.resetsAt, now)})`);
  }
  const daily = m => /^Tokens \d{4}-/.test(m.name);
  const formatted = m => `${m.scope} · ${clean(m.name)}: ${typeof m.value === 'number' ? Intl.NumberFormat('en', { maximumFractionDigits: 4 }).format(m.value) : clean(m.value)} ${m.unit}${m.estimated ? ' (estimate)' : ''}`;
  for (const m of snap.metrics.filter(m => !daily(m))) lines.push(formatted(m));
  if (snap.sessions.length) lines.push('', 'Local sessions (lifetime totals for sessions active in the selected period):');
  for (const s of snap.sessions) {
    lines.push(`  ${clean(s.id)} · ${clean(s.model ?? 'model unknown')} · ${clean(s.project ?? '')}`);
    for (const m of s.metrics ?? []) lines.push(`    ${clean(m.name)}: ${m.value} ${m.unit}${m.estimated ? ' (estimate)' : ''}`);
  }
  lines.push('', ...snap.notes);
  if (snap.metrics.some(daily)) lines.push('', 'Daily account activity (newest first)', ...snap.metrics.filter(daily).map(formatted));
  return lines.map(clean).join('\n');
}
