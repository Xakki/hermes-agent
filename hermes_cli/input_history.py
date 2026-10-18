"""Project-scoped input recall shared by classic CLI sessions."""
from pathlib import Path
import os

from hermes_constants import get_hermes_home
from tui_gateway.git_probe import input_history_key


def project_history_file(cwd: str) -> Path:
    key = input_history_key(cwd)
    if key is None:
        raise ValueError("Input history requires a project workspace")
    directory = get_hermes_home() / "input-history"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / key


def refresh_input_history(cli) -> None:
    path = project_history_file(os.getcwd())
    if getattr(cli, "_history_file", None) == path:
        return
    cli._history_file = path
    buffer = getattr(cli, "_input_history_buffer", None)
    if buffer is not None:
        from prompt_toolkit.history import FileHistory
        buffer.history = FileHistory(str(path))
        buffer.reset()
