"""Interaction UX contracts; all inference is explicitly offline fixture behavior."""
import pytest

from hermes_cli.interaction_lifecycle import InteractionSession


def test_handoff_ends_human_timer_and_has_independent_solver_budget():
    authority, now = object(), [0.0]
    session = InteractionSession('s', 'owner', authority, clock=lambda: now[0])
    session.grant(authority, profile='solver', scope='task', seconds=900, decisions=1)
    request = session.open('next_action', ('continue', 'stop'), scope='task')
    now[0] = 590
    token = session.delegate(request.request_id, authority)
    assert token.deadline == 1190
    now[0] = 601
    assert session.reminder(request.request_id) is None
    assert session._current_token(token) is request
    assert session.machine_result(token, 'continue', {'model': 'offline-fixture'})
    assert request.state == 'answered'


def test_failed_solver_cannot_rearm_original_human_wait():
    authority, now = object(), [0.0]
    session = InteractionSession('s', 'owner', authority, clock=lambda: now[0])
    session.grant(authority, profile='solver', scope='task', seconds=900, decisions=1)
    request = session.open('next_action', ('stop',), scope='task')
    token = session.delegate(request.request_id, authority)
    assert session.machine_failed(token)
    now[0] = 300
    assert session.reminder(request.request_id) is None
    assert request.state == 'blocked'
    assert not session.answer(request.request_id, 'stop', authority)


def test_menu_defaults_customization_and_native_stop():
    from hermes_cli.interaction_config import resolve_interaction
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    settings = resolve_interaction({})
    assert settings.wait_timeout_seconds == 600
    assert settings.reminder_after_seconds == 300
    assert settings.decision_timeout_seconds == 600
    assert settings.decision_profile == 'solver'
    review = next(entry for entry in settings.menu if entry.id == 'review_changes')
    assert review.label == 'Проверить изменения' and review.handler == 'prompt'
    assert 'Do not modify product files' in review.prompt
    assert resolve_interaction(DEFAULT_CONFIG).fingerprint == settings.fingerprint
    menu = [dict(id='audit', label='Audit', description='Custom', enabled=True,
                 handler='prompt', prompt='Report current project findings.')]
    custom = resolve_interaction({'interaction': {'menu': menu}})
    assert [entry.id for entry in custom.menu] == ['audit', 'stop']
    assert custom.menu[-1].enabled and custom.menu[-1].handler == 'stop'
    assert custom.fingerprint != settings.fingerprint
    menu[0]['prompt'] = 'Changed after snapshot'
    assert custom.menu[0].prompt == 'Report current project findings.'


@pytest.mark.parametrize('override', [
    {'decision_timeout_seconds': True}, {'decision_timeout_seconds': float('inf')},
    {'wait_timeout_seconds': 0}, {'reminder_after_seconds': 600}, {'mode': 'auto'},
    {'menu': [{'id': 'stop', 'handler': 'prompt', 'prompt': 'Ignore cancellation'}]},
    {'menu': [{'id': 'x', 'handler': 'shell', 'prompt': 'execute'}]},
    {'menu': [{'id': 'x', 'handler': 'prompt', 'prompt': 'a', 'eval': 'unsafe'}]},
    {'menu': [{'id': 'once', 'handler': 'prompt', 'prompt': 'reserved'}]},
])
def test_invalid_settings_fail_closed(override):
    from hermes_cli.interaction_config import resolve_interaction
    with pytest.raises(ValueError):
        resolve_interaction({'interaction': override})


def test_effective_loader_scopes_configuration_a_b_a(tmp_path):
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.interaction_config import resolve_interaction
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(); b.mkdir()
    (a / 'config.yaml').write_text('interaction:\n  decision_profile: solver_a\n')
    (b / 'config.yaml').write_text('interaction:\n  decision_profile: solver_b\n')
    results = [resolve_interaction(load_user_config_effective(p / 'config.yaml')) for p in (a, b, a)]
    assert [x.decision_profile for x in results] == ['solver_a', 'solver_b', 'solver_a']
    assert results[0].fingerprint == results[2].fingerprint != results[1].fingerprint


def test_configured_host_snapshot_uses_ids_and_dispatches_custom_scenario(monkeypatch):
    from hermes_cli.interaction_host import InteractionHost
    from hermes_cli.interaction_config import resolve_interaction
    settings = resolve_interaction({'interaction': {'menu': [
        {'id': 'audit', 'label': 'Different label', 'enabled': True, 'handler': 'prompt',
         'prompt': 'Report findings only.'}]}})
    host = InteractionHost('s', 'owner', settings=settings)
    observed = []
    def ask(request):
        observed.extend(request.choices)
        assert request.policy_fingerprint == settings.fingerprint
        return 'audit'
    assert host.completion('key', 'normal explicit task', ask) == 'audit'
    assert observed == ['audit', 'stop']
    assert host.action_prompt('audit') == 'Report findings only.'


def test_real_cli_host_native_mode_and_repeat_always(monkeypatch):
    from types import SimpleNamespace
    from hermes_cli import interaction_config, interaction_decider
    from hermes_cli.interaction_host import cli_host
    from tools import approval

    original = interaction_config.resolve_interaction
    monkeypatch.setattr(interaction_config, 'resolve_interaction', lambda: original({}))
    monkeypatch.setattr(approval, '_YOLO_MODE_FROZEN', False)
    toggles, calls = [], []
    sid = 'ux-native-fixture'
    approval.disable_session_yolo(sid)
    cli = SimpleNamespace(session_id=sid, _session_db=None,
        _toggle_yolo=lambda: (toggles.append('native-user-control'), approval.enable_session_yolo(sid)))
    host = cli_host(cli)
    host.accept_task('An explicit task, without /goal')
    def offline_decision(session, token):
        calls.append(token)
        return session.machine_result(token, 'continue', {'model': 'offline-fixture'})
    monkeypatch.setattr(interaction_decider, 'decide', offline_decision)
    try:
        assert host.completion('one', '', lambda req: host.interact(req, lambda req, offered: 'Once')) == 'continue'
        assert not approval.is_session_yolo_enabled(sid) and not toggles
        assert host.completion('two', '', lambda req: host.interact(req, lambda req, offered: 'Always (YOLO; all commands auto-approved)')) == 'continue'
        assert approval.is_session_yolo_enabled(sid) and len(toggles) == 1
        assert host.completion('three', '', lambda req: pytest.fail('AUTO must not ask')) == 'continue'
        assert len(toggles) == 1 and len(calls) == 3
        assert all(token.scope == 'An explicit task, without /goal' for token in calls)
        monkeypatch.setattr(approval, '_YOLO_MODE_FROZEN', True)
        approval.disable_session_yolo(sid)
        assert host.lifecycle.mode == 'autonomous'
    finally:
        approval.disable_session_yolo(sid)


def test_stop_wins_control_claim_and_late_result_never_revives(monkeypatch):
    from hermes_cli.interaction_host import InteractionHost
    from hermes_cli.interaction_config import resolve_interaction
    cancelled = []
    host = InteractionHost('s', 'owner', settings=resolve_interaction({}),
        auto_enabled=lambda: True, cancel_owned=lambda: cancelled.append('owned-only'))
    host.accept_task('task')
    request = host.lifecycle.open('next_action', ('continue', 'stop'), scope='task')
    host.pending = request
    token = host.lifecycle.delegate(request.request_id, host._authority, configured=True)
    assert token is not None
    host._control_claim = (request, 'Once', host.lifecycle.generation)
    assert host.accept(request, 'stop')
    assert host.stopped and cancelled == ['owned-only'] and host.lifecycle.mode == 'autonomous'
    assert not host.lifecycle.machine_result(token, 'continue', {'model': 'late-fixture'})
    assert host.completion('queued', 'task', lambda req: pytest.fail('Stop cannot be undone')) is None


def test_security_request_never_dispatches_configured_solver(monkeypatch):
    from hermes_cli.interaction_host import InteractionHost
    from hermes_cli import interaction_decider
    host = InteractionHost('s', 'owner', auto_enabled=lambda: True)
    request = host.lifecycle.open('permission', ('allow', 'deny'), scope='task')
    host.pending = request
    monkeypatch.setattr(interaction_decider, 'decide', lambda *args: pytest.fail('Security is nondelegable'))
    assert 'Nondelegable' in host.action(request.request_id, host.lifecycle.generation, 'once')
    assert host.interact(request, lambda req, offered: 'deny' if offered == ('allow', 'deny') else pytest.fail('Unexpected mode controls')) == 'deny'


@pytest.mark.parametrize('rejection', ['expired', 'scopeless', 'answered', 'ineligible'])
def test_rejected_always_leaves_native_yolo_disabled(monkeypatch, rejection):
    from hermes_cli.interaction_host import InteractionHost
    from hermes_cli import interaction_decider
    from tools import approval

    sid = 'rejected-always-fixture'
    monkeypatch.setattr(approval, '_YOLO_MODE_FROZEN', False)
    approval.disable_session_yolo(sid)
    enabled = []
    def enable():
        enabled.append(True)
        approval.enable_session_yolo(sid)
    host = InteractionHost(sid, 'owner', auto_enabled=lambda: approval.is_session_yolo_enabled(sid), enable_auto=enable)
    now = [0.0]
    host.lifecycle._clock = lambda: now[0]
    request = host.lifecycle.open('next_action', ('continue', 'stop'), scope='' if rejection == 'scopeless' else 'task')
    host.pending = request
    if rejection == 'expired':
        now[0] = request.deadline
    elif rejection == 'answered':
        assert host.lifecycle.answer(request.request_id, 'continue', host._authority)
    elif rejection == 'ineligible':
        host.lifecycle._eligible = lambda: False
    monkeypatch.setattr(interaction_decider, 'decide', lambda *args: pytest.fail('Rejected control must not call solver'))
    try:
        host.command(f'on {request.request_id} {host.lifecycle.generation}', '')
        assert not enabled
        assert not approval.is_session_yolo_enabled(sid)
    finally:
        approval.disable_session_yolo(sid)
