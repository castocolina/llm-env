"""tests/conftest.py switches uv and Hugging Face offline for every test and the processes it starts."""
import os

import pytest


@pytest.mark.parametrize("name", ["UV_OFFLINE", "HF_HUB_OFFLINE"])
def test_children_inherit_offline_switches(name):
    assert os.environ[name] == "1"
