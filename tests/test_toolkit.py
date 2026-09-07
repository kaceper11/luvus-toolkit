import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from toolkit_core import ROOT, FEATURES, configure, directory
from toolkit_core.install import render, migrate
from toolkit_core.transport import request, cli_args

class ToolkitTests(unittest.TestCase):
    def test_manifest_keeps_every_feature_and_unique_names(self):
        import tomllib
        manifest = tomllib.loads(render())
        self.assertEqual(manifest['id'], 'kacper.toolkit')
        for kind in ('actions','panes','docks','bars','settings'):
            key = 'key' if kind == 'settings' else 'id'
            ids = [x[key] for x in manifest.get(kind, [])]
            self.assertEqual(len(ids),len(set(ids)))
        for feature in FEATURES:
            self.assertTrue(any(a['command'][3:4] == [feature] for a in manifest['actions']))
        self.assertEqual(sum(x['command'][-1]=='toolkit_core/tab_titles.py' for x in manifest['events']),2)

    def test_nested_feature_paths_and_context_preserve_selected_session(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LUVUS_MODULE_ID':'kacper.toolkit','LUVUS_MODULE_CONFIG_DIR':tmp,'LUVUS_BIN_PATH':'selected','LUVUS_SOCKET_PATH':'selected-socket'}, clear=True):
            configure('tasks'); configure('project-commands'); configure('tasks')
            self.assertEqual(directory('tasks'),Path(tmp)/'features/tasks')
            self.assertEqual(os.environ['LUVUS_BIN_PATH'],'selected')
            self.assertEqual(os.environ['LUVUS_SOCKET_PATH'],'selected-socket')
            self.assertEqual(os.environ['LUVUS_MODULE_ID'],'kacper.toolkit')

    def test_transport_maps_names_without_changing_payload_text(self):
        args,_=request('tasks','ui.dock.push',{'id':'luvus-tasks','rows':[{'text':'open','action':'open','value':'open'}]})
        self.assertEqual(args['id'],'tasks-luvus-tasks')
        self.assertEqual(args['rows'],[{'text':'open','action':'tasks-open','value':'open'}])
        args,owner=request('cli-launcher','module.pane.open',{'module':'personal.luvus-tasks','entrypoint':'tasks'})
        self.assertEqual(args,{'module':'kacper.toolkit','entrypoint':'tasks-tasks'})
        self.assertEqual(owner,'tasks')
        original=['python',str(ROOT/'features/send-to-agent/launcher.py'),'ui','path with spaces','$literal']
        args,_=request('send-to-agent','terminal.backend.create',{'command':original})
        self.assertEqual(args['command'][-3:],original[-3:])
        self.assertIn(str(ROOT/'toolkit.py'),args['command'])
        argv,_,_,_=cli_args('git-sidebar',['bar','push','--id','git-actions','--content','[{"action":"refresh","text":"refresh"}]'])
        self.assertIn('git-sidebar-git-actions',argv)
        self.assertIn('git-sidebar-refresh',argv[-1])

    def test_migration_preserves_data_and_is_repeatable_and_conflict_safe(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LUVUS_HOME':tmp}, clear=True), patch('sys.argv',['toolkit.py','migrate','--apply']), contextlib.redirect_stdout(io.StringIO()):
            source=Path(tmp)/'modules/config/personal.luvus-tasks';source.mkdir(parents=True)
            (source/'config.json').write_text('{"connections":[]}')
            (source/'worktrees').mkdir();(source/'worktrees/private').write_text('leave here')
            with sqlite3.connect(source/'tasks.sqlite3') as db:
                db.execute('create table records (value text)');db.execute("insert into records values ('saved')")
            migrate();migrate()
            target=directory('tasks')
            self.assertFalse((target/'worktrees').exists())
            with sqlite3.connect(target/'tasks.sqlite3') as db:self.assertEqual(db.execute('select value from records').fetchone()[0],'saved')
            (target/'config.json').write_text('changed')
            with self.assertRaisesRegex(ValueError,'conflict'):migrate()
            self.assertEqual((target/'config.json').read_text(),'changed')

if __name__ == '__main__': unittest.main()
