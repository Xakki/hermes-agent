"""Sensitive exception boundaries retain call sites without payloads or source lines."""
import logging

from agent.api_request_hooks import ApiRequestHooksMixin
from agent.exception_logging import exception_type_info


def test_diagnostic_excludes_messages_chains_source_lines_and_locals(caplog):
    logger = logging.getLogger(__name__)
    try:
        try:
            raise ValueError('synthetic-private-cause')
        except ValueError as cause:
            raise OSError('synthetic-private-payload') from cause
    except OSError as error:
        logger.warning('Boundary failed', exc_info=exception_type_info(error))
    assert 'OSError' in caplog.text
    assert 'test_diagnostic_excludes_messages_chains_source_lines_and_locals' in caplog.text
    assert 'synthetic-private' not in caplog.text
    assert caplog.records[-1].exc_info is not None


def test_identity_callback_failure_keeps_fail_closed_snapshot_and_safe_diagnostic(caplog):
    class Pool:
        def identity_for_entry_id(self, *args, **kwargs):
            raise RuntimeError('synthetic-private-identity')

    hook = ApiRequestHooksMixin()
    hook._credential_pool = Pool()
    assert hook._credential_identity_for_api_request() == {}
    assert 'RuntimeError' in caplog.text
    assert 'identity_for_entry_id' in caplog.text
    assert 'synthetic-private-identity' not in caplog.text


def test_redaction_failure_keeps_fail_closed_value_and_safe_diagnostic(monkeypatch, caplog):
    from agent import redact

    def failed_redaction(*args, **kwargs):
        raise RuntimeError('synthetic-private-redaction')

    monkeypatch.setattr(redact, 'redact_sensitive_text', failed_redaction)
    assert ApiRequestHooksMixin._redact_hook_value('synthetic-private-input') == '<redaction failed>'
    assert 'failed_redaction' in caplog.text
    assert 'synthetic-private' not in caplog.text
