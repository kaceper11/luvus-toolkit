import tempfile
import unittest
from unittest.mock import patch
import tab_titles as titles


class NamingTests(unittest.TestCase):
    def test_exact_id_manual_name_and_background_retry(self):
        current = {'tab_id':'tab-1', 'name':None}
        pane = {'terminal_id':'terminal-1', 'tab_id':'tab-1'}
        mutations = []
        active = True
        def call(method, **params):
            if method == 'pane.get': return dict(pane)
            if method == 'tab.list': return {'tabs':[dict(current)] if active else []}
            if method == 'tab.rename':
                self.assertEqual(params['tab_id'], current['tab_id'])
                current['name'] = params['name']; mutations.append(params)
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {'LUVUS_MODULE_STATE_DIR':root}, clear=True), patch.object(titles.subprocess, 'Popen'):
            active = False
            self.assertFalse(titles.remember(call, '4', '↗ Send to agent'))
            self.assertFalse(mutations)
            record = {'pane':'4', **pane, 'title':'↗ Send to agent'}
            active = True
            self.assertTrue(titles.apply(call, record))
            current['name'] = 'My custom name'
            titles.remember(call, '4', 'New module default')
            self.assertEqual(current['name'], 'My custom name')
            pane['terminal_id'] = 'replacement'
            self.assertFalse(titles.apply(call, record))
            self.assertEqual(len(mutations), 1)

    def test_workspace_switch_fails_closed_and_titles_are_bounded(self):
        def call(method, **params):
            if method == 'pane.get': return {'terminal_id':'t', 'tab_id':'exact'}
            if method == 'tab.list': return {'tabs':[{'tab_id':'exact','name':None}]}
            if method == 'tab.rename': raise ValueError('not_found: workspace changed')
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {'LUVUS_MODULE_STATE_DIR':root}, clear=True), patch.object(titles.subprocess, 'Popen'):
            self.assertFalse(titles.remember(call, '1', 'Codex · issue-42 · ' + 'long '*20))
        self.assertEqual(len(titles.title('Codex · issue-42 · ' + 'long '*20)),40)
        self.assertNotIn('\x1b', titles.title('a\x1b\n b'))


if __name__ == '__main__': unittest.main()
