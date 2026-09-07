#!/usr/bin/env python3
"""Run: python3 test_git_sidebar.py. Only disposable repositories are mutated."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import git_sidebar as app


def expect_error(fn, contains):
    try:
        fn()
    except app.GitError as error:
        assert contains in str(error), str(error)
    else:
        raise AssertionError("Expected error: " + contains)


def target(repo):
    return {"repo": app.root(repo), "identity": app.identity(repo)}


def run():
    with patch.object(app, "luvus", return_value={"enabled": True, "version": app.VERSION}), patch.object(app, "active_workspace_path", side_effect=["/a", "/a", "/b"]), patch.object(app, "refresh") as paint:
        previous = ""
        for _ in range(3):
            previous = app.watch_once(previous)
        assert previous == "/b" and paint.call_count == 2
    with patch.object(app, "luvus", return_value={"enabled": False}), patch.object(app, "refresh") as paint:
        assert app.watch_once("/b") is None
        paint.assert_not_called()
    with tempfile.TemporaryDirectory(prefix="git-sidebar-test-") as temporary:
        folder = Path(temporary)
        repo = folder / "repo with spaces"
        repo.mkdir()
        app.git(repo, "init", "-b", "main")
        app.git(repo, "config", "user.name", "Test")
        app.git(repo, "config", "user.email", "test@example.invalid")
        # Isolate tests from personal Git hooks, signing, templates and configuration.
        env = {"LUVUS_MODULE_STATE_DIR": str(folder / "state"), "LUVUS_SOCKET_PATH": "/test/socket"}
        with patch.dict(os.environ, env), patch.object(app, "active_workspace_path", side_effect=lambda: os.environ.get("LUVUS_WORKSPACE_CWD", str(repo))):
            names = ["plain.txt", "two words.txt", ("line-break.txt" if os.name == "nt" else "line\nbreak.txt"), "雪.txt", "[abc].txt", "-flag.txt", "--option.txt", "$(touch nope).txt"]
            for name in names:
                (repo / name).write_text("one\n")
            assert {f["path"] for f in app.status(repo)} == set(names)
            # Non-repository folders render a calm empty state and expose no Git mutation.
            with patch.dict(os.environ, {"LUVUS_WORKSPACE_CWD": str(folder)}), patch.object(app, "luvus") as host:
                app.refresh()
                empty = json.loads(next(c for c in host.call_args_list if c.args[:3] == ("ui", "dock", "push")).kwargs["stdin"])
                assert any(r["text"] == "NO GIT REPOSITORY" for r in empty)
                assert {r["action"] for r in empty if "action" in r} == {"refresh", "open-repository"}
                assert not (folder / ".git").exists()
                expect_error(lambda: app.validate_workspace(target(repo)), "not a Git repository")
            # Paging is navigable, collapse preserves files, and all state actions are read-only.
            t = target(repo)
            assert app.primary(app.snapshot(t))[1] == "review-changes"
            assert app.primary(None)[1] == "open-repository"
            with patch.object(app, "luvus", return_value={}) as native:
                app.native_preview(dict(t, paths=["--option.txt"], staged=False, untracked=True))
                assert native.call_args.args[2] == str(Path(t["repo"]) / "--option.txt")
                assert native.call_args.args[4] == "untracked"
            dock = app.rows(t)
            first = {json.loads(r["value"])["paths"][0] for r in dock if r.get("action") == "preview"}
            assert len(first) == 4
            page = next(r for r in dock if r.get("action") == "page")
            with patch.dict(os.environ, {"LUVUS_WORKSPACE_CWD": str(repo), "LUVUS_MODULE_ROW_VALUE": page["value"]}), patch.object(app, "luvus"), patch.object(app.sys, "argv", ["git_sidebar.py", "page"]):
                assert app.main() == 0
            second = {json.loads(r["value"])["paths"][0] for r in app.rows(t) if r.get("action") == "preview"}
            assert first | second == set(names) and not first & second
            heading = next(r for r in dock if r.get("action") == "toggle")
            with patch.dict(os.environ, {"LUVUS_WORKSPACE_CWD": str(repo), "LUVUS_MODULE_ROW_VALUE": heading["value"]}), patch.object(app, "luvus"), patch.object(app.sys, "argv", ["git_sidebar.py", "toggle"]):
                assert app.main() == 0
            assert not any(r.get("action") == "preview" for r in app.rows(t))
            assert {f["path"] for f in app.status(repo)} == set(names)
            app.ui_state_path(t["repo"]).unlink()
            with patch.object(app.shutil, "which", return_value=None), patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()) as preview_output:
                app.preview(dict(t, paths=["[abc].txt"], staged=False, untracked=True))
            assert "+one" in preview_output.getvalue()
            assert app.git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0
            app.stage(dict(t, paths=["[abc].txt"]), "stage")
            assert app.output(repo, "diff", "--cached", "--name-only") == "[abc].txt"
            app.stage(dict(t, paths=["[abc].txt"]), "unstage")
            assert (repo / "[abc].txt").exists()
            expect_error(lambda: app.staged_snapshot(repo), "Nothing staged")
            app.stage(t, "stage-all")
            app.git(repo, "commit", "-m", "Initial")

            # Staged and unstaged hunks in the same file remain separately visible.
            (repo / "plain.txt").write_text("two\n")
            t = target(repo)
            app.stage(dict(t, paths=["plain.txt"]), "stage")
            snapshot = app.staged_snapshot(repo)
            (repo / "plain.txt").write_text("three\n")
            assert app.staged_snapshot(repo) == snapshot
            dock = app.rows(t)
            assert len([r for r in dock if r.get("text", "").endswith("plain.txt")]) == 2
            previews = [r for r in dock if r.get("action") == "preview"]
            assert len(previews) == 2
            assert app.primary(app.snapshot(t))[1] == "review-changes"
            full, compact = app.toolbar(app.snapshot(t))
            assert compact[-1]["tone"] == "accent" and compact[-1]["action"] == "review-changes"
            assert json.loads(compact[-1]["value"]) == t
            assert full[-2] == dict(type="spacer", width=2)
            assert not any(r.get("action") == "stage-all" for r in dock)
            assert {r["action"] for r in full if "action" in r} == {"review-changes"}
            before_review = app.staged_snapshot(repo)
            with patch.object(app, "native_preview") as native, patch.object(app, "refresh"):
                app.review_changes(t)
                assert native.call_args.args[0]["paths"] == ["plain.txt"]
                assert not native.call_args.args[0]["staged"]
            assert app.staged_snapshot(repo) == before_review
            assert (repo / "plain.txt").read_text() == "three\n"
            before_preview = app.staged_snapshot(repo)
            for r in previews:
                selected = json.loads(r["value"])
                layer = "staged" if selected["staged"] else "worktree"
                with patch.object(app, "luvus", return_value={}) as native:
                    app.native_preview(selected)
                    assert native.call_args_list[-1].args == ("diff", "open", str(Path(t["repo"]) / "plain.txt"), "--layer", layer, "--placement", "preview", "--view", "auto")
                with patch.object(app, "luvus", side_effect=app.GitError("Native diff unavailable")) as native:
                    expect_error(lambda: app.native_preview(selected), "unavailable")
                    assert native.call_count == 1
            assert app.staged_snapshot(repo) == before_preview
            assert (repo / "plain.txt").read_text() == "three\n"
            assert {r["dot"] for r in previews} == {"working", "done"}
            actions_menu = next(r["menu"] for r in dock if r.get("action") == "more")
            assert any(item["action"] == "commit" for item in actions_menu)
            assert all("paths" in json.loads(r["value"]) for r in previews)
            assert not any(r.get("action") == "stage" for r in dock)
            with patch.object(app.shutil, "which", return_value=None), patch("builtins.input", return_value=""):
                for r in previews:
                    app.preview(json.loads(r["value"]))
            assert app.staged_snapshot(repo) == snapshot
            assert (repo / "plain.txt").read_text() == "three\n"
            app.ui_state_path(t["repo"]).write_text(json.dumps(dict(paths=["plain.txt"], staged=False, more=True)))
            expanded = app.rows(t)
            assert any(r.get("action") == "stage" for r in expanded)
            assert any(r.get("action") == "generate" for r in expanded)
            app.stage(dict(t, paths=["plain.txt"]), "stage")
            assert app.staged_snapshot(repo) != snapshot
            app.stage(dict(t, paths=["plain.txt"]), "unstage")
            assert (repo / "plain.txt").read_text() == "three\n"

            # Cancellation leaves index and HEAD untouched.
            app.stage(t, "stage-all")
            before = app.staged_snapshot(repo)
            with patch.object(app, "edit_message", lambda p: p.write_text("Update text\n")), patch("builtins.input", side_effect=["e", ""]):
                app.commit_review(str(repo))
            assert app.staged_snapshot(repo) == before
            drafts = list(app.state_dir().glob("draft-*/message.txt"))
            assert len(drafts) == 1 and drafts[0].read_text() == "Update text\n"
            with patch.object(app, "generate", side_effect=app.GitError("Generation unavailable")), patch("builtins.input", side_effect=["g", ""]):
                app.commit_review(str(repo))
            assert drafts[0].read_text() == "Update text\n"

            # An external staging change during review prevents commit.
            def changed(_prompt):
                (repo / "plain.txt").write_text("four\n")
                app.git(repo, "add", "--", "plain.txt")
                return "c"
            with patch.object(app, "edit_message", lambda p: p.write_text("Update text\n")), patch("builtins.input", side_effect=changed):
                expect_error(lambda: app.commit_review(str(repo)), "changed")
            assert app.identity(repo) == t["identity"]
            with patch.object(app, "edit_message", lambda p: p.write_text("Update text\n")), patch("builtins.input", side_effect=["e", "c"]):
                app.commit_review(str(repo))
            expect_error(lambda: app.validate(t), "changed")
            # Toolbar payloads use the same stale-HEAD guard as sidebar clicks.
            before_stale = app.git(repo, "ls-files", "--stage", "-z").stdout
            with patch.dict(os.environ, {"LUVUS_MODULE_BAR_VALUE": json.dumps(t), "LUVUS_MODULE_ROW_VALUE": ""}), patch.object(app, "luvus"), patch.object(app.sys, "argv", ["git_sidebar.py", "stage-all"]):
                assert app.main() == 1
            assert app.git(repo, "ls-files", "--stage", "-z").stdout == before_stale

            # Renames unstage both paths; deletions never restore working files.
            app.git(repo, "mv", "plain.txt", "renamed.txt")
            rename = next(f for f in app.status(repo) if f["path"] == "renamed.txt")
            assert rename["paths"] == ["renamed.txt", "plain.txt"]
            app.stage(dict(target(repo), paths=rename["paths"]), "unstage")
            assert (repo / "renamed.txt").exists() and not (repo / "plain.txt").exists()
            app.stage(target(repo), "stage-all")
            app.git(repo, "commit", "-m", "Rename")

            # Linked worktree gets its own repository and HEAD identity.
            worktree = folder / "linked worktree"
            app.git(repo, "worktree", "add", "-b", "linked", str(worktree))
            assert Path(app.root(worktree)).resolve() == worktree.resolve()
            assert target(worktree) != target(repo)

            # Repository choices are clickable, deduplicated, and exclude non-Git folders.
            with patch.object(app, "luvus", return_value={"workspaces": [
                dict(cwd=str(repo), name="Project One"), dict(cwd=str(repo), name="Duplicate"),
                dict(cwd=str(worktree), name="Linked"), dict(cwd=str(folder), name="Not Git")]}):
                choices = app.repository_choices()
            selected = [r for r in choices if r.get("action") == "select-repository"]
            assert len(selected) == 2
            assert json.loads(selected[0]["value"])["repo"] == app.root(repo)
            assert any(r.get("action") == "repository-path" for r in choices)
            # Working status persists through refresh; dead processes cannot leave a spinner.
            with patch.object(app, "PROGRESS_SCOPE", app.root(repo)), patch.object(app, "refresh"):
                app.progress("Loading diff…")
                assert app.read_progress(app.root(repo))["state"] == "working"
                with patch.object(app.psutil, "pid_exists", return_value=False):
                    assert app.read_progress(app.root(repo))["state"] == "blocked"
                app.progress("Diff opened", "done")
                assert app.read_progress(app.root(repo))["text"] == "Diff opened"
            # Completion refresh follows the live workspace, not the operation's old cwd.
            with patch.object(app, "active_workspace_path", return_value=str(worktree)), patch.object(app, "luvus") as host:
                app.refresh()
                bar = json.loads(host.call_args.args[5])
                assert any(x.get("text") == worktree.name for x in bar)
            assert any("local refs" in x.get("text", "") or "no upstream" in x.get("text", "") for x in bar)
            assert app.primary(app.snapshot(target(repo)))[1] == "refresh"
            detached = app.snapshot(target(repo))
            detached["target"] = dict(detached["target"], identity=["", detached["target"]["identity"][1]])
            assert app.primary(detached)[1] == "refresh"
            for ahead, behind, expected in [(0, 0, "refresh"), (2, 0, "push"), (0, 3, "pull"), (2, 3, "pull")]:
                tracked = dict(app.snapshot(target(repo)), upstream=True, ahead=ahead, behind=behind)
                assert app.primary(tracked)[1] == "refresh"
            # Repository picker validates first; cancellation and invalid paths create nothing.
            with patch("builtins.input", side_effect=[str(folder / "missing"), ""]), patch.object(app, "luvus") as host:
                app.open_repository()
                assert not any(c.args[:2] == ("workspace", "open") for c in host.call_args_list)
            with patch("builtins.input", side_effect=[str(repo), "y"]), patch.object(app, "refresh"), patch.object(app, "luvus") as host:
                app.open_repository()
                host.assert_called_once_with("workspace", "open", app.root(repo))

            # Local remote: initial push, upstream pull, and fetch/merge another branch.
            remote = folder / "remote.git"
            app.git(folder, "init", "--bare", str(remote))
            app.git(repo, "remote", "add", "origin", str(remote))
            with patch("builtins.input", side_effect=["1", "y"]):
                app.network_action("push", target(repo))
            clone = folder / "clone"
            app.git(folder, "clone", "-b", "main", str(remote), str(clone))
            app.git(clone, "config", "user.name", "Test")
            app.git(clone, "config", "user.email", "test@example.invalid")
            (clone / "upstream.txt").write_text("upstream\n")
            app.git(clone, "add", ".")
            app.git(clone, "commit", "-m", "Upstream")
            app.git(clone, "push")
            app.network_action("pull", target(repo))
            assert (repo / "upstream.txt").exists()
            app.git(clone, "checkout", "-b", "feature")
            (clone / "feature.txt").write_text("feature\n")
            app.git(clone, "add", ".")
            app.git(clone, "commit", "-m", "Feature")
            app.git(clone, "push", "-u", "origin", "feature")
            with patch.object(app, "choose", return_value="refs/remotes/origin/feature"), patch("builtins.input", return_value="y"):
                app.network_action("merge", target(repo))
            assert (repo / "feature.txt").exists()
            with patch("builtins.input", return_value="y"):
                app.network_action("push", target(repo))

            # Dirty pull is rejected, and conflicts remain for explicit resolution.
            (repo / "renamed.txt").write_text("local\n")
            expect_error(lambda: app.network_action("pull", target(repo)), "Commit or stash")
            app.git(repo, "add", ".")
            app.git(repo, "commit", "-m", "Local conflict")
            app.git(clone, "checkout", "main")
            app.git(clone, "pull", "--ff-only")
            (clone / "renamed.txt").write_text("remote\n")
            app.git(clone, "add", ".")
            app.git(clone, "commit", "-m", "Remote conflict")
            app.git(clone, "push")
            expect_error(lambda: app.network_action("pull", target(repo)), "Git failed")
            assert any(f["conflict"] for f in app.status(repo))
            assert app.primary(app.snapshot(target(repo)))[1] == "review-changes"
            assert any(x.get("tone") == "warning" for x in app.toolbar(app.snapshot(target(repo)))[0])
            expect_error(lambda: app.stage(target(repo), "stage-all"), "Resolve conflicts")
            app.git(repo, "merge", "--abort")

            # Generation uses staged data only; failures and concurrent staging are safe.
            (repo / "renamed.txt").write_text("generated staged\n")
            app.git(repo, "add", "--", "renamed.txt")
            (repo / "renamed.txt").write_text("unstaged secret text\n")
            message = folder / "draft.txt"
            real_run = subprocess.run
            def fake_codex(argv, **kwargs):
                if argv[0] != "codex":
                    return real_run(argv, **kwargs)
                assert "read-only" in argv and "--ephemeral" in argv
                assert "generated staged" in kwargs["input"]
                assert "unstaged secret text" not in kwargs["input"]
                Path(argv[argv.index("--output-last-message") + 1]).write_text("Update renamed file\n")
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(subprocess, "run", side_effect=fake_codex):
                assert app.generate(str(repo), message) == app.staged_snapshot(repo)
            assert message.read_text() == "Update renamed file\n"
            def failed_codex(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1) if argv[0] == "codex" else real_run(argv, **kwargs)
            with patch.object(subprocess, "run", side_effect=failed_codex):
                expect_error(lambda: app.generate(str(repo), message), "could not generate")
            assert (repo / "renamed.txt").read_text() == "unstaged secret text\n"

            # Payload round-trip preserves exact repository and filenames.
            values = [r["value"] for r in app.rows(target(repo)) if "value" in r]
            assert all(Path(json.loads(v)["repo"]).resolve() == repo.resolve() for v in values)
            assert "\x1b" not in app.display("unsafe\x1b[2J\n")
            with patch.object(app, "luvus", return_value={"pane": "test-pane"}) as host:
                app.open_terminal("commit", target(repo))
                request = json.loads(app.pending_path().read_text())
                assert request["target"] == target(repo)
                expect_error(lambda: app.open_terminal("push", target(repo)), "opening")
                assert host.call_args.args == ("module", "pane", "focus", "test-pane")
                app.pending_path().unlink()
            # Lazygit launches in a tab, reuses it, and verifies focus/process identity.
            calls = []
            def fake_host(*args, **kwargs):
                calls.append(args)
                if args[:3] == ("module", "pane", "open"):
                    app.pending_path().unlink()  # Simulate consume-once handoff.
                    return {"pane": "test-lazygit"}
                if args[:2] == ("pane", "processes"):
                    return dict(terminal_id="unique-terminal", executables=["lazygit"])
                if args[:2] == ("pane", "list"):
                    return dict(panes=[dict(pane="test-lazygit", focused=True, module=dict(id="kacper.toolkit", entrypoint="git-sidebar-lazygit-terminal-windows" if os.name == "nt" else "git-sidebar-lazygit-terminal"))])
                return {}
            with patch.object(app, "luvus", side_effect=fake_host), patch.object(app, "progress") as progress:
                app.open_terminal("lazygit", target(repo))
                app.open_terminal("lazygit", target(repo))
                launches = [c for c in calls if c[:3] == ("module", "pane", "open")]
                assert len(launches) == 1 and launches[0][-3:] == ("lazygit-terminal", "--placement", "tab")
                assert calls.count(("module", "pane", "focus", "test-lazygit")) == 2
                progress.assert_called_with("Lazygit opened", "done")
            def fail_focus(*args, **kwargs):
                if args[:3] == ("module", "pane", "focus"):
                    raise app.GitError("focus failed")
                return fake_host(*args, **kwargs)
            with patch.object(app, "luvus", side_effect=fail_focus), patch.object(app, "progress") as progress:
                expect_error(lambda: app.open_terminal("lazygit", target(repo)), "focus failed")
                progress.assert_not_called()
            app.lazygit_record(target(repo)["repo"]).write_text(json.dumps(dict(pane="old", terminal_id="old-terminal")))
            with patch.object(app, "luvus", side_effect=fake_host), patch.object(app, "progress"):
                app.open_terminal("lazygit", target(repo))
                assert len([c for c in calls if c[:3] == ("module", "pane", "open")]) == 2
            # A long-lived Lazygit pane must not block sidebar staging.
            app.pending_path().write_text(json.dumps(dict(action="lazygit", target=target(repo))))
            canonical_repo = target(repo)["repo"]
            def fake_lazygit(*args, **kwargs):
                with app.lock(canonical_repo):
                    pass
                return subprocess.CompletedProcess(args, 0)
            with patch.object(app, "validate", return_value=target(repo)["repo"]), patch.object(app, "refresh"), patch.object(app.shutil, "which", return_value="lazygit"), patch.object(app.subprocess, "run", side_effect=fake_lazygit), patch("builtins.input", side_effect=AssertionError("Successful pane should close")):
                app.terminal()
            with patch.object(app, "refresh"), patch("builtins.input", return_value=""):
                app.terminal()  # Session restore cannot replay a consumed action.
    print("PASS: staging, unusual paths, renames, worktrees, review guards, push/pull/merge/conflict, pane handoff")


def branch_checks():
    with tempfile.TemporaryDirectory(prefix="git-branch-review-") as temporary:
        repo = Path(temporary) / "repo"
        repo.mkdir()
        app.git(repo, "init", "-b", "main")
        app.git(repo, "config", "user.name", "Test")
        app.git(repo, "config", "user.email", "test@example.invalid")
        expect_error(lambda: app.branch_comparison(target(repo), "refs/heads/main"), "No commits")
        (repo / "old.txt").write_text("rename me\n")
        (repo / "delete.txt").write_text("remove me\n")
        app.git(repo, "add", ".")
        app.git(repo, "commit", "-m", "Base")
        original = app.identity(repo)[1]
        app.git(repo, "branch", "feature")
        (repo / "base-only.txt").write_text("not an agent change\n")
        app.git(repo, "add", ".")
        app.git(repo, "commit", "-m", "Advance base")
        app.git(repo, "checkout", "feature")
        unusual = "--雪 line-break.txt" if os.name == "nt" else "--雪\tline\nbreak.txt"
        app.git(repo, "mv", "--", "old.txt", unusual)
        (repo / "delete.txt").unlink()
        (repo / "binary.dat").write_bytes(b"\0binary\xff")
        (repo / "added.txt").write_text("one\ntwo\n\x1b[2Junsafe\n")
        app.git(repo, "add", "--all")
        app.git(repo, "commit", "-m", "Agent changes")
        t = target(repo)
        c = app.branch_comparison(t, "refs/heads/main")
        assert (c["files"], c["added"], c["deleted"], c["binary"]) == (4, 3, 1, 1), c
        assert c["merge_base"] == original and c["base_oid"] != original
        assert app.branch_comparison(t, "refs/heads/feature")["files"] == 0
        expect_error(lambda: app.branch_comparison(t, "--help"), "Select a local")
        expect_error(lambda: app.branch_comparison(t, "refs/heads/missing"), "revision")
        real_git = app.git
        def multiple(repo, *args, **kwargs):
            if args[:2] == ("merge-base", "--all"):
                return subprocess.CompletedProcess(args, 0, (original + "\n" + c["head"] + "\n").encode(), b"")
            return real_git(repo, *args, **kwargs)
        with patch.object(app, "git", side_effect=multiple):
            expect_error(lambda: app.branch_comparison(t, "refs/heads/main"), "Multiple merge bases")
        # An orphan commit supplies an unrelated branch without switching the checkout.
        tree = app.output(repo, "rev-parse", "HEAD^{tree}")
        orphan = app.output(repo, "commit-tree", tree, "-m", "Unrelated")
        app.git(repo, "update-ref", "refs/heads/unrelated", orphan)
        expect_error(lambda: app.branch_comparison(t, "refs/heads/unrelated"), "unrelated histories")
        app.git(repo, "checkout", "--detach")
        assert app.branch_comparison(target(repo), "refs/heads/main")["head"] == c["head"]
        app.git(repo, "checkout", "feature")
        (repo / "added.txt").write_text("staged local\n")
        app.git(repo, "add", "--", "added.txt")
        (repo / "added.txt").write_text("unstaged local\n")
        (repo / "untracked.txt").write_text("untracked local\n")
        before_index = app.git(repo, "ls-files", "--stage", "-z").stdout
        before_files = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
        with patch.object(app, "active_workspace_path", return_value=app.root(repo)), \
                patch.object(app, "refresh"), patch.object(app, "choose", return_value="refs/heads/main"), \
                patch.object(app.shutil, "which", return_value=None), \
                patch("builtins.input", side_effect=["v", ""]), contextlib.redirect_stdout(io.StringIO()) as screen:
            with patch.object(app, "git", wraps=real_git) as commands:
                app.review_branch(t)
            assert all(call.args[1] in ("rev-parse", "symbolic-ref", "for-each-ref", "merge-base", "diff", "status") for call in commands.call_args_list)
        text = screen.getvalue()
        assert "4 changed files · +3 / -1 text lines · 1 binary files" in text
        assert "1 staged / 1 unstaged / 1 untracked / 0 conflicted" in text
        assert "+one" in text and "base-only.txt" not in text and "unstaged local" not in text
        assert "\x1b" not in text and "\\x1b" in text
        assert app.git(repo, "ls-files", "--stage", "-z").stdout == before_index
        assert {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()} == before_files
        assert app.identity(repo) == t["identity"]
        with patch.object(app, "active_workspace_path", return_value=app.root(repo)), \
                patch.object(app, "choose", return_value=None), patch.object(app, "branch_comparison") as compare:
            app.review_branch(t)
            compare.assert_not_called()
        with patch.object(app, "active_workspace_path", return_value=temporary), patch.object(app, "refresh"):
            expect_error(lambda: app.review_branch(t), "not a Git repository")
        # Refs move after selection; the old patch is still exactly reproducible.
        app.git(repo, "commit", "-m", "New agent commit")
        expect_error(lambda: app.branch_comparison(t, "refs/heads/main"), "changed")
        with patch.object(app.shutil, "which", return_value=None), contextlib.redirect_stdout(io.StringIO()) as screen:
            app.branch_patch(c, "Pinned")
        assert "+one" in screen.getvalue() and "staged local" not in screen.getvalue()
        # The actual pager receives a file, not a potentially huge input string.
        with tempfile.TemporaryFile(mode="w+") as text_file:
            text_file.write("safe patch\n")
            with patch.object(app.shutil, "which", return_value="less"), \
                    patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as pager:
                app.page_text(text_file)
                assert pager.call_args.kwargs["stdin"] is text_file
                assert pager.call_args.kwargs["env"]["LESSSECURE"] == "1"
            with patch.object(app.shutil, "which", return_value="less"), \
                    patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                expect_error(lambda: app.page_text(text_file), "pager failed")
        with patch.dict(os.environ, {"LUVUS_MODULE_STATE_DIR": str(Path(temporary) / "state")}):
            actions = next(r["menu"] for r in app.rows(target(repo)) if r.get("action") == "more")
            assert any(a["action"] == "review-branch" for a in actions)
    print("PASS: branch comparison, summary, binary/rename paths, local exclusions, pinned revisions, cancellation, pager")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="git-config-") as config:
        with patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                     "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.hooksPath",
                                     "GIT_CONFIG_VALUE_0": config, "GIT_CONFIG_KEY_1": "commit.gpgsign",
                                     "GIT_CONFIG_VALUE_1": "false"}):
            run()
            branch_checks()
