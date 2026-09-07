import { executable } from './executable.mjs';
// Native Luvus boundary for the two Node features; no binary/socket substitution.
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { stat, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
const exec = promisify(execFile);
export const root = dirname(dirname(fileURLToPath(import.meta.url)));
export const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export const moduleId = 'kacper.toolkit';

export async function native(ctx, args, options = {}) {
  const [file, argv] = executable(ctx.binary, args);
  const { stdout } = await exec(file, argv, { timeout: 10000, maxBuffer: 1048576, ...options });
  const data = JSON.parse(stdout);
  if (data.error) throw new Error(data.error.message ?? data.error.code);
  return data.result ?? data;
}
export async function featureSettings(ctx, feature) {
  const result = await native(ctx, ['module', 'settings', moduleId]);
  return Object.fromEntries(result.settings.filter(s => !s.secret && s.key.startsWith(feature + '-')).map(s => [s.key.slice(feature.length + 1), s.value]));
}
export async function namespace(feature, value, kind = 'actions') {
  const id = feature + '-' + value;
  const catalog = JSON.parse(await readFile(join(root, 'toolkit_core/catalog.json'), 'utf8'));
  return process.platform === 'win32' && catalog[kind].includes(id + '-windows') ? id + '-windows' : id;
}
async function ui(feature, value) {
  if (Array.isArray(value)) return Promise.all(value.map(v => ui(feature, v)));
  if (value && typeof value === 'object') {
    return Object.fromEntries(await Promise.all(Object.entries(value).map(async ([k,v]) => [k, k === 'action' && v ? await namespace(feature, v) : await ui(feature,v)])));
  }
  return value;
}
export async function argsFor(feature, args) {
  args = [...args];
  if (args[0] === 'module') {
    args = args.map(v => ['kacper.ai-usage','kacper.keep-awake'].includes(v) ? moduleId : v);
    if (args[1] === 'pane' && args[2] === 'open') args[4] = await namespace(feature,args[4],'panes');
  }
  if (args[0] === 'bar') {
    if (args.includes('--id')) { const i=args.indexOf('--id')+1;args[i]=await namespace(feature,args[i],'bars'); }
    for (const flag of ['--content','--compact-content']) {
      if (args.includes(flag)) { const i=args.indexOf(flag)+1;args[i]=JSON.stringify(await ui(feature,JSON.parse(args[i]))); }
    }
  }
  return args;
}
export async function featureInfo(ctx, feature, result) {
  const values = await featureSettings(ctx, feature);
  const info = result.module ?? result;
  info.enabled = info.enabled && values.enabled !== false;
  return result;
}
export async function sessionIdentity(ctx) {
  if (process.platform !== 'win32') {
    const info = await stat(ctx.socket);
    return `${info.ino}:${info.birthtimeMs}`;
  }
  // Named pipes are not filesystem sockets. Use the server's generation instead.
  const generation = (await rpc(ctx, 'terminal.backend.inventory')).server_generation;
  if (!generation) throw new Error('Missing server generation');
  return generation;
}
export function rpc(ctx, method, params = {}) {
  return new Promise((resolve,reject) => {
    const [file, argv] = executable(ctx.binary, ['uhp','proxy']);
    const child=spawn(file,argv,{stdio:['pipe','pipe','pipe']});
    let output='',error='',finished=false;
    const done=(err,value)=>{if(finished)return;finished=true;clearTimeout(timer);err?reject(err):resolve(value);};
    const timer=setTimeout(()=>{child.kill();done(new Error('Luvus request timed out'));},10000);
    child.stdout.on('data',chunk=>{output+=chunk;if(Buffer.byteLength(output)>1048576){child.kill();done(new Error('Luvus response exceeds limit'));}});
    child.stderr.on('data',chunk=>{error=(error+chunk).slice(-4096);});
    child.on('error',done);
    child.on('close',code=>{try{if(code)throw new Error(error);const data=JSON.parse(output);if(data.error)throw new Error(data.error.message);done(null,data.result);}catch(e){done(e);}});
    child.stdin.on('error',done);
    child.stdin.end(JSON.stringify({id:'toolkit',method,params})+'\n');
  });
}
export async function sameSession(ctx, identity) {
  try { return await sessionIdentity(ctx) === identity; } catch { return false; }
}
export function stopTree(child) {
  if (!child || child.exitCode != null || child.signalCode != null) return;
  if (process.platform === 'win32') {
    // A live owned ChildProcess, never a PID loaded from a state file.
    execFile('taskkill.exe',['/PID',String(child.pid),'/T','/F'],()=>{});
  } else {
    try { process.kill(-child.pid,'SIGTERM'); } catch { child.kill('SIGTERM'); }
  }
}
