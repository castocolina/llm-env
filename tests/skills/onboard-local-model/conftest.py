import os
import shutil
import tempfile
from pathlib import Path

import pytest
from skill_helpers import ROOT, scratch


class _TempInRepo(pytest.UsageError, pytest.Collector.CollectError):  # pyright: ignore[reportGeneralTypeIssues]  # @final is typing-only
    """UsageError when this conftest loads at startup; CollectError when it loads during collection (`pytest tests/`), so pytest prints the message alone and xdist shows it once."""


def _temp_in_repo(config: pytest.Config) -> Path | None:
    basetemp = config.option.basetemp
    if basetemp and hasattr(config, "workerinput"):
        basetemp = Path(basetemp).parent  # xdist worker: <controller basetemp>/popen-gwN; the same path on every worker
    temp = Path(basetemp or os.environ.get("PYTEST_DEBUG_TEMPROOT") or tempfile.gettempdir()).resolve()
    return temp if temp.is_relative_to(ROOT) else None


def pytest_configure(config: pytest.Config) -> None:
    """Fail fast when pytest's temp dir is inside the repo: tests use tmp_path as their 'outside the repo' case."""
    if temp := _temp_in_repo(config):
        raise _TempInRepo(
            f"pytest's temp dir {temp} (from --basetemp or TMPDIR) is inside the repo: these tests need it outside "
            "(they use paths under tmp_path as their 'outside the repo tmp' and 'not a git tree' cases); "
            "unset TMPDIR / drop --basetemp, or point them outside the repo"
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """An xdist worker runs the suite despite a collection error: drop every test so the run ends at that error."""
    if _temp_in_repo(config):
        items.clear()


@pytest.fixture(autouse=True)
def _scout_out_cleanup(tmp_path: Path):
    yield
    shutil.rmtree(scratch(tmp_path), ignore_errors=True)
