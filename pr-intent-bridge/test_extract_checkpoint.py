#!/usr/bin/env python3
"""
Tests for extract_checkpoint.py

Tests both Format A (metadata.json + prompt.txt) and Format B (checkpoint.jsonl)
parsing using mocked git commands.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent))

from extract_checkpoint import (
    detect_format,
    parse_format_a,
    parse_format_b,
    get_checkpoint_for_commit,
    CHECKPOINT_BRANCH,
)


class TestExtractCheckpoint(unittest.TestCase):
    """Test cases for checkpoint extraction."""

    def setUp(self):
        self.test_repo = "/fake/repo"
        self.test_sha = "abcdef123456"
        self.checkpoint_id = "ab1234567890"
        self.cp_path = f"{self.checkpoint_id[:2]}/{self.checkpoint_id[2:]}"

    def _mock_run_git(self, side_effect_map):
        """Create a mock for run_git that returns values from side_effect_map."""
        def mock_run_git(cmd, repo_path):
            key = " ".join(cmd)
            if key in side_effect_map:
                result = side_effect_map[key]
                if isinstance(result, Exception):
                    raise result
                return result
            raise RuntimeError(f"Unexpected git command: {key}")
        return mock_run_git

    @patch("extract_checkpoint.run_git")
    def test_format_a_valid(self, mock_run_git):
        """Test Format A: valid metadata.json and prompt.txt parse correctly."""
        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": json.dumps({
                "cli_version": "1.0.0",
                "checkpoint_id": self.checkpoint_id,
                "strategy": "test",
                "branch": "main",
                "checkpoints_count": 1,
                "files_touched": ["file1.py", "file2.py"],
                "sessions": [{"metadata": "", "transcript": "", "context": "", "content_hash": "", "prompt": "test prompt"}],
                "token_usage": {"input_tokens": 10, "cache_creation_tokens": 0, "cache_read_tokens": 0, "output_tokens": 5, "api_call_count": 1}
            }),
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/0/prompt.txt": "This is the user prompt for Format A",
        })

        result = get_checkpoint_for_commit(self.test_sha, self.test_repo)

        self.assertIsNotNone(result)
        self.assertEqual(result["format_version"], "a")
        self.assertTrue(result["complete"])
        self.assertEqual(result["prompt_text"], "This is the user prompt for Format A")
        self.assertEqual(result["files_touched"], ["file1.py", "file2.py"])
        self.assertEqual(result["warnings"], [])

    @patch("extract_checkpoint.run_git")
    def test_format_b_valid(self, mock_run_git):
        """Test Format B: valid checkpoint.jsonl parses correctly."""
        jsonl_content = "\n".join([
            '{"event": "session_start", "session_id": "test-123", "agent": "opencode"}',
            '{"event": "prompt", "text": "Add feature to parse JSONL files"}',
            '{"event": "file_edit", "path": "extract_checkpoint.py"}',
            '{"event": "file_edit", "path": "test_extract_checkpoint.py"}',
            '{"event": "session_end", "session_id": "test-123"}',
        ])

        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": RuntimeError("not found"),
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/checkpoint.jsonl": jsonl_content,
        })

        result = get_checkpoint_for_commit(self.test_sha, self.test_repo)

        self.assertIsNotNone(result)
        self.assertEqual(result["format_version"], "b")
        self.assertTrue(result["complete"])
        self.assertEqual(result["prompt_text"], "Add feature to parse JSONL files")
        self.assertEqual(result["files_touched"], ["extract_checkpoint.py", "test_extract_checkpoint.py"])
        self.assertEqual(result["warnings"], [])

    @patch("extract_checkpoint.run_git")
    def test_format_b_unknown_event(self, mock_run_git):
        """Test Format B: unknown event types are skipped, not crashed on."""
        jsonl_content = "\n".join([
            '{"event": "session_start", "session_id": "test-123"}',
            '{"event": "prompt", "text": "Test prompt"}',
            '{"event": "unknown_event_type", "data": "should be skipped"}',
            '{"event": "custom_tool_x", "payload": "also skipped"}',
            '{"event": "file_edit", "path": "some_file.py"}',
            '{"event": "session_end", "session_id": "test-123"}',
        ])

        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": RuntimeError("not found"),
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/checkpoint.jsonl": jsonl_content,
        })

        result = get_checkpoint_for_commit(self.test_sha, self.test_repo)

        self.assertIsNotNone(result)
        self.assertEqual(result["format_version"], "b")
        self.assertTrue(result["complete"])
        self.assertEqual(result["prompt_text"], "Test prompt")
        self.assertEqual(result["files_touched"], ["some_file.py"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertTrue(any("unknown_event_type" in w for w in result["warnings"]))
        self.assertTrue(any("custom_tool_x" in w for w in result["warnings"]))

    @patch("extract_checkpoint.run_git")
    def test_format_b_incomplete(self, mock_run_git):
        """Test Format B: missing session_end returns complete=False with partial data."""
        jsonl_content = "\n".join([
            '{"event": "session_start", "session_id": "test-123"}',
            '{"event": "prompt", "text": "Incomplete checkpoint"}',
            '{"event": "file_edit", "path": "partial.py"}',
            # Missing session_end
        ])

        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": RuntimeError("not found"),
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/checkpoint.jsonl": jsonl_content,
        })

        result = get_checkpoint_for_commit(self.test_sha, self.test_repo)

        self.assertIsNotNone(result)
        self.assertEqual(result["format_version"], "b")
        self.assertFalse(result["complete"])
        self.assertEqual(result["prompt_text"], "Incomplete checkpoint")
        self.assertEqual(result["files_touched"], ["partial.py"])
        self.assertTrue(any("Missing session_end" in w for w in result["warnings"]))

    @patch("extract_checkpoint.run_git")
    def test_format_a_missing_prompt(self, mock_run_git):
        """Test Format A: missing prompt.txt returns complete=False with warning."""
        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": json.dumps({
                "cli_version": "1.0.0",
                "checkpoint_id": self.checkpoint_id,
                "strategy": "test",
                "branch": "main",
                "checkpoints_count": 1,
                "files_touched": ["file1.py"],
                "sessions": [{"metadata": "", "transcript": "", "context": "", "content_hash": "", "prompt": ""}],
                "token_usage": {"input_tokens": 10, "cache_creation_tokens": 0, "cache_read_tokens": 0, "output_tokens": 5, "api_call_count": 1}
            }),
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/0/prompt.txt": RuntimeError("not found"),
        })

        result = get_checkpoint_for_commit(self.test_sha, self.test_repo)

        self.assertIsNotNone(result)
        self.assertEqual(result["format_version"], "a")
        self.assertFalse(result["complete"])
        self.assertEqual(result["prompt_text"], "")
        self.assertEqual(result["files_touched"], ["file1.py"])
        self.assertTrue(any("prompt.txt not found" in w for w in result["warnings"]))

    @patch("extract_checkpoint.run_git")
    def test_format_a_malformed_metadata(self, mock_run_git):
        """Test Format A: malformed metadata.json returns complete=False with warning."""
        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": "not valid json {",
        })

        result = get_checkpoint_for_commit(self.test_sha, self.test_repo)

        self.assertIsNotNone(result)
        self.assertEqual(result["format_version"], "a")
        self.assertFalse(result["complete"])
        self.assertEqual(result["prompt_text"], "")
        self.assertEqual(result["files_touched"], [])
        self.assertTrue(any("Failed to parse metadata.json" in w for w in result["warnings"]))

    @patch("extract_checkpoint.run_git")
    def test_no_checkpoint_found(self, mock_run_git):
        """Test when no checkpoint is found for the commit."""
        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": "",
            f"log {CHECKPOINT_BRANCH} --oneline -1": "no checkpoint id here",
        })

        result = get_checkpoint_for_commit(self.test_sha, self.test_repo)

        self.assertIsNone(result)

    @patch("extract_checkpoint.run_git")
    def test_detect_format_a(self, mock_run_git):
        """Test detect_format returns 'a' for Format A."""
        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": "{}",
        })

        fmt = detect_format(self.test_sha, self.test_repo)
        self.assertEqual(fmt, "a")

    @patch("extract_checkpoint.run_git")
    def test_detect_format_b(self, mock_run_git):
        """Test detect_format returns 'b' for Format B."""
        mock_run_git.side_effect = self._mock_run_git({
            f"log {CHECKPOINT_BRANCH} --grep {self.test_sha} --oneline --all-match": f"xyz {self.checkpoint_id} checkpoint for {self.test_sha}",
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/metadata.json": RuntimeError("not found"),
            f"show {CHECKPOINT_BRANCH}:{self.cp_path}/checkpoint.jsonl": '{"event": "session_start"}',
        })

        fmt = detect_format(self.test_sha, self.test_repo)
        self.assertEqual(fmt, "b")


if __name__ == "__main__":
    unittest.main()