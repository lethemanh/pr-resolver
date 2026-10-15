"""Process adapter: prompt on stdin, output and recovery artifacts on disk."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid
from .progress import AgentProgress, report, outcome_summary
from .review_document import atomic_write, parse, pending, validate, format_results


def configured_codex_paths():
    override = os.environ.get('PR_RESOLVER_CODEX_PATHS')
    if override == '':
        return []
    paths = override.split(os.pathsep) if override is not None else [
        '/Applications/Codex.app/Contents/Resources/codex',
        '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
        '~/Applications/Codex.app/Contents/Resources/codex',
        '~/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
    ]
    if any(not p.strip() for p in paths):
        raise ValueError('PR_RESOLVER_CODEX_PATHS must not contain empty path entries')
    candidates = [Path(p).expanduser() for p in paths]
    if any(not p.is_absolute() for p in candidates):
        raise ValueError('PR_RESOLVER_CODEX_PATHS must contain absolute paths (or ~/ paths)')
    return candidates


def agent_environment(extra_names=()):
    # Never inherit arbitrary credentials, shell startup hooks, or loader settings.
    allowed = {'PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'TMP', 'TEMP',
               'LANG', 'LC_ALL', 'LC_CTYPE', 'TERM', 'COLORTERM',
               'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT'}
    return {name: os.environ[name] for name in allowed.union(extra_names)
            if name in os.environ}


def codex_executable():
    override = os.environ.get('PR_RESOLVER_CODEX')
    if override:
        executable = shutil.which(str(Path(override).expanduser()))
        if not executable:
            raise ValueError('PR_RESOLVER_CODEX must name an executable Codex CLI file')
        return executable
    executable = shutil.which('codex')
    if executable:
        return executable
    for candidate in configured_codex_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise ValueError(
        'Codex CLI not found on PATH or in configured fallback locations. '
        'Set PR_RESOLVER_CODEX to its absolute executable path, install Codex CLI, '
        'or supply --agent-command. The review document is safe; rerun resolve after fixing this.')


def agent_command(command, document):
    if command is not None:
        args = shlex.split(command)
        if not args:
            raise ValueError('Agent command cannot be empty')
        executable = shutil.which(str(Path(args[0]).expanduser()))
        if not executable:
            raise ValueError(f'Agent executable not found: {args[0]}. Supply an executable path with --agent-command.')
        return [executable, *args[1:]]
    return [codex_executable(), 'exec', '--sandbox', 'workspace-write',
            '--add-dir', str(document.parent), '-']


def run(repo, document, metadata, snapshot, command=None, include_resolved=False, agent_env=()):
    before = document.read_text()
    _, entries = parse(before)
    selected = pending(entries, include_resolved)
    report(f'[agent] Selected {len(selected)} pending items out of {len(entries)} document entries.')
    if not selected:
        outcome_summary(entries, label='document')
        print('No pending feedback selected.')
        return 0
    args = agent_command(command, document)
    # Keep documents and logs together outside the target checkout.
    directory = document.parent / 'runs' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)
    atomic_write(directory / 'before.md', before)
    atomic_write(directory / 'checkout.json', json.dumps(snapshot, indent=2))
    template = (Path(__file__).resolve().parent.parent / 'prompts' / 'resolve.md').read_text()
    prompt = template.replace('{{CONTEXT}}', json.dumps({
        'repository_path': str(repo.path), 'pr': metadata, 'review_document': str(document),
        'date': datetime.now().astimezone().date().isoformat(), 'selected_entry_ids': selected,
        'existing_changes': snapshot['status']}, indent=2))
    atomic_write(directory / 'prompt.md', prompt)
    code = 130
    print(f'Run artifacts: {directory}', flush=True)
    log_fd = os.open(directory / 'agent.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(log_fd, 'w') as log:
        try:
            process = subprocess.Popen(args, cwd=repo.path, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True,
                                       env=agent_environment(agent_env))
            try:
                report(f'[agent] Started {args[0]}; progress follows saved Result sections. Full output: {directory / "agent.log"}')
                with AgentProgress(document, before, selected):
                    process.communicate(prompt)
                code = process.returncode
            except KeyboardInterrupt:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        except OSError as exc:
            code = 127
            log.write(f'Agent failed to start: {exc}\n')
            raise
        finally:
            atomic_write(directory / 'exit.json', json.dumps({'exit_status': code}))
    report(f'[agent] Process exited with status {code}; validating results...')
    after = document.read_text() if document.exists() else ''
    try:
        validate(before, after, selected, complete=code == 0)
    except ValueError:
        atomic_write(directory / 'invalid-after.md', after)
        # Recover only valid, completed results; never trust changed source content.
        recovered = before
        try:
            from .review_document import ENTRY
            # Parse entries independently so a truncated final entry does not lose
            # earlier completed results. Full document validation already failed.
            header = before.split('\n', 1)[0] + '\n'
            new = {}
            for fragment in ENTRY.finditer(after):
                try:
                    _, isolated = parse(header + fragment.group(0))
                    new.update(isolated)
                except ValueError:
                    continue
            for match in list(ENTRY.finditer(before))[::-1]:
                key = match.group(1)
                if key in selected and key in new and new[key][0] == entries[key][0]:
                    source, result = new[key]
                    candidate = before[:match.start(3)] + result + before[match.end(3):]
                    try:
                        validate(before, candidate, selected, complete=False)
                    except ValueError:
                        continue
                    offset_match = next(m for m in ENTRY.finditer(recovered) if m.group(1) == key)
                    recovered = recovered[:offset_match.start(3)] + result + recovered[offset_match.end(3):]
        except ValueError:
            pass
        atomic_write(document, format_results(recovered, selected))
        report('[agent] Document validation failed; restored sources and recovered valid saved results.')
        outcome_summary(parse(recovered)[1], selected, 'recovered results')
        raise
    atomic_write(document, format_results(after, selected))
    outcome_summary(parse(after)[1], selected, 'run results')
    return code if code >= 0 else 128 - code
