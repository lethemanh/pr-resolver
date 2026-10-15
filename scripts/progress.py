"""Plain terminal progress; completion counts come from validated saved results."""
from collections import Counter
import threading
import time
from .review_document import parse, status, validate


def report(message):
    print(message, flush=True)


def fetch_summary(entries):
    kinds = Counter(e['kind'] for e in entries)
    threads = [e['data'] for e in entries if e['kind'] == 'thread']
    comments = {c['id']: c for thread in threads for c in thread['comments']}
    replies = sum(bool(c.get('replyTo')) for c in comments.values())
    report(f"[fetch] Complete: {kinds['comment']} general comments, {kinds['review']} submitted reviews, "
           f"{len(threads)} inline threads, {len(comments)} inline comments ({replies} replies). "
           f"{sum(bool(t.get('isResolved')) for t in threads)} threads already resolved on GitHub; "
           f"{sum(bool(e.get('context_only')) for e in entries)} context-only entries.")


def outcome_summary(entries, selected=None, label='Results'):
    eligible = {key: pair for key, pair in entries.items()
                if (selected is None or key in selected)
                and not pair[0].get('context_only') and not pair[0].get('retired')}
    counts = Counter(status(result) for _, result in eligible.values())
    report(f"[{label}] {len(eligible)} items: {counts['changed']} fixed/changed, "
           f"{counts['answered']} answered, {counts['blocked']} blocked, "
           f"{counts['skipped']} skipped, {counts['pending']} pending. "
           'These are local assessments; no GitHub replies or resolutions are posted.')


class AgentProgress:
    def __init__(self, document, before, selected, interval=2, heartbeat=15):
        self.document, self.before, self.selected = document, before, selected
        self.interval, self.heartbeat = interval, heartbeat
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join()

    def watch(self):
        start = time.monotonic()
        last_report, previous = start, None
        while not self.stop_event.wait(self.interval):
            now = time.monotonic()
            try:
                current = self.document.read_text()
                validate(self.before, current, self.selected, complete=False)
                _, entries = parse(current)
                counts = Counter(status(entries[key][1]) for key in self.selected)
                completed = len(self.selected) - counts['pending']
                signature = tuple(sorted(counts.items()))
            except (OSError, ValueError):
                # Agent may be in the middle of writing; never count partial results.
                if now - last_report >= self.heartbeat:
                    report(f'[agent] Running ({int(now-start)}s); waiting for a valid saved result update.')
                    last_report = now
                continue
            if signature != previous or now - last_report >= self.heartbeat:
                report(f"[agent] {completed}/{len(self.selected)} assessed ({int(now-start)}s): "
                       f"{counts['changed']} fixed/changed, {counts['answered']} answered, "
                       f"{counts['blocked']} blocked, {counts['skipped']} skipped, {counts['pending']} pending.")
                last_report, previous = now, signature
