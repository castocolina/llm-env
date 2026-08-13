import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.dotenv import read_env_file


def test_read_env_file_parses_simple_assignments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OMNI_ROUTER_MASTER_KEY=abc123\nOTHER=value\n")
    assert read_env_file(env_file) == {"OMNI_ROUTER_MASTER_KEY": "abc123", "OTHER": "value"}


def test_read_env_file_skips_blank_lines_and_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("\n# a comment\nKEY=value\n  \n# another\n")
    assert read_env_file(env_file) == {"KEY": "value"}


def test_read_env_file_returns_empty_dict_when_file_missing(tmp_path):
    assert read_env_file(tmp_path / "does-not-exist.env") == {}


def test_read_env_file_strips_surrounding_whitespace(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("  KEY = value with spaces  \n")
    assert read_env_file(env_file) == {"KEY": "value with spaces"}
