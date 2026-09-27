"""Behavioral coverage for check_fn log levels in tools/registry.py.

Contract: a check_fn that returns False (tool genuinely unavailable here) logs
at INFO; a check_fn that RAISES keeps its WARNING (crash diagnosability); a
failure inside the grace window of a last success keeps its WARNING (transient
flake keeping tools available is worth surfacing).
"""

import logging

import pytest

import tools.registry as reg


@pytest.fixture(autouse=True)
def _clean_check_fn_caches():
    """Isolate the module-level check_fn caches per test."""
    reg.invalidate_check_fn_cache()
    yield
    reg.invalidate_check_fn_cache()


def test_check_fn_returned_false_logs_at_info(caplog):
    """The 'returned False; dependent tools will be unavailable' notice is INFO."""
    def unavailable_check():
        return False

    with caplog.at_level(logging.INFO, logger="tools.registry"):
        assert reg._check_fn_cached(unavailable_check) is False

    records = [r for r in caplog.records
               if "dependent tools will be unavailable this turn" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert not any(r.levelno >= logging.WARNING for r in records)


def test_check_fn_raised_stays_warning(caplog):
    """A crashing check_fn keeps its WARNING so silent tool loss stays diagnosable."""
    def crashing_check():
        raise RuntimeError("probe exploded")

    with caplog.at_level(logging.INFO, logger="tools.registry"):
        assert reg._check_fn_cached(crashing_check) is False

    records = [r for r in caplog.records
               if "dependent tools will be unavailable this turn" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING


def test_grace_window_transient_failure_stays_warning(caplog, monkeypatch):
    """A failure within the grace window of a last success is a flake: stays WARNING."""
    clock = {"now": 5000.0}
    monkeypatch.setattr(reg.time, "monotonic", lambda: clock["now"])
    state = {"ok": True}

    def flaky_check():
        return state["ok"]

    with caplog.at_level(logging.INFO, logger="tools.registry"):
        # Seed last-good with a success, then expire the TTL but stay inside the grace window.
        assert reg._check_fn_cached(flaky_check) is True
        clock["now"] += reg._CHECK_FN_TTL_SECONDS + 1
        state["ok"] = False
        assert reg._check_fn_cached(flaky_check) is True  # grace keeps tools available

    records = [r for r in caplog.records if "treating as transient" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
