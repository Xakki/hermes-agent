"""Shared output cleanup for the JSON-RPC protocol fixtures."""
import sys

import pytest

_original_stdout = sys.stdout


@pytest.fixture(autouse=True)
def _restore_stdout():
    yield
    sys.stdout = _original_stdout
