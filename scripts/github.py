"""Deterministic, read-only GitHub CLI client."""
import json
import subprocess
from .progress import report, fetch_summary

THREAD_FIELDS = '''id isResolved isOutdated path line originalLine startLine diffSide
comments(first:100, after:$cursor) { nodes { id databaseId body createdAt updatedAt url
 author { login } diffHunk path line originalLine replyTo { id } pullRequestReview { databaseId url } }
 pageInfo { hasNextPage endCursor } }'''


class GitHub:
    def __init__(self, repo):
        self.repo = repo

    def api(self, endpoint, fields=None):
        self.repo.validate_host()
        cmd = ['gh', 'api', '--hostname', self.repo.host, endpoint]
        if fields:
            for key, value in fields.items():
                cmd += ['-F', f'{key}={value}']
        result = json.loads(subprocess.check_output(cmd, cwd=self.repo.path, text=True))
        if isinstance(result, dict) and result.get('errors'):
            raise ValueError(f'GitHub GraphQL errors: {result["errors"]}')
        return result

    def pages(self, endpoint):
        page = 1
        while True:
            rows = self.api(f'{endpoint}?per_page=100&page={page}')
            report(f'[fetch] {endpoint}: page {page}, {len(rows)} records')
            yield from rows
            if len(rows) < 100:
                break
            page += 1

    def fetch(self, number, include_resolved=False):
        r = self.repo
        prefix = f'repos/{r.owner}/{r.name}'
        report(f'[fetch] Reading PR #{number} metadata for {r.owner}/{r.name}...')
        pr = self.api(f'{prefix}/pulls/{number}')
        metadata = {'host': r.host, 'repository': f'{r.owner}/{r.name}', 'number': number,
                    'url': pr['html_url'], 'base': pr['base']['ref'],
                    'head': pr['head']['ref'], 'sha': pr['head']['sha']}
        entries = []
        for row in self.pages(f'{prefix}/issues/{number}/comments'):
            entries.append({'id': f'comment-{row["id"]}', 'kind': 'comment', 'data': row})
        for row in self.pages(f'{prefix}/pulls/{number}/reviews'):
            if row.get('state') != 'PENDING':
                entries.append({'id': f'review-{row["id"]}', 'kind': 'review', 'data': row})
        report('[fetch] Reading thread resolution status...')
        cursor = None
        resolved_review_ids = set()
        active_review_ids = set()
        excluded_ids = set()
        while True:
            # GitHub cannot filter reviewThreads by resolution server-side.
            # Read only status and parent review IDs, then load selected bodies.
            query = '''query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){
reviewThreads(first:100,after:$after){nodes{id isResolved
comments(first:1){nodes{pullRequestReview{databaseId}}}}
pageInfo{hasNextPage endCursor}}}}}'''
            fields = dict(query=query, owner=r.owner, name=r.name, number=number)
            if cursor:
                fields['after'] = cursor
            connection = self.api('graphql', fields)['data']['repository']['pullRequest']['reviewThreads']
            for summary in connection['nodes']:
                review_ids = {c['pullRequestReview']['databaseId']
                              for c in summary['comments']['nodes'] if c.get('pullRequestReview')}
                if summary['isResolved'] and not include_resolved:
                    resolved_review_ids.update(review_ids)
                    excluded_ids.add(f'thread-{summary["id"]}')
                    continue
                query2 = '''query($id:ID!,$cursor:String){node(id:$id){... on PullRequestReviewThread{FIELDS}}}'''.replace('FIELDS', THREAD_FIELDS)
                thread = self.api('graphql', dict(query=query2, id=summary['id']))['data']['node']
                # A thread can become resolved between the status and detail calls.
                if thread['isResolved'] and not include_resolved:
                    resolved_review_ids.update(review_ids)
                    excluded_ids.add(f'thread-{summary["id"]}')
                    continue
                active_review_ids.update(review_ids)
                comments = thread['comments']
                nodes = list(comments['nodes'])
                while comments['pageInfo']['hasNextPage']:
                    comments = self.api('graphql', dict(query=query2, id=thread['id'], cursor=comments['pageInfo']['endCursor']))['data']['node']['comments']
                    nodes.extend(comments['nodes'])
                    report(f"[fetch] Thread {thread['id']}: {len(nodes)} comments read...")
                thread['comments'] = list({c['id']: c for c in nodes}.values())
                entries.append({'id': f'thread-{thread["id"]}', 'kind': 'thread', 'data': thread})
            report(f"[fetch] Thread page: {len(connection['nodes'])} statuses scanned")
            if not connection['pageInfo']['hasNextPage']:
                break
            cursor = connection['pageInfo']['endCursor']
        if not include_resolved:
            # Reviews have no resolution flag. Drop parents whose threads are all
            # resolved, but retain standalone review bodies and active parents.
            excluded_ids.update(e['id'] for e in entries if e['kind'] == 'review'
                                and e['data']['id'] in resolved_review_ids - active_review_ids)
            entries = [e for e in entries if not (e['kind'] == 'review'
                       and e['data']['id'] in resolved_review_ids - active_review_ids)]
        metadata['excluded_entry_ids'] = sorted(excluded_ids)
        report('[fetch] Verifying selected inline comments against REST...')
        self.verify_inline_comments(number, entries, selected_only=not include_resolved)
        entries = classify(list({e['id']: e for e in entries}.values()))
        fetch_summary(entries)
        return metadata, entries

    def verify_inline_comments(self, number, entries, selected_only=False):
        endpoint = f'repos/{self.repo.owner}/{self.repo.name}/pulls/{number}/comments'
        fetched = {comment['databaseId']: comment
                   for entry in entries if entry['kind'] == 'thread'
                   for comment in entry['data']['comments']}
        if selected_only:
            # Avoid downloading resolved comment bodies through the REST inventory.
            rest = {key: self.api(f'repos/{self.repo.owner}/{self.repo.name}/pulls/comments/{key}')
                    for key in fetched}
            if any(row['id'] != key for key, row in rest.items()):
                raise ValueError('GitHub returned an inconsistent inline comment ID. Retry fetch.')
        else:
            rest = {row['id']: row for row in self.pages(endpoint)}
        missing = (fetched.keys() - rest.keys()) if selected_only else (rest.keys() - fetched.keys())
        if missing:
            raise ValueError(f'Incomplete inline feedback: GitHub comment IDs missing from selected inventory: {sorted(missing)}. Retry fetch.')
        for key in (fetched.keys() & rest.keys()):
            row = rest[key]
            comment = fetched[key]
            if (comment['body'] != row['body'] or
                    (comment.get('pullRequestReview') or {}).get('databaseId') != row['pull_request_review_id']):
                raise ValueError(f'GitHub feedback changed during fetch or review association differs for comment {key}. Retry fetch.')


def context_reason(source, inline_review_ids=()):
    """Conservative known-bot notices only; never infer human intent from keywords."""
    data = source['data']
    author = (data.get('user') or {}).get('login', '').removesuffix('[bot]')
    body = data.get('body', '')
    if author != 'coderabbitai':
        return None
    if source['kind'] == 'comment' and any(marker in body for marker in (
            '<!-- walkthrough_start -->',
            '<!-- This is an auto-generated comment: rate limited by coderabbit.ai -->')):
        return 'CodeRabbit walkthrough/status notice; use submitted reviews and inline threads for findings.'
    if (source['kind'] == 'review' and data['id'] in inline_review_ids
            and '**Actionable comments posted:' in body
            and '<!-- This is an auto-generated comment by CodeRabbit for review status -->' in body
            and not any(label in body.lower() for label in ('outside diff', 'additional comments', 'nitpick'))):
        return 'CodeRabbit review summary; assess its linked inline threads individually.'
    return None


def classify(entries):
    review_ids = {c['pullRequestReview']['databaseId']
                  for e in entries if e['kind'] == 'thread'
                  for c in e['data']['comments'] if c.get('pullRequestReview')}
    for source in entries:
        reason = context_reason(source, review_ids)
        if reason:
            source['context_only'] = reason
    return entries
