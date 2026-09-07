import json
import os
import tempfile
import unittest
from unittest.mock import patch
import git_sidebar as git


class HubTests(unittest.TestCase):
    def test_git_menu_opens_changes_directly(self):
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, {'LUVUS_MODULE_STATE_DIR': state}):
            target = {'repo': '/clicked repo', 'identity': ['refs/heads/main', 'abc']}
            with patch.object(git, 'open_terminal', return_value='42') as opened:
                self.assertEqual(git.show_hub(target), '42')
                opened.assert_called_once_with('lazygit-status', target)
                self.assertFalse(git.ui_state('__git_picker__')['open'])

    def test_inactive_clicked_workspace_is_explicit_and_no_git_operation_runs(self):
        with tempfile.TemporaryDirectory() as state:
            target = {"repo": "/clicked repo", "identity": ["refs/heads/main", "abc"]}
            environment = {"LUVUS_MODULE_CONTEXT_JSON": json.dumps({"workspace": {"cwd": "/clicked repo"}}),
                           "LUVUS_MODULE_STATE_DIR": state, "LUVUS_SOCKET_PATH": "/test/socket", "LUVUS_BIN_PATH": "/test/luvus"}
            with patch.dict(os.environ, environment), patch.object(git.sys, "argv", ["git_sidebar.py", "hub"]), \
                 patch.object(git, "current_target", return_value=target) as selected, patch.object(git, "show_hub") as hub:
                self.assertEqual(git.dispatch(), 0)
                selected.assert_called_once_with("/clicked repo")
                hub.assert_called_once_with(target)




class ViewTests(unittest.TestCase):
    def test_each_view_reuses_its_own_tab(self):
        with tempfile.TemporaryDirectory() as state:
            target = {'repo': state, 'identity': ['refs/heads/main', 'abc']}
            captured, processes = [], {}
            active = None
            def host(*args, **kwargs):
                nonlocal active
                if args[:3] == ('module', 'pane', 'open'):
                    request = json.loads(git.pending_path().read_text())
                    git.pending_path().unlink()
                    captured.append(request)
                    active = str(len(captured))
                    processes[active] = {'terminal_id': 'terminal-' + active, 'executables': ['lazygit']}
                    return {'pane': active}
                if args[:2] == ('pane', 'processes'): return processes[args[2]]
                if args[:3] == ('module', 'pane', 'focus'): active = args[3]; return {}
                if args[:2] == ('pane', 'list'):
                    return {'panes': [{'pane': active, 'focused': True, 'module': {'id':git.MODULE, 'entrypoint':'lazygit-terminal'}}]}
                return {}
            with patch.dict(os.environ, {'LUVUS_MODULE_STATE_DIR': state, 'LUVUS_SOCKET_PATH': '/synthetic'}), \
                 patch.object(git, 'validate', return_value=state), patch.object(git, 'luvus', side_effect=host), patch.object(git, 'progress'):
                for view in git.VIEWS:
                    git.open_terminal('lazygit-' + view, target)
                for view in git.VIEWS:
                    git.open_terminal('lazygit-' + view, target)
                self.assertEqual(len(captured), 4)
                self.assertEqual([r['target']['view'] for r in captured], list(git.VIEWS))
                # An ambiguous open marker never launches another process.
                git.lazygit_record(state, 'log').write_text(json.dumps({'pending': True}))
                with self.assertRaisesRegex(git.GitError, 'uncertain'):
                    git.open_terminal('lazygit-log', target)
                self.assertEqual(len(captured), 4)

    def test_visible_actions_are_view_shortcuts(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(['git', 'init', '-q', root], check=True)
            with patch.dict(os.environ, {'LUVUS_MODULE_STATE_DIR':root + '/state', 'LUVUS_SOCKET_PATH':'/synthetic'}):
                target = git.current_target(root)
                actions = {r.get('action') for r in git.rows(target)}
                self.assertTrue({'lazygit-' + v for v in git.VIEWS} <= actions)
                self.assertFalse({'pull', 'push', 'merge', 'stage-all'} & actions)

if __name__ == "__main__":
    unittest.main()
