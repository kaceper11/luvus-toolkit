// Opt-in native assertion lifetime check. This briefly prevents idle sleep.
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { setTimeout as delay } from 'node:timers/promises';
import { python, root } from '../toolkit_core/runtime.mjs';
import { join } from 'node:path';
const exec=promisify(execFile);
const moduleURL=new URL('../toolkit_core/power.mjs',import.meta.url).href;
const source=`import {spawn} from 'node:child_process';import {assertionCommand} from ${JSON.stringify(moduleURL)};
const [file,args]=assertionCommand();const child=spawn(file,args,{stdio:['ignore','pipe','inherit']});
const ready=()=>console.log(child.pid);
if(file!=='/usr/bin/caffeinate')child.stdout.once('data',ready);else child.once('spawn',()=>setTimeout(ready,500));
child.once('exit',code=>{console.error('Native assertion exit:',code);process.exit(code||1);});
child.once('error',e=>{console.error(e.message);process.exit(1);});
process.stdin.resume();process.stdin.once('end',()=>{child.stdout.destroy();child.unref();});`;
const parent=spawn(process.execPath,['--input-type=module','-e',source],{stdio:['pipe','pipe','inherit']});
const output=await Promise.race([
  new Promise((resolve,reject)=>{parent.stdout.once('data',chunk=>resolve(String(chunk)));parent.once('exit',code=>reject(new Error('Assertion parent exited early: '+code)));}),
  delay(15000).then(()=>{parent.stdin.end();throw new Error('Native assertion did not become ready');})
]);
const pid=Number(output.trim());
if(!Number.isInteger(pid)||pid<=0)throw new Error('Missing owned assertion PID');
const describe=async()=>JSON.parse((await exec(python,[join(root,'toolkit_core/process.py'),String(pid)])).stdout);
let identity;
try {identity=await describe();} finally {parent.stdin.end();}
if(!identity)throw new Error('Native assertion exited before its lifetime could be observed');
await new Promise(resolve=>parent.once('exit',resolve));
const deadline=Date.now()+40000;
while(Date.now()<deadline){
  const current=await describe();
  if(!current||current.created!==identity.created){console.log('PASS: owned assertion exited after parent termination within 40 seconds. Check actual power requests on the host separately.');process.exit(0);}
  await delay(500);
}
throw new Error('Owned assertion exceeded its bounded lifetime');
