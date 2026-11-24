"""The owner consumes one explicit idle-session action before its own deadline."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway import session_commands, session_next_action as next_action
from gateway.config import Platform
from gateway.session_contract import SessionActor, SessionRef, Submission
from persistence.core.errors import AuthorityRefusal


@pytest.fixture
def owner(tmp_path, monkeypatch):
    ref = SessionRef(str(tmp_path), 'owned')
    actor = SessionActor('author', ref.profile_id, frozenset({'session:respond', 'session:submit'}), 'viewer')
    live = SimpleNamespace(source=SimpleNamespace(platform=Platform.LOCAL, user_id=actor.subject),
                           next_action_candidate=None, next_action_offer=None, subscribers={'viewer': actor},
                           mutation_lock=asyncio.Lock(), event_stream=SimpleNamespace(publish=Mock()))
    handle = SimpleNamespace(execution_state='idle', execution_generation=3, revision=7)
    authority = SimpleNamespace(sessions={ref.session_id: live}, profile_id=ref.profile_id,
                                authorize=Mock(), _handle=lambda _: handle, _pending_rows=lambda _: [],
                                _submit=AsyncMock())
    conn = SimpleNamespace(authority=authority, actor=actor, subscriptions={ref.session_id: 'viewer'},
                           submission=lambda target, p: Submission(p.pop('submission_id'), target, p, 'queue'))
    clock = SimpleNamespace(now=10)
    monkeypatch.setattr(next_action, 'time', SimpleNamespace(monotonic=lambda: clock.now, time=lambda: 1_000))
    (tmp_path / 'config.yaml').write_text(r'''interaction:
  show_countdown: false
  menu:
    - {id: summarize, label: Summarize, description: Outline changes, enabled: true, handler: prompt, prompt: 'Summarize the changes; mention /diff without running it.'}
    - {id: continue, label: Spoof, handler: prompt, prompt: Wrong}
    - {id: shell, label: Shell, handler: exec, prompt: Wrong}
    - {id: slash, label: Slash, handler: prompt, prompt: '  /new'}
    - {id: bang, label: Bang, handler: prompt, prompt: '!printf nope'}
    - {id: disguised, label: Disguised, handler: prompt, prompt: "@file:/example\n\n/new"}
''')
    return conn, ref, live, handle, clock


def issue(owner):
    conn, ref, live, _, _ = owner
    next_action.candidate(live, {'payload': {'text': 'request', 'cli_turn_v1': {'clarify_batch': True}},
                                 'request_id': 'request', 'principal_id': conn.actor.subject, 'generation': 3},
                          {'outcome': 'completed'}, 'answer')
    next_action.offer_idle(conn.authority, ref, live)
    _, event = live.event_stream.publish.call_args
    assert event['event_type'] == 'interaction.next_action'
    return live.event_stream.publish.call_args.args[1]


def choose(owner, offer, name):
    conn, ref, *_ = owner
    return asyncio.run(next_action.handlers(conn)['interaction.next_action.respond'](
        ref, {'offer_id': offer['id'], 'action_id': name}))


def test_profile_menu_only_admits_the_selected_text_once(owner, monkeypatch):
    conn, ref, live, _, _ = owner
    slash = AsyncMock(side_effect=AssertionError('text reached the slash dispatcher'))
    monkeypatch.setattr(session_commands, 'execute_command', slash)
    card = issue(owner)
    assert card['deadline_ms'] == 1_600_000 and card['show_countdown'] is False
    assert [option['id'] for option in card['actions']] == ['continue', 'review_changes', 'summarize', 'close']
    assert all('prompt' not in option for option in card['actions'])
    next_action.offer_idle(conn.authority, ref, live)
    assert live.event_stream.publish.call_count == 1
    assert choose(owner, card, 'summarize') == {}
    conn.authority._submit.assert_awaited_once_with(conn.actor, Submission(
        f"next-action:{card['id']}", ref,
        {'text': 'Summarize the changes; mention /diff without running it.', 'clarify_batch': True}, 'queue'))
    with pytest.raises(AuthorityRefusal, match='stale_generation'):
        choose(owner, card, 'continue')
    slash.assert_not_awaited()


@pytest.mark.parametrize('invalid', ['exact_deadline', 'late', 'detached', 'foreign_offer', 'wrong_owner', 'new_revision'])
def test_refused_selection_never_runs_an_action(owner, invalid):
    conn, ref, live, handle, clock = owner
    card = issue(owner)
    reason = 'stale_generation'
    if invalid in {'exact_deadline', 'late'}:
        clock.now = live.next_action_offer.deadline + (1 if invalid == 'late' else 0)
    elif invalid == 'new_revision':
        handle.revision += 1
    elif invalid == 'foreign_offer':
        card = {**card, 'id': 'belongs-to-another-session'}
    elif invalid == 'wrong_owner':
        reason = 'permission_denied'
        conn.actor = SessionActor('other', ref.profile_id, conn.actor.capabilities, 'second-viewer')
    else:
        reason = 'permission_denied'
        conn.subscriptions.clear()
    with pytest.raises(AuthorityRefusal, match=reason):
        choose(owner, card, 'continue')
    conn.authority._submit.assert_not_awaited()
    assert live.event_stream.publish.call_count == 1


def test_close_does_nothing_and_show_changes_uses_fixed_diff(owner, monkeypatch):
    conn, ref, live, _, _ = owner
    slash = AsyncMock(return_value={'type': 'exec', 'output': 'changes'})
    monkeypatch.setattr(session_commands, 'execute_command', slash)
    assert choose(owner, issue(owner), 'close') == {}
    assert live.next_action_offer is None
    slash.assert_not_awaited()
    assert choose(owner, issue(owner), 'review_changes') == {'output': 'changes'}
    slash.assert_awaited_once_with(conn, ref, {'command': 'diff'})
    conn.authority._submit.assert_not_awaited()
