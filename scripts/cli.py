import argparse
from pathlib import Path
import subprocess
import sys
from .repository import Repository
from .github import GitHub
from .review_document import refresh, parse, identity
from .agent import run
from .progress import report, outcome_summary


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def default_output(repo):
    return PROJECT_ROOT / 'reviews' / repo.path.name / 'RESOLVE_REVIEW.md'


def migrate_legacy_folder(repo, number):
    legacy = PROJECT_ROOT / 'reviews' / repo.host / repo.owner / repo.name
    destination = default_output(repo).parent
    if destination.exists() or not (legacy / 'RESOLVE_REVIEW.md').exists():
        return
    metadata, _ = parse((legacy / 'RESOLVE_REVIEW.md').read_text())
    if identity(metadata) != (repo.host, f'{repo.owner}/{repo.name}', number):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(destination)
    report(f'[document] Moved existing review folder to {destination}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Fetch and resolve PR feedback without publishing')
    parser.add_argument('command', choices=['fetch', 'resolve', 'run'])
    parser.add_argument('--repo', required=True)
    parser.add_argument('--pr', required=True, type=int)
    parser.add_argument('--remote')
    parser.add_argument('--trusted-host', action='append', default=[], metavar='HOST',
                        help='Explicitly trust an Enterprise hostname (repeatable); github.com is trusted by default')
    parser.add_argument('--agent-env', action='append', default=[], metavar='NAME',
                        help='Explicitly pass an environment variable to the agent (repeatable; may expose secrets)')
    parser.add_argument('--output', help='Document path; relative paths are relative to PR-resolver')
    parser.add_argument('--agent-command', help='Quoted executable and arguments; prompt is sent on stdin (no shell)')
    parser.add_argument('--include-resolved', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.pr <= 0:
            raise ValueError('PR number must be positive')
        report('[repository] Validating checkout and GitHub remote...')
        repo = Repository.discover(args.repo, args.remote, args.trusted_host)
        if not args.output:
            migrate_legacy_folder(repo, args.pr)
        output = (PROJECT_ROOT / Path(args.output).expanduser()).resolve() if args.output else default_output(repo)
        if args.command in ('fetch', 'run') and not args.output:
            output.parent.mkdir(parents=True, exist_ok=True)
        if args.command in ('fetch', 'run'):
            metadata, entries = GitHub(repo).fetch(args.pr)
            refresh(output, metadata, entries)
            report(f'[document] Saved {len(entries)} feedback entries: {output}')
            outcome_summary(parse(output.read_text())[1], label='document')
        if args.command in ('resolve', 'run'):
            metadata, _ = parse(output.read_text())
            if identity(metadata) != (repo.host, f'{repo.owner}/{repo.name}', args.pr):
                raise ValueError('Document does not match requested repository and PR')
            # Recheck live head so a stale document cannot launch against an obsolete PR head.
            report('[repository] Checking current PR head and local changes before starting agent...')
            live = GitHub(repo).api(f'repos/{repo.owner}/{repo.name}/pulls/{args.pr}')
            if live['head']['sha'] != metadata['sha']:
                raise ValueError('PR head changed; fetch again before resolving')
            snapshot = repo.snapshot(metadata['sha'])
            return run(repo, output, metadata, snapshot, args.agent_command, args.include_resolved, args.agent_env)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
