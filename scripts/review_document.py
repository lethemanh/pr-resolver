"""Versioned Markdown contract with strictly delimited source and result regions."""
import base64
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

HEADER = '<!-- pr-resolver:v1 '
ENTRY = re.compile(r'<!-- entry:([\w-]+) -->\n### Source\n((?:```json\n.*?\n```|.*?<!-- source-data:[A-Za-z0-9+/=]+ -->))\n### Result\n(.*?)<!-- /entry:\1 -->', re.S)
DEFAULT = 'Status: pending\nAssessment: \nChanges: \nValidation: \nSuggested reply: \n'
STATUSES = {'pending', 'changed', 'answered', 'blocked', 'skipped'}


def canonical(value):
    # Escape Markdown fence and HTML marker characters inside original strings.
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2).replace('`', '\\u0060').replace('<', '\\u003c')


def readable_source(source):
    data = source['data']
    kind = source['kind']
    lines = [f"**{kind.title()}** · ID: `{source['id']}`", '']
    if source.get('context_only'):
        lines += [f"**Context only — excluded from agent tasks:** {source['context_only']}", '',
                  '*Any existing Result below is retained history, not an active task.*', '']
    if source.get('retired'):
        lines += ['**Retired:** no longer returned by GitHub.', '']
    if kind == 'thread':
        location = data.get('path', 'Unknown file')
        line = data.get('line') or data.get('originalLine')
        if line is not None:
            location += f':{line}'
        lines += [f"**File:** `{location}`", '',
                  f"**Thread:** {'Resolved' if data.get('isResolved') else 'Unresolved'} · "
                  f"{'Outdated' if data.get('isOutdated') else 'Current'}", '']
        comments = data['comments']
        reviews = {c['pullRequestReview']['url'] for c in comments if c.get('pullRequestReview')}
        for url in sorted(reviews):
            lines += [f'**Submitted review:** [View on GitHub]({url})', '']
        diff = next((c.get('diffHunk') for c in comments if c.get('diffHunk')), None)
        if diff:
            fence = '`' * max(3, max((len(m) + 1 for m in re.findall(r'`+', diff)), default=3))
            lines += ['**Diff context**', '', fence + 'diff', diff, fence, '']
    else:
        comments = [data]
        if kind == 'review':
            lines += [f"**Review:** {data.get('state', 'SUBMITTED')}", '']
    for index, comment in enumerate(comments):
        author = (comment.get('author') or comment.get('user') or {}).get('login', 'Unknown author')
        url = comment.get('url') or comment.get('html_url')
        when = comment.get('createdAt') or comment.get('created_at') or comment.get('submitted_at') or ''
        label = 'Reply' if index else 'Comment'
        link = f'[{label} on GitHub]({url})' if url else label
        lines += [f"**@{author}** · {when} · {link}", '']
        updated = comment.get('updatedAt') or comment.get('updated_at')
        if updated and updated != when:
            lines += [f'*Edited: {updated}*', '']
        body = comment.get('body') or '*No comment body.*'
        # Expand GitHub disclosure blocks in the local reading view. Exact original
        # HTML/Markdown remains in source-data for refreshes and integrity checks.
        body = re.sub(r'<!--.*?-->', '', body, flags=re.S)
        body = re.sub(r'<summary>(.*?)</summary>', r'\n**\1**\n', body, flags=re.S | re.I)
        body = re.sub(r'</?details(?:\s[^>]*)?>', '', body, flags=re.I)
        lines += ['\n'.join('> ' + line for line in body.splitlines()), '', '---', '']
    rendered = '\n'.join(lines)
    # Feedback cannot inject document-control markers. Exact original remains encoded.
    rendered = re.sub(r'<!-- (?=(?:/?entry:|source-data:))', '&lt;!-- ', rendered)
    encoded = base64.b64encode(canonical(source).encode()).decode()
    return rendered + f'\n<!-- source-data:{encoded} -->'


def atomic_write(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def parse(text):
    try:
        first, body = text.split('\n', 1)
        if not first.startswith(HEADER) or not first.endswith(' -->'):
            raise ValueError('Missing document header')
        meta = json.loads(first[len(HEADER):-4])
        if meta['version'] != 1 or not all(k in meta for k in ('host', 'repository', 'number', 'sha')):
            raise ValueError('Invalid document metadata')
        entries = {}
        matches = list(ENTRY.finditer(body))
        if body.count('<!-- entry:') != len(matches) or body.count('<!-- /entry:') != len(matches):
            raise ValueError('Malformed entry markers')
        for match in matches:
            key, source, result = match.groups()
            if key in entries:
                raise ValueError('Duplicate entry marker')
            if source.startswith('```json\n'):
                source = json.loads(source[8:-4])
            else:
                encoded = re.search(r'<!-- source-data:([A-Za-z0-9+/=]+) -->$', source).group(1)
                source = json.loads(base64.b64decode(encoded, validate=True))
            if source['id'] != key:
                raise ValueError('Entry ID mismatch')
            status(result)
            entries[key] = (source, result)
        return meta, entries
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Malformed review document: {exc}') from exc


RESULT_FIELDS = r'Status|Assessment|Changes|Validation|Suggested reply'


def plain_result(result):
    return re.sub(rf'^\*\*({RESULT_FIELDS}):\*\*', r'\1:', result, flags=re.M)


def format_result(result):
    plain = plain_result(result)
    formatted = re.sub(rf'^[ \t]*({RESULT_FIELDS}):[ \t]*',
                       lambda m: '\n\n**' + m[1] + ':** ', plain, flags=re.M)
    formatted = re.sub(r'\n{3,}(?=\*\*(?:' + RESULT_FIELDS + r'):\*\*)', '\n\n', formatted)
    return formatted.strip() + '\n\n'


def format_results(text, selected=None):
    def replace(match):
        if selected is not None and match[1] not in selected:
            return match[0]
        start, end = match.start(3) - match.start(), match.end(3) - match.start()
        return match[0][:start] + format_result(match[3]) + match[0][end:]
    return ENTRY.sub(replace, text)


def status(result):
    result = plain_result(result)
    matches = re.findall(r'^Status: (\w+)$', result, re.M)
    if len(matches) != 1 or matches[0] not in STATUSES:
        raise ValueError('Each result needs exactly one valid Status')
    for field in ('Assessment', 'Changes', 'Validation', 'Suggested reply'):
        if len(re.findall(rf'^{field}:', result, re.M)) != 1:
            raise ValueError(f'Result missing or duplicating {field}')
    return matches[0]


def identity(meta):
    return tuple(meta[k] for k in ('host', 'repository', 'number'))


def feedback_content(source):
    # Display classification and newly fetched review links are not edited feedback.
    source = json.loads(json.dumps(source))
    source.pop('context_only', None)
    if source['kind'] == 'thread':
        for comment in source['data']['comments']:
            comment.pop('pullRequestReview', None)
    return source


def refresh(path, meta, fetched):
    path = Path(path)
    old_meta, old = parse(path.read_text()) if path.exists() else (None, {})
    if old_meta and identity(old_meta) != identity(meta):
        raise ValueError('Document belongs to another PR; select a different --output path')
    meta = {**meta, 'version': 1, 'fetched_at': datetime.now(timezone.utc).isoformat()}
    rows = []
    for source in fetched:
        key = source['id']
        result = DEFAULT
        if key in old:
            previous, result = old.pop(key)
            if feedback_content(previous) != feedback_content(source) or old_meta['sha'] != meta['sha']:
                result = re.sub(r'^(\*\*)?Status:(?:\*\*)? \w+$', lambda m: '**Status:** pending' if m[1] else 'Status: pending', result, flags=re.M)
                result += '\nReassessment required: feedback or PR head changed.\n'
        rows.append((source, result))
    # Preserve results for deleted feedback, with explicit retired source state.
    excluded_ids = set(meta.get('excluded_entry_ids', []))
    for source, result in old.values():
        if source['id'] in excluded_ids:
            continue
        rows.append(({**source, 'retired': True}, result))
    text = render_document(meta, rows)
    parse(text)
    atomic_write(path, text)
    return text


def render_document(meta, rows):
    text = HEADER + canonical(meta).replace('\n', ' ') + ' -->\n# PR review resolution\n\n'
    text += f"PR: {meta['url']}\nBase: {meta['base']} | Head: {meta['head']} | SHA: {meta['sha']}\nFetched: {meta['fetched_at']}\n\n"
    rows = list(rows)
    rendered = set()
    def emit(source, result):
        nonlocal text
        rendered.add(source['id'])
        text += f"<a id=\"{source['id']}\"></a>\n"
        text += f"<!-- entry:{source['id']} -->\n### Source\n{readable_source(source)}\n### Result\n{format_result(result)}<!-- /entry:{source['id']} -->\n\n"
    for source, result in rows:
        if source['kind'] != 'review':
            continue
        children = [(s, r) for s, r in rows if s['kind'] == 'thread'
                    and any(c.get('pullRequestReview', {}).get('databaseId') == source['data']['id']
                            for c in s['data']['comments'])]
        text += f"## Submitted review [{source['data']['id']}]({source['data'].get('html_url', meta['url'])})\n\n"
        if children:
            text += '**Inline threads in this review:**\n\n'
            for child, _ in children:
                text += f"- [{child['data'].get('path', child['id'])}](#{child['id']})\n"
            text += '\n'
        emit(source, result)
        for child, outcome in children:
            if child['id'] not in rendered:
                emit(child, outcome)
    for source, result in rows:
        if source['id'] not in rendered:
            text += '## General PR comment (context)\n\n' if source.get('context_only') else ''
            emit(source, result)
    return text


def pending(entries, include_resolved=False):
    return [key for key, (source, result) in entries.items()
            if status(result) == 'pending' and not source.get('retired') and not source.get('context_only')
            and (include_resolved or not source['data'].get('isResolved', False))]


def validate(before, after, selected, complete):
    meta, entries = parse(before)
    new_meta, new = parse(after)
    def source_regions(text):
        return ENTRY.sub(lambda m: m.group(0)[:m.start(3) - m.start()] + '<RESULT>\n' + m.group(0)[m.end(3) - m.start():], text)
    if source_regions(before) != source_regions(after):
        raise ValueError('Agent changed content outside result sections')
    if meta != new_meta or entries.keys() != new.keys():
        raise ValueError('Agent changed document metadata or entry IDs')
    for key, (source, result) in new.items():
        if source != entries[key][0]:
            raise ValueError(f'Agent modified source: {key}')
        outcome = status(result)
        if key not in selected and result != entries[key][1]:
            raise ValueError(f'Agent modified unselected result: {key}')
        if key in selected and outcome != 'pending':
            for field in ('Assessment', 'Changes', 'Validation', 'Suggested reply'):
                if not re.search(rf'^{field}:[ \t]*[^\s][^\n]*', plain_result(result), re.M):
                    raise ValueError(f'{key}: completed outcome needs {field}')
        if complete and key in selected and outcome == 'pending':
            raise ValueError(f'Agent left item pending: {key}')
