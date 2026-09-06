# PR Intent Bridge

A GitHub Action that triggers on PR open/update, reads the real Entire Checkpoint(s) tied to the PR's commits, and posts a structured PR comment summarizing:

1. **What was asked** — the user prompt/intent from the checkpoint
2. **What actually changed** — files touched, diff summary from the checkpoint
3. **Any gaps between intent and diff** — discrepancies between the stated goal and actual changes

## How it works

- Triggered on `pull_request` events (opened, synchronize)
- Checks out the repo with full history (`fetch-depth: 0`) to access the `entire/checkpoints/v1` branch
- Uses `extract_checkpoint.py` to read checkpoint metadata and prompt data directly from the checkpoint branch using `git show` / `git cat-file` (no checkout needed)
- Finds checkpoint(s) associated with the PR's head commit (`github.event.pull_request.head.sha`)
- Outputs structured data for PR comment posting

## Checkpoint data source

Entire stores checkpoint data on the `entire/checkpoints/v1` git branch. Each checkpoint has:

```
<checkpoint_id[:2]>/<checkpoint_id[2:]>/
├── metadata.json          # CheckpointMetadata (cli_version, strategy, files_touched, sessions[])
├── 0/
│   ├── metadata.json      # SessionMetadata (session_id, agent, created_at, files_touched)
│   ├── prompt.txt         # User prompt / intent
│   ├── full.jsonl         # Full transcript
│   └── content_hash.txt   # Content hash
```

## Usage

The workflow runs automatically on PR events. To test locally:

```bash
cd pr-intent-bridge
python extract_checkpoint.py <commit_sha> <repo_path>
```

## Output Schema

`extract_checkpoint.py` outputs JSON with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `prompt_text` | string | The user prompt/intent text extracted from the checkpoint |
| `files_touched` | array[string] | List of file paths modified in the checkpoint |
| `complete` | boolean | Whether the checkpoint data is complete (has session_start and session_end for Format B, or metadata.json + prompt.txt for Format A) |
| `format_version` | string | `"a"` for legacy metadata.json+prompt.txt format, `"b"` for new checkpoint.jsonl event stream format |
| `warnings` | array[string] | Non-fatal warnings (missing data, unknown event types skipped, etc.) |

**Note:** This schema changed from the previous version which used keys `prompt`, `checkpoint_id`, `session_count`, `strategy`, `branch`, `cli_version`, `session_id`, `agent`, `created_at`. The new schema is format-agnostic and focuses on the essential data needed for PR intent analysis.

## TODO (Track 3 follow-ups)

- [ ] Post structured comment to PR using `gh api` or GitHub REST API
- [ ] Integrate Claude API (or other LLM) to summarize intent vs. diff gaps
- [ ] Handle multiple checkpoints per PR (iterate all commits in PR range)
- [ ] Add configuration for checkpoint branch name (default: `entire/checkpoints/v1`)
- [ ] Add fallback for PRs without checkpoints (e.g., external contributions)