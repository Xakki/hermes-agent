"""Lifecycle contracts for same-scope discovery flights (no sleeps or timing thresholds)."""

import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from agent import skill_commands as sc
from agent import skill_commands_discovery as discovery


@pytest.fixture(autouse=True)
def isolated_maps(monkeypatch):
    monkeypatch.setattr(sc, "_skill_commands_by_key", {})
    assert discovery._inflight == {}
    yield
    assert discovery._inflight == {}


@pytest.mark.parametrize("fail", [False, True])
def test_waiters_share_result_or_failure_and_next_attempt_recovers(monkeypatch, fail):
    entered, waited, release = threading.Event(), threading.Event(), threading.Event()
    expected = {"/flight": {"name": "flight"}}
    key = (None, "home", "project")
    calls = []

    class ObservedFuture(Future):
        def result(self, timeout=None):
            waited.set()
            return super().result(timeout=timeout)

    def scan():
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(timeout=10)
        if fail:
            raise RuntimeError("fixture scan failed")
        return expected

    monkeypatch.setattr(discovery, "Future", ObservedFuture)
    monkeypatch.setattr(sc, "scan_skill_commands", scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(discovery.coalesced_scan, key, None)
        try:
            assert entered.wait(timeout=10)
            second = pool.submit(discovery.coalesced_scan, key, None)
            assert waited.wait(timeout=10)
        finally:
            release.set()
        for result in (first, second):
            if fail:
                with pytest.raises(RuntimeError, match="fixture scan failed"):
                    result.result(timeout=10)
            else:
                assert result.result(timeout=10) is expected
    assert len(calls) == 1
    monkeypatch.setattr(sc, "scan_skill_commands", lambda: expected)
    assert discovery.coalesced_scan(key, None) is expected


def test_another_scope_finishes_while_first_scope_is_blocked(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    a, b = (None, "a", None), (None, "b", None)

    def scan():
        if threading.current_thread().name.startswith("first-scope"):
            entered.set()
            assert release.wait(timeout=10)
            return {"/a": {"name": "a"}}
        return {"/b": {"name": "b"}}

    monkeypatch.setattr(sc, "scan_skill_commands", scan)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first-scope") as pool:
        first = pool.submit(discovery.coalesced_scan, a, None)
        try:
            assert entered.wait(timeout=10)
            assert set(discovery.coalesced_scan(b, None)) == {"/b"}
        finally:
            release.set()
        assert set(first.result(timeout=10)) == {"/a"}


def test_reentrant_builder_returns_previous_map_without_waiting(monkeypatch):
    key = (None, "home", None)
    previous = {"/previous": {"name": "previous"}}
    sc._skill_commands_by_key[key] = previous
    nested = []

    def scan():
        nested.append(discovery.coalesced_scan(key, previous))
        return {"/fresh": {"name": "fresh"}}

    monkeypatch.setattr(sc, "scan_skill_commands", scan)
    assert set(discovery.coalesced_scan(key, previous)) == {"/fresh"}
    assert nested == [previous]
    sc._skill_commands_by_key.clear()
    assert set(discovery.coalesced_scan(key, None)) == {"/fresh"}
    assert nested[-1] == {}


def test_wait_timeout_does_not_cancel_or_replace_builder(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    key = (None, "home", None)
    calls = []

    def scan():
        calls.append(1)
        entered.set()
        assert release.wait(timeout=10)
        return {"/fresh": {"name": "fresh"}}

    monkeypatch.setattr(discovery, "_WAIT_SECONDS", 0)
    monkeypatch.setattr(sc, "scan_skill_commands", scan)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(discovery.coalesced_scan, key, None)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(TimeoutError):
                discovery.coalesced_scan(key, None)
        finally:
            release.set()
        assert set(first.result(timeout=10)) == {"/fresh"}
    assert len(calls) == 1
