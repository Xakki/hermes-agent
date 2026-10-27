"""Codex route metadata, compressor budgets and context display share one window.

Synthetic catalogs qualify resolver behavior, not a particular account's entitlement.
"""
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from agent import model_metadata as mm
from agent.context_breakdown import compute_session_context_breakdown
from agent.context_compressor import ContextCompressor


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(mm, "_codex_oauth_context_cache", {})
    monkeypatch.setattr(mm, "_codex_oauth_max_context_cache", {})
    monkeypatch.setattr(mm, "save_context_length", Mock())
    monkeypatch.setattr("agent.system_prompt.build_system_prompt_parts", lambda agent: {})

    def install(window, maximum=872_000):
        response = Mock(status_code=200)
        entry = {"slug": "gpt-6.1-sol", "context_window": window}
        if maximum is not None:
            entry["max_context_window"] = maximum
        response.json.return_value = {"models": [entry]}
        get = Mock(return_value=response)
        monkeypatch.setattr(mm.model_metadata_http, "get", get)
        return get

    return install


@pytest.mark.parametrize("advertised,model,pin,expected", [
    (272_000, "gpt-6.1-sol", None, 272_000),
    (500_000, "gpt-6.1-sol", None, 500_000),
    (272_000, "gpt-6.1-sol-900k", None, 872_000),
    (500_000, "gpt-6.1-sol-900k", None, 872_000),
    (272_000, "gpt-6.1-sol", 600_000, 600_000),
])
def test_catalog_window_reaches_compressor_and_display(catalog, advertised, model, pin, expected):
    get = catalog(advertised)
    route: dict[str, Any] = dict(base_url="https://route.example.invalid/codex", api_key='fixture-1',
                 provider="openai-codex", config_context_length=pin)
    detected = mm.get_model_context_length(model, **route)
    comp = ContextCompressor(model=model, threshold_percent=0.75, max_tokens=16_384,
                             quiet_mode=True, **route)
    agent = SimpleNamespace(model=model, context_compressor=comp, tools=[])
    assert detected == expected
    assert comp.context_length == detected
    assert comp.threshold_tokens == int((expected - 16_384) * 0.75)
    assert comp.threshold_tokens + 16_384 < expected
    assert compute_session_context_breakdown(agent, [])["context_max"] == detected
    assert get.call_count == (0 if pin else 1)


@pytest.mark.parametrize("advertised,maximum,expected", [
    (500_000, None, 500_000),
    (500_000, 500_000, 500_000),
    (500_000, 872_000, 872_000),
    (500_000, 1_000_000, 900_000),
    (1_000_000, 2_000_000, 1_000_000),
    (272_000, 128_000, 128_000),
])
def test_explicit_variant_respects_published_ceiling(catalog, advertised, maximum, expected):
    catalog(advertised, maximum)
    route: dict[str, Any] = dict(provider="openai-codex", api_key='fixture-1',
                 base_url="https://route.example.invalid/codex")
    assert mm.get_model_context_length("gpt-6.1-sol-900k", **route) == expected
    assert mm.get_model_context_length("gpt-6.1-sol", **route) == advertised


def test_catalog_cache_is_scoped_to_token_and_route(catalog):
    get = catalog(272_000)
    first: dict[str, Any] = dict(provider="openai-codex", api_key='fixture-2',
                 base_url="https://first.example.invalid/codex")
    assert mm.get_model_context_length("gpt-6.1-sol", **first) == 272_000
    get.return_value.json.return_value["models"][0]["context_window"] = 500_000
    assert mm.get_model_context_length("gpt-6.1-sol", **first) == 272_000
    assert mm.get_model_context_length("gpt-6.1-sol", **{**first, "api_key": "fixture-account-b"}) == 500_000
    assert mm.get_model_context_length("gpt-6.1-sol", **{
        **first, "base_url": "https://second.example.invalid/codex",
    }) == 500_000
    assert get.call_count == 3
    assert get.call_args.args[0].startswith("https://second.example.invalid/codex/models?")


def test_unavailable_catalog_uses_codex_fallback_not_generic_api_window(catalog):
    get = catalog(500_000)
    get.side_effect = OSError("offline fixture")
    comp = ContextCompressor(model="gpt-6.1-sol", provider="openai-codex",
                             base_url="https://route.example.invalid/codex", api_key="fixture-key",
                             threshold_percent=0.75, quiet_mode=True)
    assert comp.context_length == 272_000
    assert comp.threshold_tokens == int(comp.context_length * 0.75)
