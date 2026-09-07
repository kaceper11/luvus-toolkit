"""Kernel-owned advisory locks on Unix and Windows."""
import os
import time


def acquire(handle, wait=False):
    if os.name != 'nt':
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        return
    import msvcrt
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b'\0'); handle.flush()
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if not wait: raise BlockingIOError('Lock already held') from error
            time.sleep(.05)


def release(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)


if __name__ == '__main__':
    import sys
    deadline = time.monotonic() + float(sys.argv[2])
    with open(sys.argv[1], 'a+b') as handle:
        while True:
            try:
                acquire(handle)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print('busy', flush=True)
                    raise SystemExit(0)
                time.sleep(.05)
        print('ready', flush=True)
        sys.stdin.buffer.read()  # EOF releases the lock even after parent termination.
