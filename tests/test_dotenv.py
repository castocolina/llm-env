import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.dotenv import read_env_file


def test_read_env_file_parses_simple_assignments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OMNI_ROUTER_MASTER_KEY=abc123\nOTHER=value\n")
    assert read_env_file(env_file) == {"OMNI_ROUTER_MASTER_KEY": "abc123", "OTHER": "value"}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(
            "\n# a comment\nKEY=value\n  \n# another\n", {"KEY": "value"},
            id="read_env_file_skips_blank_lines_and_comments",
        ),
        pytest.param(
            "  KEY = value with spaces  \n", {"KEY": "value with spaces"},
            id="read_env_file_strips_surrounding_whitespace",
        ),
    ],
)
def test_read_env_file_normalizes_lines(tmp_path, content, expected):
    env_file = tmp_path / ".env"
    env_file.write_text(content)
    assert read_env_file(env_file) == expected


def test_read_env_file_returns_empty_dict_when_file_missing(tmp_path):
    assert read_env_file(tmp_path / "does-not-exist.env") == {}
