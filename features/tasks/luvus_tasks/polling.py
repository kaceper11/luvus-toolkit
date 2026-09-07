"""One session-owned PR/CI reader, launched by the native module startup hook."""
import hashlib
import os
from pathlib import Path
import time
import uuid

from . import MODULE_ID
from .core import TaskError
from .workflow import poll_prs


def watch(app):
    if not os.environ.get("LUVUS_SOCKET_PATH") or not os.environ.get("LUVUS_BIN_PATH"):
        raise TaskError("PR monitoring needs the inherited Luvus socket and binary.")
    host = app.host()
    generation = host.call("terminal.backend.inventory")["server_generation"]
    key = hashlib.sha256((os.environ["LUVUS_SOCKET_PATH"] + str(generation)).encode()).hexdigest()
    # OS ownership releases on exit/crash; leave the inode in place for concurrent starters.
    with (app.store.root / ("pr-watcher-" + key + ".lock")).open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                if lock.tell() == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            return 0

        # Enabling a module retires native docks but preserves the SQLite paint cache.
        from .operations import session_key
        app.store.set_preference("dock:" + session_key() + ":paint", {})
        workflow_owner = uuid.uuid4().hex

        def active():
            info = host.call("module.info", id=MODULE_ID)
            return (info.get("enabled") and info.get("runnable")
                    and Path(info["root"]).resolve() == Path(__file__).resolve().parent.parent
                    and host.call("terminal.backend.inventory")["server_generation"] == generation)

        try:
            while active():
                app.store.set_preference("workflow-helper:" + session_key(), {"at": time.time(), "generation": generation})
                from .bounded import tick
                try:
                    tick(app.store, host, workflow_owner)
                except TaskError:
                    pass  # Another short control/report operation owns the workflow lock.
                if not active():
                    break
                app.dock(False)
                # ponytail: serial provider reads; one group per tick keeps shutdown/queues fair.
                poll_prs(app.store, limit=1)
                if not active():
                    break
                app.dock(False)
                time.sleep(5)
        except TaskError:
            # A disconnected/replaced session must never fall back to another socket.
            return 1
    return 0
