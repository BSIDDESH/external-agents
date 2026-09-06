#!/usr/bin/env python3
"""
Extract checkpoint data for a given commit SHA from the entire/checkpoints/v1 branch.

Supports two checkpoint formats:
- Format A (legacy): metadata.json + prompt.txt files
- Format B (new): single checkpoint.jsonl with event stream

Reads checkpoint data using git commands without checking out the checkpoint branch.
Matches checkpoints to commits by searching checkpoint branch commits for the target
commit SHA in their messages.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TokenUsage:
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    api_call_count: int


@dataclass
class SessionRef:
    metadata: str
    transcript: str
    context: str
    content_hash: str
    prompt: str


@dataclass
class CheckpointMetadata:
    cli_version: str
    checkpoint_id: str
    strategy: str
    branch: str
    checkpoints_count: int
    files_touched: list[str]
    sessions: list[SessionRef]
    token_usage: TokenUsage


@dataclass
class SessionMetadata:
    cli_version: str
    checkpoint_id: str
    session_id: str
    strategy: str
    created_at: str
    branch: str
    agent: str
    checkpoints_count: int
    files_touched: list[str]
    token_usage: TokenUsage
    initial_attribution: dict
    transcript_path: str


CHECKPOINT_BRANCH = "entire/checkpoints/v1"


def run_git(cmd: list[str], repo_path: str) -> str:
    """Run a git command and return stdout, raise on error."""
    result = subprocess.run(
        ["git", "-C", repo_path] + cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(cmd)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def checkpoint_path(checkpoint_id: str) -> str:
    """Convert checkpoint ID to its two-level directory path (e.g., ab/c123def456)."""
    return f"{checkpoint_id[:2]}/{checkpoint_id[2:]}"


def list_checkpoint_ids(repo_path: str) -> list[str]:
    """List all checkpoint IDs from the checkpoint branch tree."""
    out = run_git(["ls-tree", "-r", "--name-only", CHECKPOINT_BRANCH], repo_path)
    if not out:
        return []
    ids = set()
    for line in out.splitlines():
        parts = line.split("/")
        if len(parts) >= 2:
            cid = parts[0] + parts[1]
            if len(cid) == 12:
                ids.add(cid)
    return sorted(ids)


def read_blob(repo_path: str, blob_path: str) -> str:
    """Read a blob from the checkpoint branch using git show."""
    return run_git(["show", f"{CHECKPOINT_BRANCH}:{blob_path}"], repo_path)


def try_read_blob(repo_path: str, blob_path: str) -> Optional[str]:
    """Read a blob from the checkpoint branch, return None if not found."""
    try:
        return read_blob(repo_path, blob_path)
    except RuntimeError:
        return None


def find_checkpoint_for_commit(commit_sha: str, repo_path: str) -> Optional[str]:
    """
    Find the checkpoint ID associated with a commit SHA.

    Strategy: Search the checkpoint branch log for commits that mention the commit SHA.
    Checkpoint branch commits typically include the original commit SHA in their message.
    """
    try:
        out = run_git(
            ["log", CHECKPOINT_BRANCH, "--grep", commit_sha, "--oneline", "--all-match"],
            repo_path,
        )
    except RuntimeError:
        out = ""

    if out:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                for part in parts:
                    if len(part) == 12 and all(c in "0123456789abcdef" for c in part.lower()):
                        return part.lower()

    try:
        out = run_git(["log", CHECKPOINT_BRANCH, "--oneline", "-1"], repo_path)
        if out:
            for part in out.split():
                if len(part) == 12 and all(c in "0123456789abcdef" for c in part.lower()):
                    return part.lower()
    except RuntimeError:
        pass

    return None


def detect_format(commit_sha: str, repo_path: str) -> Optional[str]:
    """
    Detect checkpoint format for a commit.

    Returns:
        "a" if Format A (metadata.json + prompt.txt) exists
        "b" if Format B (checkpoint.jsonl) exists
        None if no checkpoint found for commit
    """
    checkpoint_id = find_checkpoint_for_commit(commit_sha, repo_path)
    if not checkpoint_id:
        return None

    cp_path = checkpoint_path(checkpoint_id)

    format_a_metadata = try_read_blob(repo_path, f"{cp_path}/metadata.json")
    if format_a_metadata is not None:
        return "a"

    format_b_jsonl = try_read_blob(repo_path, f"{cp_path}/checkpoint.jsonl")
    if format_b_jsonl is not None:
        return "b"

    return None


def parse_format_a(commit_sha: str, repo_path: str) -> dict:
    """Parse Format A: metadata.json + prompt.txt structure."""
    warnings = []
    checkpoint_id = find_checkpoint_for_commit(commit_sha, repo_path)
    if not checkpoint_id:
        return {
            "prompt_text": "",
            "files_touched": [],
            "complete": False,
            "format_version": "a",
            "warnings": ["No checkpoint found for commit"],
        }

    cp_path = checkpoint_path(checkpoint_id)

    meta_raw = try_read_blob(repo_path, f"{cp_path}/metadata.json")
    if meta_raw is None:
        return {
            "prompt_text": "",
            "files_touched": [],
            "complete": False,
            "format_version": "a",
            "warnings": [f"metadata.json not found for checkpoint {checkpoint_id}"],
        }

    try:
        meta_data = json.loads(meta_raw)
    except json.JSONDecodeError as e:
        return {
            "prompt_text": "",
            "files_touched": [],
            "complete": False,
            "format_version": "a",
            "warnings": [f"Failed to parse metadata.json: {e}"],
        }

    files_touched = meta_data.get("files_touched", [])
    sessions = meta_data.get("sessions", [])

    prompt_text = ""
    if sessions:
        prompt_raw = try_read_blob(repo_path, f"{cp_path}/0/prompt.txt")
        if prompt_raw is None:
            warnings.append("prompt.txt not found for session 0")
        else:
            prompt_text = prompt_raw

    complete = bool(meta_raw and prompt_text)

    return {
        "prompt_text": prompt_text,
        "files_touched": files_touched,
        "complete": complete,
        "format_version": "a",
        "warnings": warnings,
    }


def parse_format_b(commit_sha: str, repo_path: str) -> dict:
    """Parse Format B: checkpoint.jsonl event stream."""
    warnings = []
    checkpoint_id = find_checkpoint_for_commit(commit_sha, repo_path)
    if not checkpoint_id:
        return {
            "prompt_text": "",
            "files_touched": [],
            "complete": False,
            "format_version": "b",
            "warnings": ["No checkpoint found for commit"],
        }

    cp_path = checkpoint_path(checkpoint_id)
    jsonl_raw = try_read_blob(repo_path, f"{cp_path}/checkpoint.jsonl")
    if jsonl_raw is None:
        return {
            "prompt_text": "",
            "files_touched": [],
            "complete": False,
            "format_version": "b",
            "warnings": [f"checkpoint.jsonl not found for checkpoint {checkpoint_id}"],
        }

    prompt_parts = []
    files_touched = set()
    has_session_start = False
    has_session_end = False

    for line_num, line in enumerate(jsonl_raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"Line {line_num}: invalid JSON, skipping")
            continue

        event_type = event.get("event")
        if event_type == "session_start":
            has_session_start = True
        elif event_type == "prompt":
            text = event.get("text", "")
            if text:
                prompt_parts.append(text)
        elif event_type == "file_edit":
            path = event.get("path")
            if path:
                files_touched.add(path)
        elif event_type == "session_end":
            has_session_end = True
        elif event_type == "tool_call":
            pass
        else:
            warnings.append(f"Line {line_num}: unknown event type '{event_type}', skipped")

    prompt_text = "\n".join(prompt_parts)
    complete = has_session_start and has_session_end

    if not has_session_start:
        warnings.append("Missing session_start event")
    if not has_session_end:
        warnings.append("Missing session_end event, checkpoint may be incomplete")

    return {
        "prompt_text": prompt_text,
        "files_touched": sorted(files_touched),
        "complete": complete,
        "format_version": "b",
        "warnings": warnings,
    }


def get_checkpoint_for_commit(commit_sha: str, repo_path: str) -> Optional[dict]:
    """
    Get checkpoint data for a commit SHA.

    Returns a dict with:
    - prompt_text: the user prompt/intent text
    - files_touched: list of file paths modified
    - complete: bool, whether checkpoint data is complete
    - format_version: "a" or "b"
    - warnings: list of warning strings for any issues
    Or None if no checkpoint found.
    """
    fmt = detect_format(commit_sha, repo_path)
    if fmt is None:
        return None

    if fmt == "a":
        return parse_format_a(commit_sha, repo_path)
    else:
        return parse_format_b(commit_sha, repo_path)


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_checkpoint.py <commit_sha> [repo_path]", file=sys.stderr)
        sys.exit(1)

    commit_sha = sys.argv[1]
    repo_path = sys.argv[2] if len(sys.argv) > 2 else "."

    if len(commit_sha) < 7 or not all(c in "0123456789abcdef" for c in commit_sha.lower()):
        print(f"Error: Invalid commit SHA format: {commit_sha}", file=sys.stderr)
        sys.exit(1)

    try:
        result = get_checkpoint_for_commit(commit_sha, repo_path)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if result is None:
        print(f"No checkpoint found for commit {commit_sha}", file=sys.stderr)
        sys.exit(0)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()