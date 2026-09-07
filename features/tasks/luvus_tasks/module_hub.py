"""Compatibility entrypoints: Tasks opens its dashboard without a picker tab."""
import json
import os

from .operations import open_console

ENV_KEYS = ("LUVUS_MODULE_CONFIG_DIR", "LUVUS_MODULE_STATE_DIR", "LUVUS_BIN_PATH", "LUVUS_SOCKET_PATH", "LUVUS_HOME", "LUVUS_SESSION")


def open_hub(app, context):
    return open_console(app.store, app.host(), "open", "", context)


def run(context, environment):
    # Previously generated hub-ui commands may still be queued in native tabs.
    for key, value in json.loads(environment).items():
        if key in ENV_KEYS:
            os.environ[key] = value
    from .ui import App
    app = App()
    try:
        open_hub(app, json.loads(context))
    finally:
        app.store.db.close()
    return 0
