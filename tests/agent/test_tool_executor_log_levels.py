"""Behavioral coverage for observed tool-error log levels in agent/tool_executor.py.

Contract: inside ``_commit_tool_result``, an observed ``search_files`` error logs
at INFO (a failed search is routine exploration, not a fault); every other tool's
observed error keeps its WARNING, and the committed result message is unchanged.
"""

import json
import logging
from types import SimpleNamespace

from agent.tool_executor import _ToolCallRef, _commit_tool_result
from tools.budget_config import BudgetConfig


def _make_agent():
    """Minimal agent double: only the collaborators _commit_tool_result touches."""
    return SimpleNamespace(
        _append_guardrail_observation=lambda name, args, result, *, failed=False, tool_call_id="": result,
        _record_file_mutation_result=lambda *a, **k: None,
        _touch_activity=lambda desc, **kw: None,
        _subdirectory_hints=SimpleNamespace(check_tool_call=lambda name, args: None),
        _tool_result_content_for_active_model=lambda name, result: result,
        _flush_messages_to_session_db=lambda messages: True,
        _tool_guardrails=SimpleNamespace(record_persisted_result=lambda *a, **k: None),
        verbose_logging=False,
        tool_progress_callback=None,
    )


def _commit_error_result(tool_name):
    """Run one observed, errored tool call through _commit_tool_result."""
    agent = _make_agent()
    messages = []
    ref = _ToolCallRef(name=tool_name, args={"pattern": "x"}, task_id="task-1",
                       call_id="call-1", trace=[])
    committed = _commit_tool_result(
        agent, messages, ref, json.dumps({"error": "invalid regex"}),
        budget=BudgetConfig(), tool_duration=0.5, is_error=True, blocked=False,
        effect_disposition=None, observed=True, error_preview=lambda res: res,
    )
    return committed, messages


def test_search_files_observed_error_logs_at_info(caplog):
    """An observed search_files error is logged at INFO, not WARNING."""
    with caplog.at_level(logging.INFO, logger="agent.tool_executor"):
        committed, messages = _commit_error_result("search_files")

    assert committed is not None  # commit path unchanged
    assert [m["role"] for m in messages] == ["tool"]
    records = [r for r in caplog.records if "returned error" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert "search_files" in records[0].getMessage()
    assert not any(r.levelno >= logging.WARNING for r in records)


def test_other_tool_observed_error_stays_warning(caplog):
    """An observed error from any other tool keeps its WARNING."""
    with caplog.at_level(logging.INFO, logger="agent.tool_executor"):
        committed, messages = _commit_error_result("terminal")

    assert committed is not None
    records = [r for r in caplog.records if "returned error" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
