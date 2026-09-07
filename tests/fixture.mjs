import { writeFile } from 'node:fs/promises';
import { basename } from 'node:path';
export async function writeExecutable(file, source, options = {}) {
  if (process.platform !== 'win32') return writeFile(file, source, options);
  await writeFile(file + '.js', source, options);
  await writeFile(file + '.cmd', `@echo off\n"%dp0%\\${basename(file)}.js" %*\n`);
}
