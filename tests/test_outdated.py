from pathlib import Path
import tempfile
import unittest

from scripts.github import classify
from scripts.review_document import DEFAULT, parse, pending, refresh, render_document


class OutdatedTests(unittest.TestCase):
    def source(self, outdated=False, resolved=False):
        return dict(id='thread-1', kind='thread',
                    data=dict(isOutdated=outdated, isResolved=resolved, comments=[]))

    def test_old_documents_skip_outdated_with_or_without_include_resolved(self):
        for resolved in (False, True):
            entries = {'thread-1': (self.source(True, resolved), DEFAULT)}
            for include in (False, True):
                self.assertEqual(pending(entries, include), [])
        self.assertEqual(pending({'thread-1': (self.source(), DEFAULT)}), ['thread-1'])

    def test_refresh_marks_outdated_as_context_and_preserves_results(self):
        meta = dict(version=1, host='github.com', repository='owner/repo', number=1,
                    sha='abc', url='https://github.com/owner/repo/pull/1',
                    base='main', head='feature', fetched_at='2026-10-22')
        result = DEFAULT.replace('Assessment: ', 'Assessment: Previous assessment')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'review.md'
            source = self.source(True)
            path.write_text(render_document(meta, [(source, result)]))
            previous = parse(path.read_text())[1]['thread-1'][1]
            text = refresh(path, meta, classify([source]))
            entries = parse(text)[1]
            self.assertIn('Outdated GitHub thread', entries['thread-1'][0]['context_only'])
            self.assertEqual(entries['thread-1'][1], previous)
            self.assertEqual(pending(entries, True), [])
            refreshed = refresh(path, meta, classify([self.source()]))
            self.assertEqual(pending(parse(refreshed)[1]), ['thread-1'])
