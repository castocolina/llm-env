"""Classify a JSONL agent transcript into a compact, relevant excerpt.

Keeps the final assistant text and any event that looks error-shaped, and
drops routine framing (tool calls, reasoning deltas, message-start markers).
If nothing recognizable is found, falls back to the transcript's last few
lines instead of nothing, so an unrecognized failure shape still surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

FALLBACK_TAIL_LINES = 5
SUPPORTED_CLIENTS = ("pi", "opencode")


def _parse_lines(transcript_path: Path) -> list[tuple[str, dict | None]]:
    text = transcript_path.read_text(encoding="utf-8", errors="replace")
    parsed: list[tuple[str, dict | None]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            parsed.append((stripped, None))
            continue
        parsed.append((stripped, record if isinstance(record, dict) else None))
    return parsed


def _is_error_shaped(record: dict) -> bool:
    if "error" in record:
        return True
    record_type = record.get("type")
    return isinstance(record_type, str) and "error" in record_type.lower()


def _extract_final_text_pi(lines: list[tuple[str, dict | None]]) -> str:
    text = ""
    for _, record in lines:
        if record is None:
            continue
        if record.get("type") != "message_end":
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return text


def _extract_final_text_opencode(lines: list[tuple[str, dict | None]]) -> str:
    message_id: str | None = None
    text = ""
    for _, record in lines:
        if record is None or record.get("type") != "text":
            continue
        part = record.get("part")
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        if part.get("messageID") != message_id:
            message_id = part.get("messageID")
            text = ""
        text += part.get("text", "")
    return text


_FINAL_TEXT_EXTRACTORS = {
    "pi": _extract_final_text_pi,
    "opencode": _extract_final_text_opencode,
}


def classify_transcript(client: str, transcript_path: Path) -> str:
    if client not in SUPPORTED_CLIENTS:
        raise ValueError(f"unsupported client: {client!r}; expected one of {SUPPORTED_CLIENTS}")

    lines = _parse_lines(Path(transcript_path))
    if not lines:
        return "(transcript is empty)"

    final_text = _FINAL_TEXT_EXTRACTORS[client](lines)
    error_lines = [raw for raw, record in lines if record is not None and _is_error_shaped(record)]

    sections: list[str] = []
    if final_text:
        sections.append(f"Final assistant text:\n{final_text}")
    if error_lines:
        sections.append("Error-shaped events:\n" + "\n".join(error_lines))
    if not sections:
        tail = [raw for raw, _ in lines][-FALLBACK_TAIL_LINES:]
        sections.append(
            "No recognized error event; last lines of transcript:\n" + "\n".join(tail)
        )

    return "\n\n".join(sections)
