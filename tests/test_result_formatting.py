from pathlib import Path
import tempfile
import unittest

from scripts.review_document import (
    DEFAULT, ENTRY, format_result, format_results, parse, plain_result,
    refresh, render_document, validate,
)


class ResultFormattingTests(unittest.TestCase):
    def setUp(self):
        self.meta = dict(version=1, host='github.com', repository='owner/repo',
                         number=1, sha='abc', url='https://github.com/owner/repo/pull/1',
                         base='main', head='feature', fetched_at='2026-10-16')
        self.source = dict(id='comment-1', kind='comment', data=dict(body='Feedback'))

    def assert_formatted(self, text):
        for match in ENTRY.finditer(text):
            result = match[3]
            self.assertEqual(result, format_result(result))
            for field in ('Status', 'Assessment', 'Changes', 'Validation', 'Suggested reply'):
                self.assertIn(f'**{field}:**', result)
            self.assertIn('\n\n**Assessment:**', result)

    def test_new_document_formats_pending_results(self):
        text = render_document(self.meta, [(self.source, DEFAULT)])
        self.assert_formatted(text)
        self.assertEqual(parse(text)[1]['comment-1'][0], self.source)

    def test_refresh_formats_legacy_results_including_retired(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'review.md'
            text = render_document(self.meta, [(self.source, DEFAULT)])
            legacy = ENTRY.sub(lambda m: m[0].replace(m[3], DEFAULT), text)
            path.write_text(legacy)
            updated = refresh(path, self.meta, [self.source])
            self.assert_formatted(updated)
            retired = refresh(path, self.meta, [])
            self.assert_formatted(retired)
            self.assertTrue(parse(retired)[1]['comment-1'][0]['retired'])

    def test_formatting_preserves_source_and_multiline_assessment(self):
        result = ('Status: changed\nAssessment: Valid finding\n\n- first\n- second\n'
                  'Changes: Updated code\nValidation: Checks passed\n'
                  'Suggested reply: Fixed\n')
        text = render_document(self.meta, [(self.source, result)])
        legacy = ENTRY.sub(lambda m: m[0].replace(m[3], result), text)
        formatted = format_results(legacy)
        validate(legacy, formatted, ['comment-1'], complete=True)
        self.assert_formatted(formatted)
        self.assertIn('- first\n- second', formatted)
        self.assertEqual(format_results(formatted), formatted)
        self.assertEqual(plain_result(parse(formatted)[1]['comment-1'][1]).splitlines()[0],
                         'Status: changed')
