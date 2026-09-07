// Resolve native executables and standard npm shims without evaluating shell text.
import { existsSync, readFileSync } from 'node:fs';
import { delimiter, dirname, extname, isAbsolute, join, resolve, relative } from 'node:path';
export function executable(file, args, platform = process.platform) {
  if (platform !== 'win32') return [file, args];
  const folders = isAbsolute(file) || /[/\\]/.test(file) ? [''] : (process.env.PATH ?? '').split(delimiter);
  const extensions = extname(file) ? [''] : ['', '.exe', '.cmd', '.bat'];
  const selected = folders.flatMap(folder => extensions.map(extension => join(folder, file + extension))).find(existsSync);
  if (!selected) return [file, args];
  if (/\.[cm]?js$/i.test(selected)) return [process.execPath,[selected,...args]];
  if (!/\.(cmd|bat)$/i.test(selected)) return [selected,args];
  const shim = readFileSync(selected,'utf8');
  const match = shim.match(/%dp0%[\\/]([^"\r\n]+\.[cm]?js)/i);
  if (!match) throw new Error('Configure the native executable or JavaScript entrypoint instead of a custom batch wrapper');
  const script=resolve(dirname(selected),match[1].replaceAll('\\','/'));
  if (relative(dirname(selected),script).startsWith('..') || !existsSync(script)) throw new Error('Invalid npm executable shim');
  return [process.execPath,[script,...args]];
}
