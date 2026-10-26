from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.cli import main
from scripts.repository import Repository, git
from scripts.review_document import refresh


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        git(self.root, 'init', '-b', 'main')
        git(self.root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
            'commit', '--allow-empty', '-m', 'initial')
        self.sha = git(self.root, 'rev-parse', 'HEAD')
        self.repo = Repository(self.root, 'github.com', 'owner', 'repo')

    def test_switches_even_when_other_branch_has_same_commit(self):
        # Capture the real subprocess runner before mocking the gh invocation.
        runner = subprocess.run
        def checkout(command, **kwargs):
            self.assertEqual(command, ['gh', 'pr', 'checkout', '123', '--repo',
                                       'github.com/owner/repo'])
            return runner(['git', '-C', str(self.root), 'switch', '-c', 'feature'], check=True,
                          capture_output=True)
        with patch('scripts.repository.subprocess.check_call', side_effect=checkout):
            self.repo.checkout_pr(123, 'feature', self.sha)
        self.assertEqual(self.repo.snapshot(self.sha)['branch'], 'feature')

    def test_already_on_pr_branch_does_not_checkout(self):
        with patch('scripts.repository.subprocess.check_call') as checkout:
            self.repo.checkout_pr(123, 'main', self.sha)
            checkout.assert_not_called()

    def test_dirty_checkout_stops_without_switching(self):
        tracked = self.root / 'tracked.txt'
        tracked.write_text('original')
        git(self.root, 'add', 'tracked.txt')
        git(self.root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
            'commit', '-m', 'tracked file')
        sha = git(self.root, 'rev-parse', 'HEAD')
        for kind in ('unstaged', 'staged', 'untracked'):
            with self.subTest(kind=kind):
                file = self.root / ('new.txt' if kind == 'untracked' else 'tracked.txt')
                file.write_text('changed')
                if kind == 'staged':
                    git(self.root, 'add', 'tracked.txt')
                with patch('scripts.repository.subprocess.check_call') as checkout:
                    with self.assertRaisesRegex(ValueError, 'Checkout has local changes'):
                        self.repo.checkout_pr(123, 'feature', sha)
                    checkout.assert_not_called()
                self.assertEqual(git(self.root, 'branch', '--show-current'), 'main')
                if kind == 'untracked':
                    file.unlink()
                else:
                    git(self.root, 'restore', '--staged', 'tracked.txt')
                    git(self.root, 'restore', 'tracked.txt')

    def test_resolve_dirty_checkout_never_starts_agent(self):
        with tempfile.TemporaryDirectory() as documents:
            document = Path(documents) / 'review.md'
            refresh(document, dict(host='github.com', repository='owner/repo', number=123,
                                   sha=self.sha, head='feature', base='main', url='https://github.com/owner/repo/pull/123'), [])
            (self.root / 'untracked.txt').write_text('local work')
            with patch('scripts.cli.Repository.discover', return_value=self.repo), \
                    patch('scripts.cli.GitHub') as github, patch('scripts.cli.run') as agent:
                github.return_value.api.return_value = dict(head=dict(sha=self.sha))
                self.assertEqual(main(['resolve', '--repo', str(self.root), '--pr', '123',
                                       '--output', str(document)]), 1)
                agent.assert_not_called()

    def test_checkout_failure_propagates(self):
        with patch('scripts.repository.subprocess.check_call',
                   side_effect=subprocess.CalledProcessError(1, ['gh'])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.repo.checkout_pr(123, 'feature', self.sha)
