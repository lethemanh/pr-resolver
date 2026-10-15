"""Local checkout discovery. Never mutates Git state."""
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


@dataclass
class Repository:
    path: Path
    host: str
    owner: str
    name: str
    trusted_hosts: tuple = ('github.com',)

    def validate_host(self):
        if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', self.host)
                or self.host not in self.trusted_hosts):
            raise ValueError('Untrusted GitHub host; explicitly allow your Enterprise hostname with --trusted-host HOST')

    @classmethod
    def discover(cls, path, remote=None, trusted_hosts=()):
        path = Path(path).expanduser().resolve(strict=True)
        root = Path(git(path, 'rev-parse', '--show-toplevel')).resolve()
        remotes = git(root, 'remote').splitlines()
        if remote is None:
            if len(remotes) != 1:
                raise ValueError('Select --remote explicitly when there are zero or multiple remotes')
            remote = remotes[0]
        if remote not in remotes:
            raise ValueError(f'Unknown remote: {remote}')
        url = git(root, 'remote', 'get-url', remote)
        if re.match(r'^[^/]+@[^:]+:', url):
            host, slug = url.split('@', 1)[1].split(':', 1)
        else:
            parsed = urlparse(url)
            if parsed.scheme not in ('https', 'http', 'ssh', 'git') or not parsed.hostname:
                raise ValueError('Remote must be a GitHub HTTPS or SSH URL')
            host, slug = parsed.hostname, parsed.path.lstrip('/')
        slug = slug.removesuffix('.git').rstrip('/')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', slug):
            raise ValueError('Remote must identify owner/repository')
        repository = cls(root, host.lower(), *slug.split('/'),
                         trusted_hosts=('github.com', *(h.lower() for h in trusted_hosts)))
        repository.validate_host()
        return repository

    def snapshot(self, sha):
        head = git(self.path, 'rev-parse', 'HEAD')
        if head != sha:
            raise ValueError(f'Checkout HEAD {head} does not match PR head {sha}; check out the PR yourself')
        return {'head': head, 'branch': git(self.path, 'branch', '--show-current'),
                'status': git(self.path, 'status', '--porcelain=v1'),
                'diff': git(self.path, 'diff', 'HEAD', '--binary')}
