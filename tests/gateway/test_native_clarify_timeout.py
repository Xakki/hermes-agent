"""Drive the owner clarify request through replay and its model-facing tool result."""
import json
from types import SimpleNamespace

import pytest

from gateway import local_viewer_events, session_clarify_batch, session_policy, session_server_requests as requests
from gateway.run_turn_runner import TurnRunner
from gateway.session_events import SessionEvents
from hermes_constants import get_hermes_home
from tools.clarify_tool import clarify_tool


@pytest.mark.parametrize(('when_answered', 'policy', 'choices', 'multi', 'selected'), [
    (None, 'recommended_or_first', ['Text', 'Table'], False, 'Text'),
    (9.999, 'recommended', ['Text', 'Table'], False, 'Table'),
    (10, 'recommended', ['Text', 'Table'], False, 'Text'),
    (None, 'recommended', ['Text'], False, None),
    (None, 'recommended_or_first', ['Text'], False, 'Text'),
    (None, 'first', ['Text', 'Table'], False, 'Text'),
    (None, 'first', ['Text (Recommended)', 'Table (Recommended)'], False, None),
    (None, 'first', ['Text', 'Text'], False, None),
    (None, 'first', ['Text', 'Table'], True, None),
    (None, 'first', None, False, None),
    (None, ['first'], ['Text', 'Table'], False, None),
    (None, 'none', ['Text', 'Table'], False, None),
])
def test_ink_native_batch_receipt(monkeypatch, when_answered, policy, choices, multi, selected):
    (get_hermes_home() / 'config.yaml').write_text(json.dumps({
        'agent': {'clarify_timeout': 10}, 'interaction': {'clarify_timeout_policy': policy},
    }))
    monkeypatch.setattr(session_policy, 'active_policy', lambda: SimpleNamespace(source='tui'))
    now = [100.0]
    monkeypatch.setattr(requests, 'time', SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1000.0), raising=False)
    events = SessionEvents()
    frames = []
    authority = SimpleNamespace(sessions={'s1': SimpleNamespace(event_stream=events)})
    turn = SimpleNamespace(_approval_owner=(authority, 's1', 1), _ctx=SimpleNamespace(agent_holder=[]))

    class Peer:
        def close(self):
            pass

        def write(self, obj: dict) -> bool:
            frame = obj
            frames.append(frame)
            if frame.get('method') != 'clarify':
                return True
            assert frame['params'].get('deadline_ms') == 1010000
            replay, = requests.open_requests(events, 's1')
            assert replay['id'] == frame['id'] and replay['params'] == frame['params']
            now[0] += 10 if when_answered is None else when_answered
            if when_answered is not None:
                assert requests.lock_answer(frame['id'], 'q0', 'Text' if when_answered == 1 else 'Table', session_ok=lambda *_: True) == (
                    [] if when_answered < 10 else None)
            return True

    events.fanout.attach(Peer())
    questions = [{'question': 'Format?', 'choices': choices, 'multi_select': multi}]
    callback = lambda qs: session_clarify_batch.ask_clarify_batch(turn, qs)
    receipt = clarify_tool(questions, callback=callback)
    result = json.loads(receipt)
    manual = when_answered == 9.999
    assert result == {'outcome': 'submitted' if selected else 'timed_out', 'responses': [{
        'qid': 'q0', 'question': 'Format?', 'choices_offered': choices,
        'status': 'answered' if selected else 'unanswered', 'user_response': selected,
    }]}
    request = next(f for f in frames if f.get('method') == 'clarify')
    assert request['params'].get('timeout_choices', {}) == (
        {'q0': {'option_id': 'q0:0', 'label': 'Text'}} if selected else {})
    assert requests.open_requests(events, 's1') == []
    assert requests.lock_answer(request['id'], 'q0', 'Table', session_ok=lambda *_: True) is None
    # Publication is synchronous; delivery to a viewer's mailbox is not.
    settled = [e for e in events.since(events.epoch, 0)['events'] if e['type'] == 'request.cancel']
    assert [e['payload'] for e in settled] == [{
        'id': request['id'], 'method': 'clarify', 'reason': 'resolved' if manual else 'timeout',
    }]

    turn._ctx.result_holder = [None]
    turn._ctx.session_id = 's1'
    local_viewer_events.complete_fields(turn, 'tool-1', 'clarify', {}, receipt)
    local_viewer_events.complete_fields(turn, 'tool-2', 'other', {}, receipt)
    messages = [{'role': 'tool', 'content': receipt}, {'role': 'assistant', 'content': 'Model text.'}]
    final = {'final_response': 'Model text.', 'completed': True, 'messages': messages}
    streamed = []
    consumer = SimpleNamespace(finish=streamed.append)
    TurnRunner._finish_stream_consumer(turn, final, [], consumer)
    expected = f'\n\nВыбранные варианты:\n- Format?: {selected}' if selected else ''
    assert final['final_response'] == 'Model text.' + expected
    assert messages[-1]['content'] == 'Model text.'
    if selected:
        assert final['_final_response_without_clarify_report'] == 'Model text.'
    assert streamed == [final['final_response']]
    assert turn._ctx.result_holder == [final]
    TurnRunner._finish_stream_consumer(turn, final, [], None)
    assert final['final_response'] == 'Model text.' + expected

    if selected and not manual:
        now[0] = 100.0
        when_answered = 1
        # Exercise the same native lock and tool projection for the same option.
        manual_receipt = clarify_tool(questions, callback=callback)
        assert manual_receipt == receipt


@pytest.mark.parametrize(('manual', 'opt_in', 'origin'), [(True, False, 'manual'), (False, True, 'timeout'),
                                                       (False, False, 'none')])
def test_profile_choice_targets_exact_question_and_preserves_origin(monkeypatch, manual, opt_in, origin):
    instruction = 'Use terse CSV with a header.'
    (get_hermes_home() / 'config.yaml').write_text(json.dumps({
        'agent': {'clarify_timeout': 10}, 'interaction': {'clarify_timeout_policy': 'recommended', 'menu': [
            {'question_key': 'Format?', 'id': 'csv', 'label': 'CSV', 'handler': 'prompt', 'prompt': instruction,
             'recommended': True, 'auto_select': opt_in},
            {'question_key': 'Format?', 'id': 'disabled', 'label': 'Disabled', 'handler': 'prompt',
             'prompt': 'Do not show this.', 'enabled': False, 'auto_select': True},
            {'id': 'old', 'label': 'Old', 'handler': 'prompt', 'prompt': 'Do not show this.'},
        ]},
    }))
    monkeypatch.setattr(session_policy, 'active_policy', lambda: SimpleNamespace(source='tui'))
    now = [100.0]
    monkeypatch.setattr(requests, 'time', SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1000.0))
    events = SessionEvents()
    frames, locks = [], []
    authority = SimpleNamespace(sessions={'s1': SimpleNamespace(event_stream=events)})
    turn = SimpleNamespace(_approval_owner=(authority, 's1', 1), _ctx=SimpleNamespace(agent_holder=[]))

    class Peer:
        def close(self):
            pass

        def write(self, obj: dict) -> bool:
            if obj.get('method') != 'clarify':
                return True
            frames.append(obj['params'])
            try:
                if manual:
                    allow = lambda *_: True
                    locks.append(requests.lock_answer(obj['id'], 'q0', None, option_id='csv', session_ok=allow))
                    locks.append(requests.lock_answer(obj['id'], 'q1', 'Text', session_ok=allow))
            finally:
                now[0] += 10
            return True

    events.fanout.attach(Peer())
    receipt = json.loads(clarify_tool([
        {'question': 'Format?', 'choices': ['Text']}, {'question': 'Alternate format?', 'choices': ['Text']},
    ], callback=lambda qs: session_clarify_batch.ask_clarify_batch(turn, qs)))
    [params] = frames
    assert params['questions'] == [
        {'qid': 'q0', 'question': 'Format?', 'choices': ['Text', 'CSV (Recommended)'], 'multi_select': False,
         'custom_option_ids': {'CSV (Recommended)': 'csv'}},
        {'qid': 'q1', 'question': 'Alternate format?', 'choices': ['Text'], 'multi_select': False},
    ]
    assert instruction not in json.dumps(params)
    assert params.get('timeout_choices', {}) == ({'q0': {'option_id': 'csv', 'label': 'CSV'}} if opt_in else {})
    response = receipt['responses'][0]
    assert response.get('configured_instruction') == (instruction if origin != 'none' else None)
    assert response == {'qid': 'q0', 'question': 'Format?', 'choices_offered': ['Text', 'CSV'],
                        'status': 'answered' if origin != 'none' else 'unanswered',
                        'user_response': 'CSV' if origin != 'none' else None,
                        **({'configured_instruction': instruction} if origin != 'none' else {})}
    assert receipt['outcome'] == ('submitted' if manual else 'timed_out')
    assert receipt['responses'][1]['status'] == ('answered' if manual else 'unanswered')
    assert receipt['responses'][1]['user_response'] == ('Text' if manual else None)
    if manual:
        assert locks == [['q1'], []]
