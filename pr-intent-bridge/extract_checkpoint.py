#!/usr/bin/env python3
"""
Extract checkpoint data for a given commit SHA from the entire/checkpoints/v1 branch.

Reads checkpoint metadata.json and prompt.txt using git commands without checking out
the checkpoint branch. Matches checkpoints to commits by searching checkpoint branch
commits for the target commit SHA in their messages.
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
    # Paths are like "ab/c123def456/metadata.json" or "ab/c123def456/0/prompt.txt"
    # Extract unique checkpoint IDs (first two path components)
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


def parse_checkpoint_metadata(repo_path: str, checkpoint_id: str) -> CheckpointMetadata:
    """Read and parse checkpoint-level metadata.json."""
    path = f"{checkpoint_path(checkpoint_id)}/metadata.json"
    raw = read_blob(repo_path, path)
    data = json.loads(raw)
    return CheckpointMetadata(
        cli_version=data.get("cli_version", ""),
        checkpoint_id=data.get("checkpoint_id", ""),
        strategy=data.get("strategy", ""),
        branch=data.get("branch", ""),
        checkpoints_count=data.get("checkpoints_count", 0),
        files_touched=data.get("files_touched", []),
        sessions=[
            SessionRef(
                metadata=s.get("metadata", ""),
                transcript=s.get("transcript", ""),
                context=s.get("context", ""),
                content_hash=s.get("content_hash", ""),
                prompt=s.get("prompt", ""),
            )
            for s in data.get("sessions", [])
        ],
        token_usage=TokenUsage(**data.get("token_usage", {})),
    )


def parse_session_metadata(repo_path: str, checkpoint_id: str, session_index: int) -> SessionMetadata:
    """Read and parse session-level metadata.json."""
    path = f"{checkpoint_path(checkpoint_id)}/{session_index}/metadata.json"
    raw = read_blob(repo_path, path)
    data = json.loads(raw)
    return SessionMetadata(
        cli_version=data.get("cli_version", ""),
        checkpoint_id=data.get("checkpoint_id", ""),
        session_id=data.get("session_id", ""),
        strategy=data.get("strategy", ""),
        created_at=data.get("created_at", ""),
        branch=data.get("branch", ""),
        agent=data.get("agent", ""),
        checkpoints_count=data.get("checkpoints_count", 0),
        files_touched=data.get("files_touched", []),
        token_usage=TokenUsage(**data.get("token_usage", {})),
        initial_attribution=data.get("initial_attribution", {}),
        transcript_path=data.get("transcript_path", ""),
    )


def read_prompt(repo_path: str, checkpoint_id: str, session_index: int) -> str:
    """Read prompt.txt for a session."""
    path = f"{checkpoint_path(checkpoint_id)}/{session_index}/prompt.txt"
    try:
        return read_blob(repo_path, path)
    except RuntimeError:
        return ""


def find_checkpoint_for_commit(commit_sha: str, repo_path: str) -> Optional[str]:
    """
    Find the checkpoint ID associated with a commit SHA.

    Strategy: Search the checkpoint branch log for commits that mention the commit SHA.
    Checkpoint branch commits typically include the original commit SHA in their message.
    """
    # Search checkpoint branch log for the commit SHA
    try:
        out = run_git(
            ["log", CHECKPOINT_BRANCH, "--grep", commit_sha, "--oneline", "--all-match"],
            repo_path,
        )
    except RuntimeError:
        out = ""

    if out:
        # The log message should contain the checkpoint ID
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                # Look for 12-char hex in the message
                for part in parts:
                    if len(part) == 12 and all(c in "0123456789abcdef" for c in part.lower()):
                        return part.lower()

    # Fallback: if no direct match, get the latest checkpoint on the branch
    # (assumes checkpoint branch tracks the default branch)
    try:
        out = run_git(["log", CHECKPOINT_BRANCH, "--oneline", "-1"], repo_path)
        if out:
            # Extract checkpoint ID from the latest checkpoint commit message
            for part in out.split():
                if len(part) == 12 and all(c in "0123456789abcdef" for c in part.lower()):
                    return part.lower()
    except RuntimeError:
        pass

    return None


def get_checkpoint_for_commit(commit_sha: str, repo_path: str) -> Optional[dict]:
    """
    Get checkpoint data for a commit SHA.

    Returns a dict with:
    - checkpoint_id
    - prompt (from first session)
    - files_touched (from checkpoint metadata)
    - session_count
    - strategy
    - branch
    Or None if not found.
    """
    checkpoint_id = find_checkpoint_for_commit(commit_sha, repo_path)
    if not checkpoint_id:
        return None

    meta = parse_checkpoint_metadata(repo_path, checkpoint_id)

    # Get prompt from first session (index 0)
    prompt = ""
    session_meta = None
    if meta.sessions:
        prompt = read_prompt(repo_path, checkpoint_id, 0)
        try:
            session_meta = parse_session_metadata(repo_path, checkpoint_id, 0)
        except RuntimeError:
            pass

    return {
        "checkpoint_id": checkpoint_id,
        "prompt": prompt,
        "files_touched": meta.files_touched,
        "session_count": len(meta.sessions),
        "strategy": meta.strategy,
        "branch": meta.branch,
        "cli_version": meta.cli_version,
        "session_id": session_meta.session_id if session_meta else "",
        "agent": session_meta.agent if session_meta else "",
        "created_at": session_meta.created_at if session_meta else "",
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_checkpoint.py <commit_sha> [repo_path]", file=sys.stderr)
        sys.exit(1)

    commit_sha = sys.argv[1]
    repo_path = sys.argv[2] if len(sys.argv) > 2 else "."

    # Validate commit SHA format
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

    # Output for GitHub Action consumption (JSON to stdout)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()