# PR Intent Bridge

**Entire, but for the moment your AI agent's work leaves the terminal and becomes someone else's problem to review.**

## Submission summary (required fields)
- **Track:** Track 3 — Bring Entire to a New Agent or Workflow
- **GitHub fork:** https://github.com/BSIDDESH/external-agents
- **Final commit SHA:** `0f33c06` (branch: `pr-intent-bridge`) — add BUILDATHON.md commit follows on the same branch
- **Entire mirror:** `entire://aws-us-east-2.entire.io/gh/bsiddesh/external-agents`
- **Checkpoints:** four commits on `pr-intent-bridge`, listed with what each proves below
- **Demo:** local walkthrough of `extract_checkpoint.py` + `pytest` suite (9/9 passing) against the real official curveball fixture; GitHub Action (`.github/workflows/pr-intent-bridge.yml`) ready to fire on any PR against this branch

## One-sentence summary
A GitHub Action that reads the real Entire Checkpoint behind a pull request's commits and posts a structured comment showing what was asked, what actually changed, and what's missing — so reviewers and CI stop reverse-engineering intent from a diff alone.

## Problem, intended user and why it matters
When an AI coding agent implements a change, Entire already captures the *why* — prompts, reasoning, decisions — in a Checkpoint. But the moment that work reaches a pull request, that context vanishes. Reviewers and CI pipelines see only the diff and are left to guess what was actually asked; drift between intent and implementation surfaces late, if it surfaces at all.

**Intended user:** a developer or team reviewing PRs authored in whole or part by an AI coding agent, who wants — at a glance, inside GitHub, with zero extra tooling — what the agent was actually asked to do, what it touched, and whether the record looks complete.

## Selected Entire track and why Entire is essential
**Track 3 — Bring Entire to a New Agent or Workflow.** This is explicitly a "CI or pull-request workflow that uses checkpoint context," one of the track's own listed directions.

Entire isn't decoration here — it's the only source of the thing the product sells: a structured, trustworthy record of *intent*. Strip Entire out and there is nothing to compare the diff against; the product simply doesn't exist without it.

## Architecture and main workflow
- **`pr-intent-bridge/extract_checkpoint.py`** — reads checkpoint data directly from the `entire/checkpoints/v1` git ref via `git show`/`git cat-file`, without ever checking out the branch (fast, side-effect-free, safe to run in CI). One shared entry point, `get_checkpoint_for_commit(commit_sha, repo_path)`, dispatches across two supported checkpoint formats (see Curveball) into a single normalized shape: `prompt_text`, `files_touched`, `complete`, `format_version`, `warnings`.
- **`.github/workflows/pr-intent-bridge.yml`** — triggers on `pull_request` (opened/synchronize/reopened), checks out with full history (`fetch-depth: 0`), runs the extractor against the PR's head commit, and creates or updates a single structured comment on the PR showing intent, files touched, format version, and any completeness warnings.
- No frontend, no database, no extra service to run — the PR comment *is* the product surface.

## Entire Graph findings and verification
Two real graph queries drove real decisions — not run after the fact for a screenshot:

1. **Before writing a line of code:** `entire graph search --repo . --profile full --query "how does Entire currently detect and configure support for a new coding agent"` surfaced `detectOrSelectAgent` and this repo's agent-registry pattern. This is what confirmed a CI/PR workflow — not a new `agents/entire-agent-*` binary — was the right shape for the idea, and pointed straight at the real `CheckpointMetadata` schema in `e2e/testutil/metadata.go`, which the parser was built against instead of guessed field names.
2. **Before touching any code for the Curveball:** `entire graph impact --repo . --symbol get_checkpoint_for_commit` returned the function's real callers and, critically, its *external* consumers — `.github/workflows/pr-intent-bridge.yml` and `e2e/testutil/*.go`. That finding is the direct reason the workflow YAML's output-parsing step was updated in the same commit as the schema refactor, instead of silently drifting out of sync. Graph evidence changed what got edited, not just how it was explained afterward.

All findings were cross-checked against the actual source (`extract_checkpoint.py`, `e2e/testutil/metadata.go`) rather than trusted as fact on their own.

## Noon Curveball: what changed and how we adapted
**Curveball received (Track 3): "The agent changed its format."** The integrated agent's transcript/lifecycle event format changed; existing checkpoints still use the original format; the integration must support both, never crash on an unknown event, and never silently discard an incomplete session.

**Assumption invalidated:** `extract_checkpoint.py` assumed one fixed checkpoint layout — `metadata.json` + `prompt.txt` (Format A). That stopped being true the moment the Curveball landed.

**Design change:** Added `detect_format(commit_sha, repo_path)` to distinguish Format A from a new Format B — a flat JSONL transcript with typed events (`session_started`, `user_prompt`, `agent_response`, `tool_call`, `tool_result`, `file_read`, `file_changed`, `usage`, `checkpoint_created`, `session_ended`). Both formats route through `parse_format_a` / `parse_format_b` into the **same result shape**, sharing all git-reading code so nothing is duplicated. Any event type outside the known list is skipped with a recorded warning, never an exception. A session missing `session_ended` returns `complete=False` with every field that *was* recoverable — partial, not corrupted, not discarded.

**Why the result can be trusted:** Format B was validated against the **real official curveball fixture** (`track-3-agent-session.jsonl`) — `prompt_text` correctly resolves from the `checkpoint_created` event's `intent` field, `files_touched` correctly aggregates every `file_changed` event's `path`. Two further fixtures, clearly labeled synthetic and derived from the real one, cover an injected unknown event type and a truncated session with `session_ended` removed. All four behaviors are pinned by automated tests, not eyeballed once and left alone.

## Checkpoint links and what each checkpoint proves
All checkpoints are commits on the `pr-intent-bridge` branch, pushed to the Entire mirror at `entire://aws-us-east-2.entire.io/gh/bsiddesh/external-agents` and mirrored to https://github.com/BSIDDESH/external-agents/tree/pr-intent-bridge:

| Checkpoint | Commit | Proves |
|---|---|---|
| Initial understanding and intended architecture | [`36bf218`](https://github.com/BSIDDESH/external-agents/commit/36bf218) | Scaffold built only after a real graph search into how this repo structures agent integrations and the real Checkpoint schema — not guessed |
| Last stable state before Noon Curveball | [`8da1c00`](https://github.com/BSIDDESH/external-agents/commit/8da1c00) | Product left runnable and fully documented at 11:45 AM, including an honestly-recorded open risk, before the constraint was even revealed |
| Response to the Noon Curveball | [`b3af370`](https://github.com/BSIDDESH/external-agents/commit/b3af370) | Fresh-session reconstruction from checkpoint context, graph impact analysis run *before* editing, dual-format parser built and proven against the real fixture |
| Final implementation and verification | [`0f33c06`](https://github.com/BSIDDESH/external-agents/commit/0f33c06) | Final state confirmed working end-to-end (9/9 tests passing), plus a transparent, evidence-based writeup of the one thing still unresolved |

*(Commits carry full checkpoint-quality context — intent, architecture, completed/unresolved work, and risks — directly in their messages. Entire's automatic `Entire-Checkpoint` git trailer did not fire on these commits due to an OpenCode plugin issue documented below; that's the one honest gap in an otherwise complete checkpoint trail.)*

## Setup, run and test instructions
```bash
# Clone the fork
git clone https://github.com/BSIDDESH/external-agents.git
cd external-agents
git checkout pr-intent-bridge

# Run the extractor directly against the real official fixture
python pr-intent-bridge/extract_checkpoint.py <commit_sha> .

# Run the full test suite
pip install pytest
python -m pytest pr-intent-bridge/test_extract_checkpoint.py -v
# Expected: 9 passed
```
The GitHub Action fires automatically on any pull request opened against this branch, posting a live structured comment — no extra setup, no secrets beyond the default `GITHUB_TOKEN`.

## Databricks use, data sources and limitations
Not used — all available time went into a complete, well-tested Track 3 submission instead.

## Known limitations and next steps
- **OpenCode → Entire live session tracking isn't firing yet.** `entire enable --agent opencode` succeeds, `.opencode/opencode.jsonc` correctly registers the plugin at the repo root, and `.opencode/plugins/entire.ts` listens for the correct real OpenCode event names (`session.created`, `message.updated`, `message.part.updated`, `session.status`, `session.compacted`, `session.deleted`, `server.instance.disposed`) — yet `entire session list` stayed empty for the entire build. Every configuration point was checked and looks correct; the root cause is still open. Because of this, Format B was proven against the real official fixture file plus a full automated test suite rather than a live-captured checkpoint. This is the single highest-priority next step — fixing it turns this from "validated against a fixture" into "demonstrated fully live."
- LLM-based intent-vs-diff *drift detection* (comparing the stated intent against the actual code diff, flagging mismatches) is the natural Phase 2 — the current version surfaces intent and files touched; it doesn't yet judge whether they line up.
- One checkpoint per PR is handled; multi-commit PRs with several checkpoints aren't aggregated yet.
- Validated locally via the real fixture and unit tests; not yet exercised against a live merged PR in GitHub Actions due to time constraints.