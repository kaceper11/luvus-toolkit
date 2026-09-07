// Opt-in native assertion lifetime check. This briefly prevents idle sleep.
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { setTimeout as delay } from 'node:timers/promises';
import { python, root } from '../toolkit_core/runtime.mjs';
import { join } from 'node:path';
const exec=promisify(execFile);
const moduleURL=new URL('../toolkit_core/power.mjs',import.meta.url).href;
const source=`import {spawn} from 'node:child_process';import {assertionCommand} from ${JSON.stringify(moduleURL)};const [file,args]=assertionCommand();const child=spawn(file,args,{stdio:'ignore'});child.once('spawn',()=>{console.log(child.pid);child.unref();});child.once('error',e=>{console.error(e.message);process.exitCode=1;});`;
const parent=spawn(process.execPath,['--input-type=module','-e',source],{stdio:['ignore','pipe','inherit']});
let output='';parent.stdout.on('data',chunk=>output+=chunk);
const code=await new Promise(resolve=>parent.once('exit',resolve));
if(code!==0)throw new Error('Assertion helper failed');
const pid=Number(output.trim());
if(!Number.isInteger(pid)||pid<=0)throw new Error('Missing owned assertion PID');
const describe=async()=>JSON.parse((await exec(python,[join(root,'toolkit_core/process.py'),String(pid)])).stdout);
const identity=await describe();
if(!identity)throw new Error('Native assertion exited before its lifetime could be observed');
const deadline=Date.now()+40000;
while(Date.now()<deadline){
  const current=await describe();
  if(!current||current.created!==identity.created){console.log('PASS: owned assertion exited after parent termination within 40 seconds. Check actual power requests on the host separately.');process.exit(0);}
  await delay(500);
}
throw new Error('Owned assertion exceeded its bounded lifetime');
