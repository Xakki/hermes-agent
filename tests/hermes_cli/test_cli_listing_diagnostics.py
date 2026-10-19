"""CLI listing failure uses the CLI logger without logging session/exception payloads."""
import logging
from types import SimpleNamespace

from hermes_cli.cli_session_mixin import CLISessionMixin


def test_listing_failure_retains_cli_logger_identity_and_private_diagnostic(monkeypatch, caplog):
    import cli
    from hermes_cli import session_listing

    assert logging.getLogger('cli') is cli.logger

    def broken_listing(*args, **kwargs):
        try:
            raise ValueError('synthetic-private-cause')
        except ValueError as cause:
            raise RuntimeError('synthetic-private-session-payload') from cause

    monkeypatch.setattr(session_listing, 'query_session_listing', broken_listing)
    caller = SimpleNamespace(_session_db=object(), session_id='synthetic-session')
    caplog.set_level(logging.ERROR, logger='cli')
    assert CLISessionMixin._list_recent_sessions(caller) == []
    record = next(row for row in caplog.records if 'listing_failed' in row.getMessage())
    assert record.name == cli.logger.name == 'cli'
    assert record.exc_info is not None
    assert 'RuntimeError' in caplog.text
    assert 'broken_listing' in caplog.text
    assert 'synthetic-private' not in caplog.text
