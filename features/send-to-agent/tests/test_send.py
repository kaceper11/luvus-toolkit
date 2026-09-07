import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from send_agent import Error, Host, Store, deliver as raw_deliver, file_item, files, message, text_item, validate_context


def deliver(store, host, record, reviewed):
    from checkout_state import review
    record['reviewed_checkouts'] = review(record)
    return raw_deliver(store, host, record, reviewed)


class FakeHost:
    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.values = [dict(pane="5", terminal_id="t5", generation="g1", session="s5", agent="codex", name="existing", cwd=self.cwd, status="idle")]
        self.calls = []
        self.fail = None
        self.ready = True

    def agents(self):
        return copy.deepcopy(self.values)

    def discover(self):
        return ["codex"]

    def call(self, method, **params):
        if method == "pane.get":
            return {}  # Naming has its own exact-tab tests.
        self.calls.append((method, params))
        if method == self.fail:
            raise Error("Transport timeout")
        if method == "terminal.backend.create":
            return dict(pane_id="6", terminal_id="t6", server_generation="g1")
        if method == "agent.start":
            self.values.append(dict(pane="6", terminal_id="t6", generation="g1", session="s6", agent=params["kind"], name=params["name"], cwd=self.cwd, status="idle"))
            return {"ready": self.ready}
        return {"ok": True}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "state")
        self.host = FakeHost(self.root)
        self.record = self.store.create({"pane": {"cwd": str(self.root), "id": "5"}, "selection": "Quoted\nzażółć 🌍 $HOME `x`"}, {})
        self.record["instruction"] = "Explain these lines"
        self.record["target"] = self.host.agents()[0]
        self.store.save(self.record)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_existing_exact_text_and_duplicate_send(self):
        text = message(self.record)
        deliver(self.store, self.host, self.record, text)
        self.assertEqual(self.record["state"], "delivered")
        self.assertEqual(self.host.calls, [("agent.prompt", {"target": "5", "text": text})])
        with self.assertRaises(Error):
            deliver(self.store, self.host, self.record, text)
        self.assertEqual(len(self.host.calls), 1)
        quoted = "\n".join(line[2:] for line in text.split("Context 1: Selected terminal text\n")[1].split("\n"))
        self.assertEqual(quoted, self.record["items"][0]["text"])

    def test_new_agent_same_checkout(self):
        self.record["target"] = None
        deliver(self.store, self.host, self.record, message(self.record))
        self.assertEqual([c[0] for c in self.host.calls], ["terminal.backend.create", "agent.start", "agent.prompt"])
        self.assertEqual(Path(self.host.calls[0][1]["cwd"]).resolve(), self.root.resolve())
        self.assertEqual(self.host.calls[-1][1]["target"], "6")
        self.assertEqual(self.store.get(self.record["id"])["launch"]["pane_id"], "6")

    def test_disappeared_replaced_or_restarted_target_no_write(self):
        for key in ("pane", "session", "generation", "terminal_id", "agent", "name", "cwd"):
            with self.subTest(key=key):
                original = self.host.values[0][key]
                self.host.values[0][key] = "replacement"
                with self.assertRaises(Error):
                    deliver(self.store, self.host, self.record, message(self.record))
                self.host.values[0][key] = original
        self.host.values = []
        with self.assertRaises(Error):
            deliver(self.store, self.host, self.record, message(self.record))
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.store.get(self.record["id"])["state"], "draft")

    def test_each_startup_or_delivery_timeout_is_durable(self):
        for method in ("terminal.backend.create", "agent.start", "agent.prompt"):
            with self.subTest(method=method):
                record = self.store.create({"pane": {"cwd": str(self.root)}, "selection": "x"}, {})
                record["instruction"] = "Explain"
                host = FakeHost(self.root)
                host.fail = method
                text = message(record)
                with self.assertRaises(Error):
                    deliver(self.store, host, record, text)
                saved = self.store.get(record["id"])
                self.assertEqual(saved["state"], "uncertain")
                count = len(host.calls)
                with self.assertRaises(Error):
                    deliver(self.store, host, saved, text)
                self.assertEqual(len(host.calls), count)

    def test_not_ready_does_not_prompt(self):
        self.record["target"] = None
        self.host.ready = False
        with self.assertRaises(Error):
            deliver(self.store, self.host, self.record, message(self.record))
        self.assertEqual(self.record["state"], "uncertain")
        self.assertNotIn("agent.prompt", [x[0] for x in self.host.calls])

    def test_two_composers_claim_once(self):
        second = Store(self.store.root)
        other = second.get(self.record["id"])
        try:
            deliver(self.store, self.host, self.record, message(self.record))
            with self.assertRaises(Error):
                deliver(second, self.host, other, message(other))
            with self.assertRaises(Error):
                second.save(other)
            self.assertEqual(len(self.host.calls), 1)
        finally:
            second.db.close()

    def test_changed_message_and_invalid_context_never_send(self):
        text = message(self.record)
        self.record["instruction"] = "Changed"
        with self.assertRaises(Error):
            deliver(self.store, self.host, self.record, text)
        self.record["items"] = []
        with self.assertRaises(Error):
            deliver(self.store, self.host, self.record, text)
        self.assertEqual(self.host.calls, [])


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / "a.txt").write_bytes(b"one\ntwo\nthree\n")
        self.git("add", "a.txt")
        self.git("commit", "-qm", "Initial")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.STDOUT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_line_ranges_and_changed_snapshot(self):
        item = file_item(self.root, "a.txt", start=2, end=3)
        self.assertEqual(item["text"], "two\nthree\n")
        self.assertEqual(file_item(self.root, "a.txt", start=2)["text"], "two\n")
        for start, end in ((0, 1), (-1, 1), (3, 2), (1, 4)):
            with self.subTest(start=start, end=end), self.assertRaises(Error):
                file_item(self.root, "a.txt", start=start, end=end)
        record = {"cwd": str(self.root), "items": [item]}
        validate_context(record)
        (self.root / "a.txt").write_text("changed\n")
        with self.assertRaises(Error):
            validate_context(record)

    def test_staged_unstaged_deleted_and_untracked(self):
        (self.root / "a.txt").write_text("staged\n")
        self.git("add", "a.txt")
        (self.root / "a.txt").write_text("unstaged\n")
        self.assertIn("+staged", file_item(self.root, "a.txt", "staged")["text"])
        self.assertIn("+unstaged", file_item(self.root, "a.txt", "unstaged")["text"])
        (self.root / "a.txt").unlink()
        self.assertIn("deleted file", file_item(self.root, "a.txt", "unstaged")["text"])
        (self.root / "new file.txt").write_text("new\n")
        self.assertIn("new file.txt", files(self.root))
        self.assertIn("a.txt", files(self.root))
        with self.assertRaises(Error):
            file_item(self.root, "new file.txt", "unstaged")

    def test_traversal_binary_missing_and_no_diff(self):
        (self.root / "binary").write_bytes(b"\0\xff")
        (self.root / "link").symlink_to(self.root.parent)
        for path in ("../outside", str(self.root / "a.txt"), "missing", "binary", "link/outside"):
            with self.subTest(path=path), self.assertRaises(Error):
                file_item(self.root, path)
        with self.assertRaises(Error):
            file_item(self.root, "a.txt", "unstaged")
        with self.assertRaises(Error):
            file_item(self.root, "a.txt", "staged", 1, 1)

    def test_literal_path_and_directory_cannot_expand_selection(self):
        (self.root / "sub").mkdir()
        (self.root / "sub/a.txt").write_text("base\n")
        (self.root / "[ab].txt").write_text("literal\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Paths")
        (self.root / "sub/a.txt").write_text("changed\n")
        (self.root / "[ab].txt").write_text("literal changed\n")
        self.assertIn("+literal changed", file_item(self.root, "[ab].txt", "unstaged")["text"])
        with self.assertRaises(Error):
            file_item(self.root, "sub", "unstaged")
        with self.assertRaises(Error):
            file_item(self.root, ":(glob)**", "unstaged")

    def test_git_does_not_modify_checkout(self):
        (self.root / "a.txt").write_text("dirty\n")
        before = self.git("status", "--porcelain")
        file_item(self.root, "a.txt", "unstaged")
        self.assertEqual(before, self.git("status", "--porcelain"))


class HostTests(unittest.TestCase):
    def test_incomplete_inherited_session_does_not_fall_back(self):
        with patch.dict(os.environ, {"LUVUS_ENV": "1"}, clear=True), self.assertRaises(Error):
            Host()

    def test_explicit_session_excludes_unrelated_inherited_session(self):
        with patch.dict(os.environ, {"LUVUS_SOCKET_PATH": "/wrong", "LUVUS_SESSION": "wrong"}):
            host = Host({"LUVUS_BIN_PATH": "/bin/luvus", "LUVUS_HOME": "/tmp/isolated"})
        self.assertNotIn("LUVUS_SOCKET_PATH", host.env)
        self.assertNotIn("LUVUS_SESSION", host.env)

    def test_transport_rejects_mismatched_response(self):
        host = Host({"LUVUS_BIN_PATH": "/bin/luvus"})
        with patch("send_agent.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"id":"wrong","result":{}}', '')):
            with self.assertRaises(Error):
                host.call("agent.list")


if __name__ == "__main__":
    unittest.main()
