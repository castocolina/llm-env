import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.transcript import classify_transcript


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")


def test_pi_final_text_is_extracted(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(
        transcript,
        [
            {"type": "message_start", "message": {"role": "assistant"}},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ready"}],
                },
            },
        ],
    )
    result = classify_transcript("pi", transcript)
    assert "ready" in result
    assert "message_start" not in result


def test_opencode_final_text_is_extracted(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(
        transcript,
        [
            {"type": "text", "part": {"type": "text", "messageID": "m1", "text": "rea"}},
            {"type": "text", "part": {"type": "text", "messageID": "m1", "text": "dy"}},
        ],
    )
    result = classify_transcript("opencode", transcript)
    assert "ready" in result


def test_error_shaped_events_are_kept(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(
        transcript,
        [
            {"type": "tool_call", "tool": "bash"},
            {"type": "tool_error", "message": "command not found"},
        ],
    )
    result = classify_transcript("pi", transcript)
    assert "tool_error" in result
    assert "command not found" in result
    assert "tool_call" not in result


def test_top_level_error_key_is_kept(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(transcript, [{"error": "connection refused"}])
    result = classify_transcript("pi", transcript)
    assert "connection refused" in result


def test_falls_back_to_tail_when_nothing_recognized(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(transcript, [{"type": "ping"}, {"type": "pong"}])
    result = classify_transcript("pi", transcript)
    assert "ping" in result
    assert "pong" in result


def test_empty_transcript_reports_empty(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    assert classify_transcript("pi", transcript) == "(transcript is empty)"


def test_malformed_json_lines_are_skipped_not_raised(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("not json\n" + json.dumps({"error": "real"}) + "\n")
    result = classify_transcript("pi", transcript)
    assert "real" in result


def test_unsupported_client_raises():
    with pytest.raises(ValueError):
        classify_transcript("unsupported", Path("/dev/null"))
