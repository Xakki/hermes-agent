"""Validated owner-profile settings, frozen per interaction rather than cached globally."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re

from hermes_cli.interaction_defaults import INTERACTION_DEFAULTS, INTERACTION_MAX_OUTPUT_TOKENS


@dataclass(frozen=True)
class InteractionAction:
    id: str
    label: str
    description: str
    enabled: bool
    handler: str
    prompt: str


@dataclass(frozen=True)
class InteractionSettings:
    decision_profile: str
    wait_timeout_seconds: int
    reminder_after_seconds: int
    decision_timeout_seconds: int
    max_attempts: int
    max_output_tokens: int
    show_countdown: bool
    decision_prompt: str
    task_sources: tuple[str, ...]
    menu: tuple[InteractionAction, ...]

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _text(value, name, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"Invalid interaction {name}")
    return value


def resolve_interaction(config=None):
    if config is None:
        from hermes_cli.config_effective import load_user_config_effective
        config = load_user_config_effective(fail_closed=True)
    raw = config.get('interaction', {})
    if not isinstance(raw, dict) or set(raw) - set(INTERACTION_DEFAULTS):
        raise ValueError('Unknown or invalid interaction settings')
    values = {**INTERACTION_DEFAULTS, **raw}
    from hermes_cli.profiles import validate_profile_name
    validate_profile_name(_text(values['decision_profile'], 'decision_profile', 64))
    for name, ceiling in [('wait_timeout_seconds', 600), ('reminder_after_seconds', 599),
                          ('decision_timeout_seconds', 600), ('max_attempts', 3),
                          ('max_output_tokens', INTERACTION_MAX_OUTPUT_TOKENS)]:
        if type(values[name]) is not int or not 0 < values[name] <= ceiling:
            raise ValueError(f'Invalid interaction {name}')
    if values['reminder_after_seconds'] >= values['wait_timeout_seconds']:
        raise ValueError('Reminder must precede human expiry')
    if type(values['show_countdown']) is not bool:
        raise ValueError('Invalid show_countdown')
    _text(values['decision_prompt'], 'decision_prompt', 2048)
    sources = values['task_sources']
    if (not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= 8
            or any(not isinstance(s, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', s) for s in sources)
            or len(set(sources)) != len(sources)):
        raise ValueError('Task sources must be unique project-local names')
    raw_menu = values['menu']
    if not isinstance(raw_menu, (list, tuple)) or len(raw_menu) > 8:
        raise ValueError('Menu must be finite')
    menu, ids = [], set()
    handlers = {'continue': 'continue_current', 'new_task': 'discover_task', 'stop': 'stop'}
    for row in raw_menu:
        if not isinstance(row, dict) or set(row) - {'id', 'label', 'description', 'enabled', 'handler', 'prompt'}:
            raise ValueError('Invalid menu fields')
        action_id = row.get('id')
        if (not isinstance(action_id, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', action_id)
                or action_id in ids or action_id in {'once', 'always', 'on', 'off', 'yolo'}):
            raise ValueError('Duplicate, reserved or invalid action id')
        ids.add(action_id)
        handler = row.get('handler', 'prompt')
        if handler != handlers.get(action_id, 'prompt'):
            raise ValueError('Native handlers cannot be overridden or aliased')
        enabled = row.get('enabled', True)
        if type(enabled) is not bool:
            raise ValueError('Invalid action enabled')
        prompt = _text(row.get('prompt', ''), 'prompt', 4096, empty=action_id == 'stop')
        if action_id == 'stop' and prompt:
            raise ValueError('Stop cannot invoke a prompt')
        menu.append(InteractionAction(action_id, _text(row.get('label', action_id), 'label', 128),
            _text(row.get('description', ''), 'description', 512, empty=True),
            True if action_id == 'stop' else enabled, handler, prompt))
    if 'stop' not in ids:
        menu.append(InteractionAction(**INTERACTION_DEFAULTS['menu'][-1]))
    if len(menu) > 8:
        raise ValueError('Menu must leave room for native Stop')
    return InteractionSettings(**{key: values[key] for key in values if key not in {'menu', 'task_sources'}},
                               task_sources=tuple(sources), menu=tuple(menu))
