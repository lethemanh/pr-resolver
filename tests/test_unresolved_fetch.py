import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.github import GitHub
from scripts.repository import Repository
from scripts.review_document import DEFAULT, parse, refresh, render_document


class UnresolvedFetchTests(unittest.TestCase):
    def fetch(self, include=False):
        client = GitHub(Repository(Path('/tmp'), 'github.com', 'owner', 'repo'))
        calls = []
        comments = {
            key: dict(id='node-' + str(key), databaseId=key, body='feedback',
                      pullRequestReview=dict(databaseId=review, url='https://github.com/owner/repo/pull/1#review'))
            for key, review in ((11, 1), (22, 2))
        }
        def api(endpoint, fields=None):
            calls.append((endpoint, fields))
            if endpoint == 'repos/owner/repo/pulls/1':
                return dict(html_url='https://github.com/owner/repo/pull/1',
                            base=dict(ref='main'), head=dict(ref='branch', sha='abc'))
            if endpoint == 'graphql':
                if 'id' not in fields:
                    cursor = fields.get('after')
                    key, resolved, review = ('closed', True, 1) if cursor is None else ('open', False, 2)
                    return dict(data=dict(repository=dict(pullRequest=dict(reviewThreads=dict(
                        nodes=[dict(id=key, isResolved=resolved, comments=dict(nodes=[dict(pullRequestReview=dict(databaseId=review, url='https://github.com/owner/repo/pull/1#review'))]))],
                        pageInfo=dict(hasNextPage=cursor is None, endCursor='next'))))))
                key = fields['id']
                nodes = [comments[11 if key == 'closed' else 22]]
                return dict(data=dict(node=dict(id=key, isResolved=key == 'closed',
                    comments=dict(nodes=copy.deepcopy(nodes), pageInfo=dict(hasNextPage=False)))))
            key = int(endpoint.rsplit('/', 1)[1])
            return dict(id=key, body='feedback', pull_request_review_id=1 if key == 11 else 2)
        def pages(endpoint):
            if '/issues/' in endpoint:
                return iter([dict(id=9, body='general comment')])
            if endpoint.endswith('/reviews'):
                return iter([dict(id=n, state='COMMENTED', body='review') for n in (1, 2, 3)])
            return iter([dict(id=k, body='feedback', pull_request_review_id=v['pullRequestReview']['databaseId'])
                         for k, v in comments.items()])
        with patch.object(client, 'api', side_effect=api), patch.object(client, 'pages', side_effect=pages):
            meta, entries = client.fetch(1, include_resolved=include)
        return meta, entries, calls

    def test_default_skips_resolved_bodies_and_parent_review(self):
        meta, entries, calls = self.fetch()
        self.assertEqual({e['id'] for e in entries}, {'comment-9', 'review-2', 'review-3', 'thread-open'})
        self.assertEqual(set(meta['excluded_entry_ids']), {'review-1', 'thread-closed'})
        self.assertFalse(any(fields and fields.get('id') == 'closed' for _, fields in calls))
        self.assertFalse(any(endpoint.endswith('/11') for endpoint, _ in calls))
        self.assertTrue(any(endpoint.endswith('/22') for endpoint, _ in calls))

    def test_include_resolved_restores_all_threads(self):
        meta, entries, calls = self.fetch(True)
        self.assertIn('thread-closed', {e['id'] for e in entries})
        self.assertIn('review-1', {e['id'] for e in entries})
        self.assertEqual(meta['excluded_entry_ids'], [])

    def test_refresh_removes_excluded_results(self):
        meta, entries, _ = self.fetch()
        old = dict(meta, version=1, fetched_at='2026-10-16', excluded_entry_ids=[])
        source = dict(id='thread-closed', kind='comment', data=dict(body='old'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'review.md'
            path.write_text(render_document(old, [(source, DEFAULT)]))
            refresh(path, meta, entries)
            self.assertNotIn('thread-closed', parse(path.read_text())[1])

    def test_selected_comment_mismatch_still_fails(self):
        client = GitHub(Repository(Path('/tmp'), 'github.com', 'owner', 'repo'))
        entries = [dict(kind='thread', data=dict(comments=[dict(databaseId=22, body='old', pullRequestReview=dict(databaseId=2))]))]
        with patch.object(client, 'api', return_value=dict(id=22, body='edited', pull_request_review_id=2)):
            with self.assertRaisesRegex(ValueError, 'changed during fetch'):
                client.verify_inline_comments(1, entries, selected_only=True)
