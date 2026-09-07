import test from 'node:test';
import assert from 'node:assert/strict';
import { argsFor, namespace } from '../toolkit_core/runtime.mjs';
import { assertionCommand } from '../toolkit_core/power.mjs';

test('feature UI names are translated while literal text is preserved', async () => {
  const args=await argsFor('ai-usage',['bar','push','--id','codex','--content',JSON.stringify([{type:'text',text:'details',action:'details'}])]);
  assert.equal(args[args.indexOf('--id')+1],'ai-usage-codex');
  const content=JSON.parse(args[args.indexOf('--content')+1]);
  assert.equal(content[0].text,'details');
  assert.equal(content[0].action,await namespace('ai-usage','details'));
});
test('pane invocation targets the bundle and the platform entrypoint', async () => {
  const args=await argsFor('ai-usage',['module','pane','open','kacper.ai-usage','details-codex']);
  assert.equal(args[3],'kacper.toolkit');
  assert.equal(args[4],await namespace('ai-usage','details-codex','panes'));
});
test('power assertions have a bounded native implementation', () => {
  const [file,args]=assertionCommand();
  if(file==='powershell.exe') {
    const code=Buffer.from(args.at(-1),'base64').toString('utf16le');
    assert.match(code,/Thread.Sleep\(30000\)/);
    assert.match(code,/finally/);
  } else assert.deepEqual(args,['-i','-t','30']);
});

test('npm shim resolution preserves argument bytes and rejects arbitrary batch files', async () => {
  const { executable } = await import('../toolkit_core/executable.mjs');
  const { mkdtemp, writeFile, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const folder=await mkdtemp(join(tmpdir(),'toolkit shim '));
  try {
    const file=join(folder,'agent.cmd');
    await writeFile(join(folder,'agent.js'),'');
    await writeFile(file,'@echo off\n"%dp0%\\agent.js" %*\n');
    const args=['spaces ✓','$literal','&unchanged'];
    assert.deepEqual(executable(file,args,'win32'),[process.execPath,[join(folder,'agent.js'),...args]]);
    await writeFile(file,'echo arbitrary batch');
    assert.throws(()=>executable(file,args,'win32'),/native executable/);
  } finally {await rm(folder,{recursive:true,force:true});}
});
