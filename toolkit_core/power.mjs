import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const exec = promisify(execFile);
const windows = process.platform === 'win32' || Boolean(process.env.WSL_DISTRO_NAME || process.env.WSL_INTEROP);
const declarations = `
using System;
using System.Runtime.InteropServices;
using System.Threading;
public static class ToolkitPower {
  [StructLayout(LayoutKind.Sequential)] public struct Status {
    public byte AC, Flag, Percent, Reserved;
    public uint Life, FullLife;
  }
  [DllImport("kernel32.dll")] public static extern bool GetSystemPowerStatus(out Status status);
  [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);
  public static void Hold() {
    if (SetThreadExecutionState(0x80000001) == 0) throw new Exception("Power assertion failed");
    try { Console.WriteLine("ready"); Console.Out.Flush(); Thread.Sleep(30000); } finally { SetThreadExecutionState(0x80000000); }
  }
}`;
function powershell(body) {
  const script = `Add-Type -TypeDefinition @'\n${declarations}\n'@\n${body}`;
  return ['powershell.exe', ['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')]];
}
export function assertionCommand() {
  if (windows) return powershell('[ToolkitPower]::Hold()');
  if (process.platform === 'darwin') return ['/usr/bin/caffeinate',['-i','-t','30']];
  throw new Error('Keep Awake currently supports macOS, Windows and WSL2');
}
export async function powerStatus(macParser, run) {
  if (!windows) {
    if (process.platform !== 'darwin') throw new Error('Unsupported power management platform');
    return macParser(await run('/usr/bin/pmset',['-g','batt']));
  }
  const [file,args] = powershell(`$status = New-Object ToolkitPower+Status
if (-not [ToolkitPower]::GetSystemPowerStatus([ref]$status)) { throw 'Power status unavailable' }
@{ac=[int]$status.AC;flag=[int]$status.Flag;percent=[int]$status.Percent} | ConvertTo-Json -Compress`);
  const {stdout} = await exec(file,args,{timeout:10000,maxBuffer:4096});
  const value=JSON.parse(stdout);
  if (![0,1].includes(value.ac) || value.flag === 255) throw new Error('Unknown power source');
  if ((value.flag & 128) !== 0 && value.ac === 1) return {pluggedIn:true,percent:100};
  if (!Number.isInteger(value.percent) || value.percent < 0 || value.percent > 100) throw new Error('Unknown battery capacity');
  return {pluggedIn:value.ac === 1,percent:value.percent};
}
