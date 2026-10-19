"""Synthetic producer -> public pending/claim presentation, never real session bodies."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from gateway import run_notifications
from gateway.config import Platform
from gateway.session import SessionSource
from gateway.session_authority import SessionAuthority
from gateway.session_automation import admission_display, automation_display_metadata
from gateway.session_contract import SessionRef
from gateway.session_events import SessionEvents
from tools import process_registry_notifications as producer
from tui_gateway.contracts.canonical_sessions import CanonicalPendingAdmission


@pytest.fixture(params=[1, 2], ids=['single', 'batch'])
def completion(request):
    events = [dict(type='completion', session_id=f'proc_synthetic_{i}', command=f'printf SYNTHETIC_{i}',
                   output=f'SYNTHETIC_OUTPUT_{i}', exit_code=0, completion_reason='exited')
              for i in range(request.param)]
    texts = [producer.format_process_notification(event) for event in events]
    raw = texts[0] if len(events) == 1 else run_notifications.GatewayNotificationsMixin._format_coalesced_process_completions(
        [(text, event, None) for text, event in zip(texts, events)])
    event = dict(events[0], _display_text=producer.process_completion_display_text(events))
    metadata = run_notifications._watch_notification_metadata(event, SessionSource(platform=Platform.LOCAL, chat_id='s'))
    return run_notifications._mark_internal_notification(raw), automation_display_metadata(metadata)


def boundary(payload, monkeypatch):
    authority = SessionAuthority.__new__(SessionAuthority)
    authority.profile_id, authority.epoch, authority.executing = 'synthetic-profile', 1, {}
    stream = SessionEvents()
    frames = []
    stream.observers.add(lambda frame: frames.append(deepcopy(frame['params'])))
    live = SimpleNamespace(event_stream=stream)
    authority.sessions = {'s': live}
    row = dict(admission_id='admission', target_session_id='s', seq=1, status='queued', outcome=None,
               owner_epoch=1, generation=None, request_id='input', payload=deepcopy(payload))
    authority._pending_rows = lambda ref: [row]
    authority._handle = lambda ref: SimpleNamespace(execution_state='running' if row['generation'] else 'idle',
                                                   execution_generation=row['generation'] or 0, revision=1)
    monkeypatch.setattr('gateway.session_bot_mailbox.wake_bot_receipts', lambda *args: None)
    ref = SessionRef(authority.profile_id, 's')
    authority._publish_pending(ref)
    row.update(status='started', generation=1)
    authority._stamp_claim(ref, live, row)
    return authority, row, frames


@pytest.mark.parametrize('envelope', ['local', 'native'])
def test_pending_and_claim_carry_only_public_producer_presentation(completion, monkeypatch, envelope):
    text, card = completion
    descriptor = dict(card, identity='private-identity', owner='s', route='private-route', target='s')
    payload = {'text': text, **({'local_automation_v1': descriptor} if envelope == 'local' else
                              {'native_text_v1': {'automation': descriptor, 'provenance': {'private': 'proof'}}})}
    original = deepcopy(payload)
    _authority, row, frames = boundary(payload, monkeypatch)
    # Reached claim control: the full original completion is still the model input.
    assert frames[1]['type'] == 'message.start'
    assert frames[1]['payload'] == dict(card, text=text)
    assert row['payload'] == original
    for frame in (frames[0], frames[2]):
        pending = frame['payload']['pending'][0]
        assert pending['text'] == text
        assert {key: pending.get(key) for key in card} == card, 'pending lost trusted producer presentation'
        assert set(pending) == {'admission_id', 'ref', 'sequence', 'status', 'outcome', 'authority_epoch',
                                'execution_generation', 'input_id', 'text', 'display_kind', 'display_text'}
        CanonicalPendingAdmission.model_validate(pending)


def test_human_identical_notification_text_is_not_a_producer(completion, monkeypatch):
    text, _card = completion
    authority, row, frames = boundary({'text': text}, monkeypatch)
    pending = asdict(authority._pending_receipt(row))
    assert pending['text'] == text
    assert pending.get('display_kind') is None
    assert pending.get('display_text') is None
    assert frames[1]['payload'] == {}
    assert admission_display({'text': text}) == {}


@pytest.mark.asyncio
async def test_disposable_native_rpc_and_execution_keep_complete_input(completion, tmp_path, monkeypatch):
    """Real canonical dispatcher/SQLite/admission execution; inference is an observed seam, not a provider."""
    from gateway.platforms.event import MessageEvent
    from gateway.session_controls import AuthorityConnection
    from gateway.session_ingress import execute_admission
    from gateway.session_local import create_local_session
    from hermes_state_runtime import claim_session_input, get_session_admission, settle_session_input
    from tests.gateway.test_automation_notification_category import _authority

    text, card = completion
    runner, authority = await _authority(tmp_path, monkeypatch)
    transport = SimpleNamespace(write=lambda frame: True)
    connection = AuthorityConnection(authority, transport, {'user_id': 'synthetic-human'})
    ref = create_local_session(authority, connection.actor, dict(request_id='presentation', source='tui',
                                                                model='fixture', toolsets=[], cwd=str(tmp_path)))
    live = authority.sessions[ref.session_id]
    target = runner.session_store.lookup_by_session_key(live.route).session_id
    frames, executed, model_inputs = [], [], []
    live.event_stream.observers.add(lambda frame: frames.append(deepcopy(frame['params'])))

    async def inventory(*args):
        # Inventory is unrelated to this boundary and may probe Git/tools/providers.
        return {'desktop_protocol': 'hermes-gateway-v1'}

    original_handle = runner._handle_message

    async def handle(event):
        executed.append((event.text, event.internal, deepcopy(event.metadata)))
        return await original_handle(event)

    async def model(**params):
        model_inputs.append(params['message'])
        return {'final_response': 'Synthetic acknowledgement', 'messages': [], 'completed': True}

    monkeypatch.setattr('gateway.session_local_inventory.snapshot_info', inventory)
    monkeypatch.setattr(runner, '_handle_message', handle)
    monkeypatch.setattr(runner, '_run_agent', model)

    async def rpc(method, **params):
        reply = await connection.dispatch({'jsonrpc': '2.0', 'id': method, 'method': method,
                                           'params': {'session_id': ref.session_id, **params}})
        assert 'result' in reply, reply
        return reply['result']

    try:
        event = MessageEvent(text=text, source=live.source, internal=True,
            metadata={'gateway_session_key': live.route, 'gateway_session_id': target, **card})
        receipt = await authority.admit_automation(runner._adapter_for_source(live.source), event, 'synthetic-completion')
        pending = (await rpc('session.resume'))['pending'][0]
        assert (pending['text'], pending['display_kind'], pending['display_text']) == (text, card['display_kind'], card['display_text'])
        assert not ({'local_automation_v1', 'native_text_v1', 'provenance', 'route'} & set(pending))
        row = claim_session_input(authority.db, epoch=authority.epoch, session_id=ref.session_id)
        authority._stamp_claim(ref, live, row)
        await execute_admission(authority, ref, row)
        assert executed[0][0] == text and executed[0][1] is True
        assert model_inputs == [text]
        assert executed[0][2]['display_kind'] == card['display_kind']
        assert get_session_admission(authority.db, admission_id=receipt.admission_id)['payload']['text'] == text
        start = next(frame for frame in frames if frame['type'] == 'message.start')
        assert start['payload'] == dict(card, text=text)
        settle_session_input(authority.db, epoch=authority.epoch, admission_id=row['admission_id'],
                             generation=row['generation'], outcome='completed')
        await rpc('prompt.submit', input_id='human-lookalike', text=text)
        human = (await rpc('session.resume'))['pending'][0]
        assert human['text'] == text and human['display_kind'] is None and human['display_text'] is None
        row = claim_session_input(authority.db, epoch=authority.epoch, session_id=ref.session_id)
        authority._stamp_claim(ref, live, row)
        await execute_admission(authority, ref, row)
        assert executed[1][0] == text and executed[1][1] is False
        assert model_inputs == [text, text]
        assert 'display_kind' not in executed[1][2]
        assert [frame for frame in frames if frame['type'] == 'message.start'][-1]['payload'] == {}
    finally:
        await connection.close()
        runner.session_store.close_all_db_handles()
