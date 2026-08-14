"""Regression tests for native Review lifecycle."""
from pathlib import Path
import pytest
from hermes_cli import kanban_db as kb

@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / '.hermes'; home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home)); monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    kb.init_db(); return home

def test_worker_handoff_and_passing_review_use_review_not_blocked(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title='x', assignee='worker')
        with kb.write_txn(conn): conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        assert kb.claim_task(conn, tid, claimer='worker')
        run = kb.latest_run(conn, tid)
        assert kb.submit_task_for_review(conn, tid, summary='tests passed', expected_run_id=run.id)
        assert kb.get_task(conn, tid).status == 'review'
        assert kb.claim_review_task(conn, tid, claimer='reviewer')
        review_run = kb.latest_run(conn, tid)
        assert kb.record_review_verdict(conn, tid, verdict='pass', summary='approved', expected_run_id=review_run.id)
        assert kb.get_task(conn, tid).status == 'done'
