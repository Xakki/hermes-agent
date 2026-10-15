"""Synthetic HTTP transports exercise the real SDK, not paid inference."""
import time

import httpx
from itertools import pairwise

import pytest
from openai import OpenAI


def test_physical_sdk_retries_budget_and_ordinary_calls_are_unchanged():
    from agent.auxiliary_budget import DecisionBudget, decision_budget, bounded_create, DecisionBudgetExceeded
    from agent.auxiliary_client import _create_with_progress_once
    seen = []
    def transport(request):
        seen.append(request)
        return httpx.Response(429, json={"error": {"message": "fixture limit", "type": "rate_limit"}})
    client = OpenAI(api_key="synthetic-not-a-credential", base_url="https://fixture.invalid/v1",
                    max_retries=1, http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    kwargs = {"model": "fixture", "messages": [{"role": "user", "content": "choose"}], "max_tokens": 256}
    budget = DecisionBudget(time.monotonic() + 30, lambda: False, attempts=2)
    with decision_budget(budget):
        for _ in range(2):
            with pytest.raises(Exception):
                _create_with_progress_once(client, kwargs)
        with pytest.raises(DecisionBudgetExceeded):
            bounded_create(client, kwargs)
    assert len(seen) == 2 and len(budget.attempts) == 2
    assert all(row["status"] == 429 and row["model"] == "fixture" for row in budget.attempts)
    assert client.max_retries == 1
    with pytest.raises(Exception):
        _create_with_progress_once(client, kwargs)
    assert len(seen) == 4  # ordinary auxiliary retains its configured SDK retry
    client.close()


def test_cancel_input_output_and_deadline_fail_before_http_dispatch():
    from agent.auxiliary_budget import DecisionBudget, decision_budget, bounded_create, DecisionBudgetExceeded
    seen = []
    client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", http_client=httpx.Client(
        transport=httpx.MockTransport(lambda req: seen.append(req) or httpx.Response(200, json={}))))
    for budget, kwargs in (
        (DecisionBudget(time.monotonic() - 1, lambda: False), {}),
        (DecisionBudget(time.monotonic() + 30, lambda: True), {}),
        (DecisionBudget(time.monotonic() + 30, lambda: False), {"messages": [{"role": "user", "content": "x" * 9000}]}),
        (DecisionBudget(time.monotonic() + 30, lambda: False), {"tools": [{"type": "function"}]}),
    ):
        with decision_budget(budget), pytest.raises(DecisionBudgetExceeded):
            bounded_create(client, {"model": "fixture", "messages": [], "max_tokens": 256, **kwargs})
    assert not seen
    client.close()


@pytest.mark.parametrize("fault", ["response-size", "cancel", "deadline", "usage"])
def test_real_sdk_response_boundary_closes_and_rejects_oversized_or_revoked_work(fault):
    import json
    import threading
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded, bounded_create, decision_budget, decision_budget_active
    closed = threading.Event()
    revoked = [False]
    seen = []
    budget = DecisionBudget(time.monotonic() + 30, lambda: revoked[0])
    class FixtureStream(httpx.SyncByteStream):
        def __iter__(self):
            if fault in {"cancel", "deadline"}:
                if fault == "cancel":
                    revoked[0] = True
                else:
                    budget.deadline = time.monotonic() - 1
                assert closed.wait(5), "physical stream was not closed at the boundary"
                yield b"late provider payload"
            elif fault == "response-size":
                yield b"x" * 65537
            else:
                yield json.dumps({"id": "fixture", "object": "chat.completion", "model": "fixture",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "stop"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 257, "total_tokens": 258}}).encode()
        def close(self):
            closed.set()
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=FixtureStream())
    client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", max_retries=2,
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    kwargs = {"model": "fixture", "messages": [{"role": "user", "content": "choose"}], "max_tokens": 256}
    try:
        with decision_budget(budget), pytest.raises(DecisionBudgetExceeded):
            bounded_create(client, kwargs)
        assert closed.is_set() and len(seen) == len(budget.attempts) == 1
        assert seen[0]["max_tokens"] == 256 and client.max_retries == 2
        assert not decision_budget_active()
    finally:
        client.close()


def test_native_stream_adapter_keeps_wire_cap_and_ordinary_auxiliary_unbounded_by_decision_context():
    import json
    from agent.auxiliary_budget import DecisionBudget, decision_budget, decision_budget_active
    from agent.auxiliary_client import _relay_sync_stream
    seen = []
    def transport(request):
        body = json.loads(request.content)
        seen.append(body)
        chunk = {"id": "fixture", "object": "chat.completion.chunk", "model": "fixture",
                 "choices": [{"index": 0, "delta": {"content": "stop"}, "finish_reason": "stop"}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
    client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    kwargs = {"model": "fixture", "messages": [{"role": "user", "content": "choose"}], "max_tokens": 1024}
    try:
        with decision_budget(DecisionBudget(time.monotonic() + 30, lambda: False, max_output_tokens=256)):
            assert list(_relay_sync_stream(client, kwargs))
        assert not decision_budget_active()
        assert list(_relay_sync_stream(client, kwargs))
        assert [body["max_tokens"] for body in seen] == [256, 1024]
    finally:
        client.close()


@pytest.mark.parametrize("cap", [1, 256, 150000])
def test_configured_wire_cap_and_shared_aggregate_bound_real_sdk_dispatch(cap):
    import json
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded, bounded_create, decision_budget
    seen = []
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"message": "unknown fixture usage", "type": "rate_limit"}})
    client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", max_retries=2,
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    budget = DecisionBudget(time.monotonic() + 600, lambda: False, max_output_tokens=cap)
    deadline = budget.deadline
    try:
        with decision_budget(budget):
            for _ in range(min(3, 150000 // cap)):
                with pytest.raises(Exception):
                    bounded_create(client, {"model": "fixture", "messages": [], "max_tokens": 150000})
            with pytest.raises(DecisionBudgetExceeded):
                bounded_create(client, {"model": "fixture", "messages": [], "max_tokens": 150000})
        assert [body["max_tokens"] for body in seen] == [cap] * min(3, 150000 // cap)
        assert budget.output_reserved == sum(row["output_cap"] for row in budget.attempts) <= 150000
        assert budget.deadline == deadline and client.max_retries == 2
        assert all("output_tokens" not in row for row in budget.attempts)
    finally:
        client.close()


@pytest.mark.parametrize("invalid", [150001, 0, -1, True, 1.5, float("inf"), float("nan"), "150000", None])
def test_invalid_decision_wire_cap_is_rejected(invalid):
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded
    with pytest.raises(DecisionBudgetExceeded):
        DecisionBudget(time.monotonic() + 600, lambda: False, max_output_tokens=invalid)


def test_later_physical_attempt_clamps_remaining_aggregate_without_usage_refund():
    import json
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded, bounded_create, decision_budget
    seen = []
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "model": "fixture",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"choice":"stop"}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 8, "prompt_tokens": 1, "total_tokens": 9}})
    client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    budget = DecisionBudget(time.monotonic() + 600, lambda: False, max_output_tokens=60000)
    try:
        with decision_budget(budget):
            for _ in range(3):
                bounded_create(client, {"model": "fixture", "messages": [], "max_completion_tokens": 60000})
            with pytest.raises(DecisionBudgetExceeded):
                bounded_create(client, {"model": "fixture", "messages": [], "max_tokens": 1})
        assert [body["max_completion_tokens"] for body in seen] == [60000, 60000, 30000]
        assert budget.output_reserved == 150000
        assert all(row["output_tokens"] == 8 for row in budget.attempts)
        # The physical boundary also rejects caller kwargs that bypass the SDK clamp.
        fresh = DecisionBudget(time.monotonic() + 600, lambda: False, max_output_tokens=256)
        with pytest.raises(DecisionBudgetExceeded):
            fresh.start(httpx.Request("POST", "https://fixture.invalid", json={"max_tokens": 257}), "fixture")
        assert not fresh.attempts
    finally:
        client.close()


@pytest.mark.parametrize("cap,oversized,usage_excess,streaming", [
    (150000, False, False, False), (150000, True, False, False),
    (150000, False, True, False), (256, True, False, False),
    (150000, False, False, True), (150000, False, True, True),
])
def test_response_bytes_and_usage_are_bounded_by_actual_wire_cap(cap, oversized, usage_excess, streaming):
    import json
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded, bounded_create, decision_budget
    byte_limit = 65536 if cap <= 256 else 65536 + cap * 512
    class FixtureStream(httpx.SyncByteStream):
        def __iter__(self):
            if oversized:
                for _ in range(byte_limit // 8192 + 1):
                    yield b"x" * 8192
                return
            payload = {"id": "fixture", "object": "chat.completion.chunk" if streaming else "chat.completion", "model": "fixture",
                "choices": [{"index": 0, "delta" if streaming else "message": {"role": "assistant", "content": '{"choice":"stop"}',
                    "reasoning_content": "x" * 70000}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": cap + 1 if usage_excess else cap, "prompt_tokens": 1,
                          "completion_tokens_details": {"reasoning_tokens": cap - 8}}}
            data = json.dumps(payload).encode()
            if streaming:
                data = b"data: " + data + b"\n\ndata: [DONE]\n\n"
            for start in range(0, len(data), 8192):
                yield data[start:start + 8192]
    client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", http_client=httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200,
            headers={"content-type": "text/event-stream" if streaming else "application/json"}, stream=FixtureStream()))))
    budget = DecisionBudget(time.monotonic() + 600, lambda: False, max_output_tokens=cap)
    def consume():
        result = bounded_create(client, {"model": "fixture", "messages": [], "max_tokens": cap, "stream": streaming})
        if streaming:
            list(result)
    try:
        with decision_budget(budget):
            if oversized or usage_excess:
                with pytest.raises(DecisionBudgetExceeded):
                    consume()
            else:
                consume()
                assert budget.attempts[0]["output_tokens"] == cap
        assert budget.output_reserved == cap
    finally:
        client.close()


@pytest.mark.parametrize("used_capped_budget", [False, True])
def test_codex_native_adapter_allows_uncapped_wire_and_reports_usage(used_capped_budget):
    import json
    from types import SimpleNamespace
    from agent.auxiliary_client import _CodexCompletionsAdapter
    from agent.auxiliary_client import _RELAY_AUX_CALL_CONTEXT
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded, bounded_create, decision_budget
    seen = []
    response_sizes = []
    decision = '{"choice":"stop"}'
    output_tokens = 150001
    def transport(req):
        seen.append(json.loads(req.content))
        events = [
            {"type": "response.created", "response": {"id": "resp_fixture", "status": "in_progress", "model": "fixture"}},
            {"type": "response.output_item.done", "output_index": 0, "item": {
                "id": "msg_fixture", "type": "message", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": decision, "annotations": []}]}},
            {"type": "response.completed", "response": {"id": "resp_fixture", "status": "completed",
                "model": "fixture", "output": None,
                "usage": {"input_tokens": 1, "output_tokens": output_tokens, "total_tokens": output_tokens + 1,
                          "output_tokens_details": {"reasoning_tokens": 17}}}},
        ]
        wire = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        response_sizes.append(len(wire.encode()))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=httpx.ByteStream(wire.encode()))
    client = OpenAI(api_key="synthetic", base_url="https://chatgpt.com/backend-api/codex", http_client=httpx.Client(
        transport=httpx.MockTransport(transport)))
    client._hermes_aux_effective_provider = "openai-codex"
    adapter = SimpleNamespace(_real_client=client, chat=SimpleNamespace(completions=_CodexCompletionsAdapter(client, "fixture")))
    budget = DecisionBudget(time.monotonic() + 600, lambda: False,
                            max_output_tokens=150000 if used_capped_budget else 256)
    capped_client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", http_client=httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(429, json={"error": {"message": "fixture"}}))))
    token = _RELAY_AUX_CALL_CONTEXT.set({"provider": "generic", "task": "interaction_decision"})
    try:
        with decision_budget(budget):
            if used_capped_budget:
                with pytest.raises(Exception):
                    bounded_create(capped_client, {"model": "fixture", "messages": [], "max_tokens": 150000})
                assert budget.output_reserved == 150000
            _RELAY_AUX_CALL_CONTEXT.set({"provider": "openai-codex", "task": "interaction_decision"})
            result = bounded_create(adapter, {"model": "fixture", "messages": [{"role": "user", "content": "choose"}], "max_tokens": 150000})
        assert result.choices[0].message.content == decision
        assert seen
        wire_body = json.dumps(seen[0])
        assert not any(f'"{field}"' in wire_body for field in ("max_output_tokens", "max_tokens", "max_completion_tokens"))
        assert [row["output_cap"] for row in budget.attempts] == ([150000, None] if used_capped_budget else [None])
        assert budget.attempts[-1]["output_tokens"] == output_tokens
        assert budget.attempts[-1]["response_model"] == "fixture"
        assert response_sizes[0] <= 65536
        assert budget.output_reserved == (150000 if used_capped_budget else 0)
        assert budget.attempts[0]["input_bytes"] <= 8192
        generic = DecisionBudget(time.monotonic() + 600, lambda: False)
        with pytest.raises(DecisionBudgetExceeded):
            generic.start(httpx.Request("POST", "https://fixture.invalid/v1/responses", json={"model": "fixture"}), "generic")
        assert not generic.attempts
    finally:
        _RELAY_AUX_CALL_CONTEXT.reset(token)
        capped_client.close()
        client.close()


@pytest.mark.parametrize("fault", ["deadline", "cancel", "input", "tools", "attempts", "response-size", "stream-cancel", "stream-deadline"])
def test_uncapped_codex_keeps_other_physical_boundaries(fault):
    import threading
    from types import SimpleNamespace
    from agent.auxiliary_client import _CodexCompletionsAdapter, _RELAY_AUX_CALL_CONTEXT
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded, bounded_create, decision_budget

    seen = []
    closed = threading.Event()
    revoked = [fault == "cancel"]
    budget = DecisionBudget(time.monotonic() + (-1 if fault == "deadline" else 30),
                            lambda: revoked[0], attempts=1, max_output_tokens=256)

    class FixtureStream(httpx.SyncByteStream):
        def __iter__(self):
            if fault in {"stream-cancel", "stream-deadline"}:
                if fault == "stream-cancel":
                    revoked[0] = True
                else:
                    budget.deadline = time.monotonic() - 1
                assert closed.wait(5), "uncapped physical stream was not closed"
            yield b"x" * 65537

        def close(self):
            closed.set()

    def transport(request):
        seen.append(request)
        if fault == "attempts":
            return httpx.Response(429, json={"error": {"message": "fixture limit"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=FixtureStream())

    client = OpenAI(api_key="synthetic", base_url="https://chatgpt.com/backend-api/codex", max_retries=2,
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    adapter = SimpleNamespace(_real_client=client, chat=SimpleNamespace(completions=_CodexCompletionsAdapter(client, "fixture")))
    kwargs = {"model": "fixture", "messages": [{"role": "user", "content": "x" * 9000 if fault == "input" else "choose"}],
              "max_tokens": 256}
    if fault == "tools":
        kwargs["tools"] = [{"type": "function", "function": {"name": "fixture", "parameters": {"type": "object", "properties": {}}}}]
    token = _RELAY_AUX_CALL_CONTEXT.set({"provider": "openai-codex", "task": "interaction_decision"})
    try:
        with decision_budget(budget):
            if fault == "attempts":
                with pytest.raises(Exception):
                    bounded_create(adapter, kwargs)
            with pytest.raises(DecisionBudgetExceeded):
                bounded_create(adapter, kwargs)
        dispatched = fault in {"attempts", "response-size", "stream-cancel", "stream-deadline"}
        assert len(seen) == len(budget.attempts) == int(dispatched)
        assert budget.output_reserved == 0 and client.max_retries == 2
        if dispatched:
            assert budget.attempts[0]["output_cap"] is None
        if fault in {"response-size", "stream-cancel", "stream-deadline"}:
            assert closed.is_set()
    finally:
        _RELAY_AUX_CALL_CONTEXT.reset(token)
        client.close()


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"], ids=["lf", "crlf", "cr"])
@pytest.mark.parametrize("footer,output", [(False, 128), (False, 256), (False, 257), (True, 256), (True, 257)],
                         ids=["below", "at-cap", "over-cap", "footer-at-cap", "footer-over-cap"])
def test_real_sdk_multiline_usage_framing_is_accepted_but_cannot_evade_wire_cap(ending, footer, output):
    import itertools
    import json
    from agent.auxiliary_budget import DecisionBudget, DecisionBudgetExceeded, bounded_create, decision_budget

    choice = '{"choice":"stop"}'
    payload = {"id": "fixture", "object": "chat.completion.chunk", "model": "fixture",
        "choices": [{"index": 0, "delta": {"content": choice}, "finish_reason": "stop"}]}
    usage = {"completion_tokens": output, "prompt_tokens": 1, "total_tokens": output + 1,
             "completion_tokens_details": {"reasoning_tokens": output - 8}}
    def event(data):
        # Only newline-joined data is valid JSON; individual data fragments are not events.
        lines = [b": fixture keepalive", b"id: synthetic", b"retry: 1000"]
        lines.extend(b"data: " + line.encode() for line in json.dumps(data, indent=2).splitlines())
        return ending.join(lines) + ending + ending
    if footer:
        wire = event(payload) + event({**payload, "choices": [], "usage": usage})
    else:
        wire = event({**payload, "usage": usage})
    wire += b"data: [DONE]" + ending + ending

    fragments = []
    offset = 0
    for width in itertools.cycle((1, 7, 2, 19, 3)):
        stop = min(offset + width, len(wire))
        cr = wire.find(b"\r", offset, stop)
        if cr >= offset:
            stop = cr + 1  # Deliberately separate every CR from a following LF.
        fragments.append(wire[offset:stop])
        offset = stop
        if offset == len(wire):
            break
    assert b"".join(fragments) == wire
    if ending == b"\r\n":
        assert any(left.endswith(b"\r") and right.startswith(b"\n") for left, right in pairwise(fragments))

    seen, closed = [], []
    class FixtureStream(httpx.SyncByteStream):
        def __iter__(self):
            yield from fragments
        def close(self):
            closed.append(True)
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=FixtureStream())
    client = OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", max_retries=2,
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    kwargs = {"model": "fixture", "messages": [], "max_tokens": 256, "stream": True}
    def consume():
        stream = bounded_create(client, kwargs)
        try:
            return list(stream)
        finally:
            stream.close()
    def assert_native_payload(chunks):
        assert "".join(chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices) == choice
        assert [chunk.usage.completion_tokens for chunk in chunks if chunk.usage is not None] == [output]
    try:
        # Ordinary native SDK accepts these exact framed events, even above the requested cap.
        assert_native_payload(consume())
        budget = DecisionBudget(time.monotonic() + 600, lambda: False, max_output_tokens=256)
        with decision_budget(budget):
            if output > 256:
                with pytest.raises(DecisionBudgetExceeded, match="provider exceeded requested output cap"):
                    consume()
            else:
                assert_native_payload(consume())
        assert len(seen) == 2 and all(body["max_tokens"] == 256 for body in seen)
        assert len(budget.attempts) == 1 and budget.output_reserved == 256
        assert closed and client.max_retries == 2
    finally:
        client.close()
