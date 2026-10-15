# PR resolver

A Python 3.10+ CLI that fetches GitHub PR feedback into a local review document,
then runs a coding agent in your existing checkout. Fetching uses only `gh api`;
it makes no AI calls. There are no automatic GitHub writes.

## Setup

Install Git and GitHub CLI, then authenticate with `gh auth login` (repository read
access is needed for private repositories). Install and authenticate Codex CLI if
using the default runner. See the [Codex non-interactive documentation](https://developers.openai.com/codex/noninteractive).
The Python application has no runtime dependencies. Run from this directory, or
install with `python -m pip install .` for the `pr-resolver` command.

Keep target checkouts outside this project. The target must be a non-bare Git
checkout with a GitHub remote. With multiple remotes, pass `--remote origin`.
GitHub Enterprise hosts are inferred from the remote; authenticate `gh` for that
host and explicitly trust it with `--trusted-host github.company.example`.
Only `github.com` is trusted by default; the check runs before any API request.
Trust only known hosts: Enterprise environment tokens may be sent to the selected
host. SSH aliases must use an actual API hostname in the selected remote URL.

```sh
python resolver.py fetch --repo /path/to/repo --pr 123
# Inspect reviews/<checkout-folder>/RESOLVE_REVIEW.md; edit Result sections if desired.
python resolver.py resolve --repo /path/to/repo --pr 123
# Or perform both phases:
python resolver.py run --repo /path/to/repo --pr 123
```

The checkout HEAD must exactly match the fetched PR head commit before execution.
The CLI also checks the current GitHub PR head to reject stale input. Switch your
checkout yourself when necessary. Existing dirty changes are allowed, recorded,
and identified in the prompt; inspect them before running. No branches are switched
and no changes are discarded by the CLI. The agent is instructed to preserve them.

## Document contract

The default output is inside this PR-resolver installation/project, with a folder
per local checkout folder:

```text
reviews/
  twake-contacts-frontend-folk/
    RESOLVE_REVIEW.md
    runs/
```

The folder name comes from the resolved checkout path, not the GitHub remote.
An existing document for another repository or PR is still protected; use
`--output` when two checkouts share the same folder name. Matching older
host/owner/repository folders are moved automatically, including their logs,
when the new destination does not exist. Target
checkouts do not receive the document or new run logs. These local artifacts are
Git-ignored by PR-resolver. Use `--output reviews/pr-123.md` to choose another path
(relative to PR-resolver); its parent directory must exist. The default repository
folder is created automatically. To keep separate documents for multiple PRs in
one repository, select explicit output paths. An existing document for another PR or an unmanaged
or malformed document is rejected; select a separate output path.

Each stable GitHub entry has a script-owned Source section showing GitHub-style conversations: original Markdown bodies, authors, timestamps,
direct comment links, review state, thread resolution/outdated state, file locations,
and diff context. An agent-owned Result section follows each conversation.
Original API data and stable IDs are stored in hidden encoded metadata for exact
round trips and refresh comparisons. Older JSON-source documents remain readable
by the CLI and are converted on the next fetch. Comment bodies are blockquoted to keep their Markdown contained within the conversation.
Document-control markers are escaped in the display; hidden metadata keeps the original strings. Inline
comments appear only through review threads, including every reply, resolution,
outdated state, original/current locations, and diff context. Submitted reviews
with nonempty bodies appear separately from their inline threads.

Edit only Result sections. Keep exactly one of each field:

```text
Status: pending
Assessment:
Changes:
Validation:
Suggested reply:
```

Final statuses are `changed`, `answered`, `blocked`, and `skipped`; each needs all
four descriptive fields populated (use `none` with a reason where appropriate).
`pending` means unfinished. Resolved threads are visible but excluded by default;
pass `--include-resolved` to assess pending resolved threads. Completed entries
are not selected again unless feedback or the PR head changes. Refreshes preserve
results, reset changed feedback to pending, and retain disappeared entries marked
`retired`. A fetch failure never replaces the previous complete document; successful
writes use a temporary file and atomic replacement. Refresh does not merge concurrent
writers: run only one resolver/editor against a document at a time.

## Agent and recovery

The default adapter runs `codex exec --sandbox workspace-write --add-dir <document-folder> -`, with the prompt
on stdin and the target repository as its working directory. A different runner
can be supplied with `--agent-command 'my-agent --some-option'`; it must accept a
prompt on stdin and honor the same document contract. Arguments are parsed without
a shell. Custom commands are trusted executables, not sandboxed by this program.
Custom runners must allow writes to the document folder as well as the target checkout.
The default Codex adapter adds the document folder automatically.

Agents receive only basic environment variables for paths, home, locale, and
terminal settings. Tokens, loader settings, and shell startup hooks are excluded.
If a runner requires a key, explicitly pass its variable name using
`--agent-env OPENAI_API_KEY` (repeatable). This grants the agent access to that value.
Home-directory authentication remains available: filtering the environment does
not isolate credential files, repository secrets, or configured agent tools.

New run artifacts live next to the review document in `runs/<run-id>/`:
`before.md`, `checkout.json` (HEAD, branch, status, tracked diff), `prompt.md`,
`agent.log`, and `exit.json`. Untracked file names appear in status; their contents
are not backed up. Logs can contain private source and feedback. Review and remove
them according to your retention needs.
New run directories use owner-only permissions (`0700`), and logs and snapshots
use `0600`. Logs are not redacted; keep them private.

On interruption or nonzero agent exit, valid completed results remain and unfinished
items stay pending. On invalid document output, `invalid-after.md` retains the
agent output; original sources are restored and independently valid results are
salvaged. The CLI reports failure. Rerun `resolve` after inspecting the document,
code diff, and log. Do not rely on the review document as a code backup.

The prompt prohibits commits, pushes, posted replies, and thread resolution. The
fetcher only reads APIs; agent compliance with publishing restrictions depends on
the configured runner and its permissions. Review the resulting code diff and
validation before using a later publishing workflow.

## Verification

```sh
python -m unittest discover -s tests -v
```

Saved API fixtures and fake agents cover REST and nested GraphQL pagination,
replies, review bodies, resolved/outdated threads, duplicate prevention, document
refresh, malformed inputs, repository checks, and interrupted runs. No test makes
network calls or invokes a real AI model.

A real-PR acceptance trial completed on 2026-10-15 against
`linagora/twake-contacts-frontend#260`, using its existing external checkout and
`--remote upstream`, at head `9b73601fbc8a98677b39e802dd8ab88ae6eccfe6`.
The document was fetched and inspected before launching Codex. The agent exited 0;
source integrity validation passed for all 10 entries (nine changed, one blocked).
Reviewable edits cover parameterized mobile tests, skeleton grid sizing, top-bar
test fixes, and skeleton regression coverage. The recorded run passed 10 targeted
Jest tests and lint with warnings; full TypeScript checking failed on declaration
outputs and typing errors. Hosted quality gates and browser checks remain
unverified; the unspecified docstring finding is recorded as blocked. No commits,
pushes, replies, or thread resolutions were made.

For another trial, run `fetch`, inspect its document, run `resolve`, then review
`git diff`, each result, and recorded validation.

The PR #260 trial document is now in the per-repository reviews folder. Because
the old file was absent, its completed assessments were recovered from the saved
trial log, retaining its original fetch timestamp and source entries.
Historical trial logs remain in the target Git directory; new runs use `runs/` above.


Submitted reviews now group and link their inline threads using GitHub's review
ID on each inline comment. The local view expands disclosure blocks so findings
inside review details are visible. Original bodies remain unchanged in hidden
source metadata. Empty-body submitted reviews are also retained as thread parents.

Recognized CodeRabbit walkthrough/rate-limit notices are retained as context and
excluded from agent tasks. CodeRabbit review-status summaries that repeat linked
inline findings are also context; the inline threads are assessed individually.
Summaries containing additional/outside-diff/nitpick sections remain eligible, as
do human comments and unrecognized bot messages. Context classification is
conservative and deterministic, not an AI judgement of arbitrary comments.
Existing results on context entries are retained as history. Adding review links
or changing display classification alone does not reset completed results.


The Python fetcher independently reads `/pulls/{pr}/comments` through all REST
pages and verifies that every inline comment ID, full body, and submitted-review
association appears in the GraphQL threads. Missing or inconsistent feedback
aborts the fetch before document replacement; retry if feedback changed during
fetching. This is separate from document rendering and context classification.
Fetcher-only regression tests use saved PR #260 responses and assert the four
inline IDs under review `5529421981`, plus missing-comment and body-change failures.


If launching the agent reports `No such file or directory: codex`, update to this
version of the resolver: it checks PATH first, then supported macOS Codex/ChatGPT
desktop app locations. You can explicitly set the executable (a path, not command
arguments) with `PR_RESOLVER_CODEX=/absolute/path/to/codex`. An explicit
`--agent-command` takes precedence. Missing executables now produce a recovery
message before a run is created. If the OS fails to start an executable after that
check, the run records exit status 127 and the launch error in its log.
After fixing discovery, rerun `resolve`; there is no need to fetch again.

Set fallback executable locations directly with `PR_RESOLVER_CODEX_PATHS`:

```sh
export PR_RESOLVER_CODEX_PATHS="$HOME/tools/codex:/opt/codex/bin/codex"
```

Separate paths with `:` on macOS/Linux or `;` on Windows. Paths must be absolute
or start with `~/`; entries are checked in order. Spaces need no extra quoting
inside the variable. An empty value disables fallback discovery; leaving the
variable unset uses the standard macOS app locations. A custom list replaces
those defaults. Empty entries within a nonempty list are rejected.
Discovery precedence is `--agent-command`, `PR_RESOLVER_CODEX`, `codex` on PATH,
then configured fallback paths. No JSON configuration file is needed.


## Terminal progress

All commands print checkout validation and fetch stages as they happen. Each REST
page reports its record count; GraphQL reports thread pages and additional reply
pages. The fetch summary separates general comments, submitted reviews, inline
threads, inline comments/replies, already-resolved GitHub threads, and context-only
entries. These categories are not interchangeable task counts.

Before launching the agent, the CLI shows how many pending items were selected.
During execution it checks saved Result sections every two seconds and reports
changed counts, with a heartbeat at least every 15 seconds while running. Partial
or invalid writes are not counted. Progress is based on saved assessments, so it
can remain at zero while the agent reads code or runs tests. The full agent output
remains available in the printed log path.

After execution, a summary reports fixed/changed, answered, blocked, skipped, and
pending items for this run's selection, along with the process exit status. On
invalid output, the summary uses recovered results. When nothing is selected,
the CLI prints the document's existing outcomes. Blocked/skipped are assessed
items but are not reported as fixes. These are local results; GitHub threads are
never resolved and suggested replies are never posted automatically.
