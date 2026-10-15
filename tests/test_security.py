import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from scripts.agent import agent_environment, run
from scripts.github import GitHub
from scripts.repository import Repository
from scripts.review_document import refresh


class SecurityTests(unittest.TestCase):
    def discover(self, remote, trusted=()):
        with tempfile.TemporaryDirectory() as folder:
            with patch('scripts.repository.git', side_effect=[folder, 'origin', remote]):
                return Repository.discover(folder, trusted_hosts=trusted)

    def test_untrusted_remote_rejected(self):
        for remote in ('https://attacker.invalid/team/project.git',
                       'git@github.com.attacker.invalid:team/project.git',
                       'git@attacker.invalid:team/project.git'):
            with self.subTest(remote=remote), self.assertRaisesRegex(ValueError, 'Untrusted'):
                self.discover(remote)

    def test_public_and_explicit_enterprise_hosts(self):
        self.assertEqual(self.discover('git@GitHub.com:team/project.git').host, 'github.com')
        self.assertEqual(self.discover('https://git.example.com/team/project.git',
                                       ['git.example.com']).host, 'git.example.com')

    def test_api_checks_host_before_starting_gh(self):
        repo = Repository(Path('/tmp'), 'attacker.invalid', 'team', 'project')
        with patch('scripts.github.subprocess.check_output') as call:
            with self.assertRaisesRegex(ValueError, 'Untrusted'):
                GitHub(repo).api('repos/team/project/pulls/1')
            call.assert_not_called()

    def test_environment_drops_secrets_and_hooks(self):
        environment = {'PATH': '/usr/bin', 'HOME': '/tmp', 'GH_TOKEN': 'fake',
                       'OPENAI_API_KEY': 'fake', 'AWS_SECRET_ACCESS_KEY': 'fake',
                       'BASH_ENV': '/tmp/startup', 'PYTHONPATH': '/tmp/injection'}
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(agent_environment(), {'PATH': '/usr/bin', 'HOME': '/tmp'})
            self.assertEqual(agent_environment(['OPENAI_API_KEY'])['OPENAI_API_KEY'], 'fake')
            self.assertNotIn('GH_TOKEN', agent_environment(['OPENAI_API_KEY']))

    def test_child_environment_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document = root / 'review.md'
            meta = dict(host='github.com', repository='team/project', number=1,
                        sha='abc', url='https://github.com/team/project/pull/1',
                        base='main', head='feature')
            refresh(document, meta, [dict(id='comment-1', kind='comment',
                                         data=dict(body='Review this code'))])
            repo = Repository(root, 'github.com', 'team', 'project')
            script = ('import os,sys; '
                      'assert "PR_RESOLVER_TEST_SECRET" not in os.environ; '
                      'assert "GH_TOKEN" not in os.environ; '
                      'sys.stdin.read(); sys.exit(7)')
            command = shlex.join([sys.executable, '-c', script])
            previous_umask = os.umask(0)
            try:
                with patch.dict(os.environ, {'PR_RESOLVER_TEST_SECRET': 'fake', 'GH_TOKEN': 'fake'}):
                    self.assertEqual(run(repo, document, meta, {'status': '', 'diff': ''}, command), 7)
            finally:
                os.umask(previous_umask)
            directory = next((root / 'runs').iterdir())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            for artifact in directory.iterdir():
                self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600, artifact.name)

    def test_secret_files_ignored(self):
        import subprocess
        root = Path(__file__).resolve().parent.parent
        for name in ('.env', '.env.local', 'credentials.pem', 'private.key', 'id_ed25519'):
            result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', name], cwd=root)
            self.assertEqual(result.returncode, 0, name)


if __name__ == '__main__':
    unittest.main()
