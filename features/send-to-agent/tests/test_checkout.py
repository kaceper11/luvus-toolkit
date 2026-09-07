import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from checkout_state import review, validate, observe


class CheckoutTests(unittest.TestCase):
    def test_branch_change_blocks_but_dirty_and_different_repositories_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / x for x in ('source', 'recipient')]
            for root in roots:
                root.mkdir()
                subprocess.run(['git', '-C', str(root), 'init', '-b', 'main'], check=True, capture_output=True)
            record = {'cwd': str(roots[0]), 'target': {'cwd': str(roots[1])}}
            record['reviewed_checkouts'] = review(record)
            (roots[1] / 'dirty').write_text('change')
            validate(record)
            self.assertIn('dirty', observe(roots[1])['badge'])
            subprocess.run(['git', '-C', str(roots[1]), 'switch', '--orphan', 'wrong'], check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'branch changed'):
                validate(record)

    def test_changed_directory_and_missing_review_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'target'
            path.mkdir()
            record = {'cwd': str(path), 'target': None}
            with self.assertRaises(ValueError):
                validate(record)
            record['reviewed_checkouts'] = review(record)
            path.rename(path.with_name('old'))
            path.mkdir()
            with self.assertRaises(ValueError):
                validate(record)
