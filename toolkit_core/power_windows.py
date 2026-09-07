"""Native Windows power status and a bounded idle-sleep assertion."""
import ctypes
import json
import sys
import time

class Status(ctypes.Structure):
    _fields_ = [('ac', ctypes.c_byte), ('flag', ctypes.c_ubyte), ('percent', ctypes.c_ubyte),
                ('reserved', ctypes.c_byte), ('life', ctypes.c_uint), ('full_life', ctypes.c_uint)]

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
if sys.argv[1] == 'status':
    value = Status()
    if not kernel.GetSystemPowerStatus(ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    print(json.dumps({'ac': value.ac, 'flag': value.flag, 'percent': value.percent}))
elif sys.argv[1] == 'hold':
    kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel.SetThreadExecutionState.restype = ctypes.c_uint
    if not kernel.SetThreadExecutionState(0x80000001):
        raise OSError('Power assertion failed')
    try:
        print('ready', flush=True)
        time.sleep(30)
    finally:
        kernel.SetThreadExecutionState(0x80000000)
else:
    raise ValueError('Expected status or hold')
