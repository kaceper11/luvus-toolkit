"""Public owner API translation checks; no real processes or services launched."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import commands_adapter as adapter
import project_launcher as project


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = str(Path(self.temp.name).resolve())
        self.definition = {"id": "web", "name": "Web", "kind": "service", "category": "custom",
                           "argv": ["server"], "urls": ["http://localhost:3210"]}
        self.session = {"socket": "/test/socket", "generation": "a" * 32}
        self.calls = []
        self.run = {"id": "run", "root": self.cwd, "definition": self.definition,
                    "session": self.session, "state": "running", "readiness": "ready", "updated": time.time()}

    def owner(self, entrypoint, cwd, action, **params):
        self.assertEqual(cwd, self.cwd)
        self.calls.append((action, params))
        if action == "config":
            return {"version": 1, "projects": {self.cwd: {"commands": [self.definition]}}}
        if action == "status":
            return [deepcopy(self.run)]
        if action == "run":
            self.assertEqual(params["reviewed"], self.definition)
            self.assertEqual(params["session"], self.session)
            return deepcopy(self.run)
        self.fail(action)

    def invoke(self, operation, **params):
        with patch.dict(os.environ, {"LUVUS_SOCKET_PATH": self.session["socket"]}):
            return adapter.adapt({"cwd": self.cwd, "operation": operation, "id": "web", **params},
                                 ["/fake/owner"], owner=self.owner,
                                 rpc=lambda *a, **k: {"server_generation": self.session["generation"]})

    def test_revisions_and_durable_owner_id(self):
        description = self.invoke("commands.describe")
        for _ in range(2):
            result = self.invoke("services.ensure", revision=description["revision"], operation_id="stable-attempt")
            self.assertEqual(result, {"state": "running", "run_id": "run"})
        self.assertEqual([p["request_id"] for a, p in self.calls if a == "run"], ["stable-attempt"] * 2)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.invoke("services.ensure", revision="stale", operation_id="unused")
        self.assertEqual(sum(a == "run" for a, _ in self.calls), 2)

    def test_urls_require_current_ready_matching_owner_state(self):
        self.assertEqual(self.invoke("services.urls")["urls"], ["http://localhost:3210"])
        for change in ({"updated": time.time() - 60}, {"state": "unknown"}, {"root": "/wrong"}, {"readiness": "unknown"}):
            before = deepcopy(self.run)
            self.run.update(change)
            with self.assertRaises(ValueError):
                self.invoke("services.urls")
            self.run = before
        self.assertFalse(any(a == "run" for a, _ in self.calls))

    def test_uncertain_and_mismatched_runs_are_not_success(self):
        self.run["state"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            self.invoke("services.ensure", revision=project.digest(self.definition), operation_id="attempt")
        self.run["root"] = "/wrong"
        with self.assertRaisesRegex(ValueError, "mismatched"):
            self.invoke("services.ensure", revision=project.digest(self.definition), operation_id="attempt")

    def test_connection_check_validates_list_without_launching(self):
        with patch("project_launcher.provider", return_value=self.invoke("commands.list")):
            self.assertIn("1 commands/services", project.check_provider(project.EMPTY, "commands", self.cwd))
        self.assertEqual([a for a, _ in self.calls], ["config"])


if __name__ == "__main__":
    unittest.main()
