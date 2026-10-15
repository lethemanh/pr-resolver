# PR Resolver

Turn GitHub pull request feedback into reviewable local code changes.

PR Resolver collects comments and review threads into `RESOLVE_REVIEW.md`, then
runs a coding agent against your local checkout. The agent assesses each selected
item, makes appropriate changes, runs checks, and records its reasoning and a
suggested reply. You review the code and results before publishing anything.

## How it works

```text
GitHub PR feedback
       │
       ▼ fetch
RESOLVE_REVIEW.md ── inspect feedback and existing results
       │
       ▼ resolve
Local code changes + updated Result sections + run logs
       │
       ▼ your review
Commit, push, and reply through your usual workflow
```

| Command | What it does | Runs an AI agent? |
| --- | --- | --- |
| `fetch` | Reads GitHub feedback and creates or refreshes the review document. | No |
| `resolve` | Checks the PR head and runs the agent for eligible pending entries in an existing document. | Yes, if entries are selected |
| `run` | Runs `fetch`, then `resolve`, without pausing for inspection. | Yes, if entries are selected |

The fetcher uses read-only GitHub APIs. The agent is instructed to leave changes
local: no commits, pushes, posted replies, or resolved GitHub threads. Enforcing
those instructions depends on the runner and its permissions.

## Setup

You need:

- Python 3.10 or newer. There are no Python runtime dependencies.
- Git and a local checkout of the repository you want to work on.
- GitHub CLI (`gh`), authenticated with access to the repository.
- An authenticated Codex CLI, or a custom agent that accepts a prompt on stdin.

Authenticate GitHub CLI:

```sh
gh auth login
```

Run commands from this PR Resolver directory using `python3 resolver.py`.
Alternatively, install the CLI:

```sh
python3 -m pip install .
pr-resolver --help
```

Keep the target checkout separate from this project. It must have a GitHub remote.
If it has several remotes, select one with `--remote origin` (or the appropriate
remote name).

## First run

The examples use PR `123` in a checkout at `/path/to/my-app`. Replace both values
with your own.

### 1. Fetch the feedback

```sh
python3 resolver.py fetch --repo /path/to/my-app --pr 123
```

Open `reviews/my-app/RESOLVE_REVIEW.md` inside this PR Resolver project. It contains
general comments, relevant submitted reviews, and unresolved inline conversations
with their replies, file locations, and diff context. Resolved thread bodies are
skipped by default; reviews whose threads are all resolved are omitted. General
comments and standalone review bodies have no GitHub resolution flag and remain
included. Add `--include-resolved` to fetch resolved conversations too.

### 2. Inspect the document and checkout

Read the feedback and check your existing local changes. Before starting the
agent, the checkout HEAD must exactly match the fetched PR head commit. Switch
the checkout yourself if needed; PR Resolver does not switch branches.

Dirty changes are allowed and recorded. The agent is instructed to preserve them,
but you should review them and keep any backups you need.

### 3. Resolve pending feedback

```sh
python3 resolver.py resolve --repo /path/to/my-app --pr 123
```

The CLI checks the current GitHub PR head again, selects pending entries, and
starts the agent in your checkout. The agent edits code and the selected Result
sections. Progress reports reflect saved results, so the count may stay unchanged
while the agent reads code or runs tests. The printed log path contains full output.

For later runs when you want fetching and execution together:

```sh
python3 resolver.py run --repo /path/to/my-app --pr 123
```

### 4. Review the outcome

Inspect the updated Result sections, recorded validation, and local code diff:

```sh
git -C /path/to/my-app diff
```

Run any additional checks you need. Commit, push, post replies, and resolve GitHub
threads through your normal workflow. Suggested replies in the document are drafts.

## Understanding the review document

Each entry has two sections:

- **Source:** feedback and metadata managed by the script. Leave it unchanged,
  including hidden metadata and entry markers.
- **Result:** the assessment managed by the agent. You may edit this section.

Results use bold labels separated by blank lines:

```markdown
**Status:** changed

**Assessment:** The reported issue is valid because ...

**Changes:** Updated ...

**Validation:** Ran ...; remaining limitations are ...

**Suggested reply:** Fixed by ...
```

| Status | Meaning |
| --- | --- |
| `pending` | Still needs assessment. |
| `changed` | Code was changed to address the feedback. |
| `answered` | Addressed with an explanation. |
| `blocked` | Needs information, permissions, or validation before proceeding. |
| `skipped` | Assessed and intentionally left without further action. |

Keep exactly one of each field. Every final status requires all four descriptive
fields; use `none` with a reason where appropriate. A `changed` result records a
local assessment, not confirmation that a hosted quality gate passed.

### What gets selected on the next run?

Only eligible `pending` entries are selected. Completed entries stay completed
unless their feedback or the PR head changes during a fetch.

- Resolved GitHub threads are omitted from default fetches and removed from the
  document on refresh, along with review parents whose threads are all resolved.
  Add `--include-resolved` to fetch them and assess their pending results.
- Feedback that disappears on refresh is retained as retired history and excluded.
- Recognized CodeRabbit walkthroughs, rate-limit notices, and summaries that repeat
  linked inline findings are kept as context. Additional findings and unrecognized
  messages remain eligible.

Fetching preserves existing results, resets changed entries to `pending`, and
verifies selected inline comment bodies and review associations against REST.
Default fetches verify each selected comment individually; `--include-resolved`
uses the full REST inventory. GitHub requires scanning thread statuses to identify
unresolved conversations, but resolved thread bodies and replies are skipped by
default. A failed fetch leaves
the previous complete document intact. Use only one resolver or editor per
document at a time; concurrent edits are not merged.

## Where files are saved

The default layout is relative to the PR Resolver installation/project:

```text
reviews/
  my-app/
    RESOLVE_REVIEW.md
    runs/
      <run-id>/
        before.md
        checkout.json
        prompt.md
        agent.log
        exit.json
```

The folder name comes from the checkout directory, not the GitHub repository name.
Documents and new logs are saved outside the target checkout. `reviews/` is ignored
by this project's Git configuration.

For multiple PRs in one checkout, or checkouts with the same directory name,
choose a separate document with `--output`. Use the same path for subsequent
`fetch` and `resolve` commands:

```sh
python3 resolver.py fetch --repo /path/to/my-app --pr 123 \
  --output /path/to/private-reviews/pr-123.md
```

The output parent directory must already exist. Relative output paths are resolved
against the PR Resolver installation/project directory. An existing document for
another PR, or an unmanaged or malformed document, is rejected.

## Configuration

### Codex executable

Select one executable explicitly:

```sh
export PR_RESOLVER_CODEX="/absolute/path/to/codex"
```

Or configure an ordered list of fallback executables:

```sh
export PR_RESOLVER_CODEX_PATHS="$HOME/tools/codex:/opt/codex/bin/codex"
```

Use `:` between paths on macOS/Linux, or `;` on Windows. Paths must be absolute or
start with `~/`. Spaces within a path are supported. An empty value disables
fallback discovery; an unset variable uses standard macOS Codex/ChatGPT app
locations. Empty entries within a nonempty list are rejected. No JSON file is needed.

Executable selection order:

1. `--agent-command`, if supplied.
2. `PR_RESOLVER_CODEX`, if set to a nonempty value. An invalid override is an error.
3. `codex` found on PATH.
4. Executables in `PR_RESOLVER_CODEX_PATHS`, or the default app locations.

### GitHub Enterprise

Authenticate your host, then explicitly trust it on each command:

```sh
gh auth login --hostname github.company.example
python3 resolver.py fetch --repo /path/to/my-app --pr 123 \
  --trusted-host github.company.example
```

Only `github.com` is trusted by default. The selected remote must contain the
actual API hostname; SSH aliases are not resolved. Trust only known hosts, because
Enterprise environment tokens may be sent to the selected host.

### Custom agents and environment variables

The default runner uses:

```text
codex exec --sandbox workspace-write --add-dir <document-folder> -
```

To use another runner:

```sh
python3 resolver.py resolve --repo /path/to/my-app --pr 123 \
  --agent-command 'my-agent --some-option'
```

Custom runners receive the prompt on stdin and must follow the document contract.
They need write access to both the checkout and document folder. Arguments are
parsed without a shell; PR Resolver does not sandbox custom runners itself.

Agents receive a limited environment containing basic path, home, locale, and
terminal settings. To pass an additional variable, name it explicitly:

```sh
python3 resolver.py resolve --repo /path/to/my-app --pr 123 \
  --agent-env OPENAI_API_KEY
```

`--agent-env` is repeatable and passes values already present in your environment.
It grants the agent access to those values. Filtering does not isolate credentials
stored in your home directory, repository secrets, or configured agent tools.

## Recovery and troubleshooting

| Problem | Next step |
| --- | --- |
| Codex executable not found | Set `PR_RESOLVER_CODEX`, fix PATH, or configure fallback paths. Rerun `resolve`. |
| Untrusted GitHub host | Verify the remote, then pass `--trusted-host HOST` if you trust it. |
| Checkout HEAD does not match the PR | Switch to the correct commit yourself, then rerun. |
| PR head changed | Fetch again, inspect the refreshed document, and make sure the checkout matches. |
| Output belongs to another PR | Choose another `--output` path. |
| Feedback completeness check failed | Retry `fetch`; feedback may have changed while it was being read. |
| Agent interrupted or exited with an error | Inspect the document, code diff, and `agent.log`, then rerun `resolve`. |
| Agent changed protected document content | Inspect `invalid-after.md`; original sources are restored and valid results are recovered where possible. |

Completed valid results can survive a failed run; unfinished entries remain
pending. Invalid document output causes the CLI to report failure. Review recovered
results before continuing.

`checkout.json` records HEAD, branch, status, and the tracked diff. Untracked file
contents are not backed up. The review document and logs are not a code backup.

Logs are not redacted and may contain private code, feedback, or sensitive output.
New run directories use owner-only permissions (`0700`); logs and snapshots use
`0600`. Keep them private and remove them according to your retention needs.

## Development checks

Run the test suite:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover executable configuration, trusted-host checks, agent environment
filtering, artifact permissions, and Result formatting. They use temporary files,
mocked API calls, and a local fake agent; they do not call a real AI model.

List all command options:

```sh
python3 resolver.py --help
```
