from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.cli import default_output, main
from scripts.repository import Repository
from scripts.review_document import parse, refresh


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.repo = Repository(self.root / 'my-app', 'github.com', 'owner', 'repo')
        patcher = patch('scripts.cli.PROJECT_ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def metadata(self, number):
        return dict(host='github.com', repository='owner/repo', number=number,
                    sha='abc', url=f'https://github.com/owner/repo/pull/{number}',
                    base='main', head='feature')

    def test_fetch_multiple_prs_preserves_documents(self):
        with patch('scripts.cli.Repository.discover', return_value=self.repo), \
                patch('scripts.cli.GitHub') as github:
            for number in (1, 2, 1):
                github.return_value.fetch.return_value = (self.metadata(number), [])
                self.assertEqual(main(['fetch', '--repo', str(self.repo.path), '--pr', str(number)]), 0)
        for number in (1, 2):
            path = default_output(self.repo, number)
            self.assertEqual(path, self.root / 'reviews/my-app' / f'pr-{number}/RESOLVE_REVIEW.md')
            self.assertEqual(parse(path.read_text())[0]['number'], number)

    def test_reuses_matching_legacy_layouts_without_moving_logs(self):
        for directory in ('my-app', 'github.com/owner/repo'):
            with self.subTest(directory=directory):
                path = self.root / 'reviews' / directory / 'RESOLVE_REVIEW.md'
                path.parent.mkdir(parents=True, exist_ok=True)
                refresh(path, self.metadata(1), [])
                self.assertEqual(default_output(self.repo, 1), path)
                self.assertNotEqual(default_output(self.repo, 2), path)
                path.unlink()

    def test_explicit_output_still_rejects_other_pr(self):
        path = self.root / 'custom.md'
        refresh(path, self.metadata(1), [])
        original = path.read_text()
        with patch('scripts.cli.Repository.discover', return_value=self.repo), \
                patch('scripts.cli.GitHub') as github:
            github.return_value.fetch.return_value = (self.metadata(2), [])
            self.assertEqual(main(['fetch', '--repo', str(self.repo.path), '--pr', '2',
                                   '--output', str(path)]), 1)
        self.assertEqual(path.read_text(), original)
