"""Explicit, time-bounded next actions for an idle local conversation."""
from dataclasses import dataclass
from functools import partial
import re
import time
from uuid import uuid4

from gateway.owner_identity import same_owner
from persistence.core.errors import AuthorityRefusal

WAIT_SECONDS = 600
MAX_CUSTOM_ACTIONS = 8
_RESERVED = frozenset({'continue', 'review_changes', 'close', 'stop', 'new_task', 'once', 'always'})
_CONTINUE = 'Continue remaining work in the accepted current task. Respect its existing scope and approvals. If complete, report that once and wait.'


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    description: str = ''
    prompt: str = ''


@dataclass(frozen=True)
class Offer:
    id: str
    principal: str
    generation: int
    revision: int
    deadline: float
    actions: tuple[Action, ...]


def _one_line(value, limit):
    return isinstance(value, str) and len(value) <= limit and all(char.isprintable() for char in value)


def _valid_prompt(value):
    # The ordinary gateway turn can recognize a command in leading position.
    # A menu prompt must never enter that path, even after leading whitespace.
    return (isinstance(value, str) and 0 < len(value) <= 4000 and bool(value.strip())
            and not value.lstrip().lower().startswith(('/', '!', '@image:', '@file:', '@url:'))
            and all(char.isprintable() or char in '\n\t' for char in value))


def menu(config):
    actions = [Action('continue', 'Continue', 'Resume remaining accepted work', _CONTINUE),
               Action('review_changes', 'Show changes', 'Show the current diff')]
    entries = config.get('menu')
    seen = set(_RESERVED)
    for entry in entries[:32] if isinstance(entries, list) else ():
        if len(actions) == MAX_CUSTOM_ACTIONS + 2:
            break
        if not isinstance(entry, dict) or entry.get('enabled', True) is not True or entry.get('handler') != 'prompt':
            continue
        key, label = entry.get('id'), entry.get('label')
        description, prompt = entry.get('description', ''), entry.get('prompt')
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', key) or key in seen:
            continue
        if not isinstance(label, str) or not label.strip() or not _one_line(label, 80) or not _one_line(description, 160):
            continue
        if not isinstance(prompt, str) or not _valid_prompt(prompt):
            continue
        seen.add(key)
        actions.append(Action(key, label, description, prompt))
    return (*actions, Action('close', 'Close', 'Dismiss without taking action'))


def candidate(live, row, settled, response):
    """Remember only a committed conversational success, not automation or our own follow-up."""
    from gateway.config import Platform
    payload = row['payload']
    if (live.source is not None and live.source.platform == Platform.LOCAL
            and same_owner(live.source.user_id, row['principal_id'])
            and settled['outcome'] == 'completed' and isinstance(response, str) and response.strip()
            and not row['request_id'].startswith('next-action:')
            and not set(payload) - {'text', 'attachments_v1', 'cli_turn_v1', 'display_v1'}):
        live.next_action_candidate = (row['principal_id'], row['generation'])


def _config(authority):
    from pathlib import Path
    from gateway.run import _profile_runtime_scope
    from hermes_cli.config import load_config
    with _profile_runtime_scope(Path(authority.profile_id), hydrate_secrets=False):
        value = load_config().get('interaction')
    return value if isinstance(value, dict) else {}


def offer_idle(authority, ref, live):
    pending, live.next_action_candidate = live.next_action_candidate, None
    if pending is None or authority.sessions.get(ref.session_id) is not live or not live.subscribers:
        return
    principal, generation = pending
    handle = authority._handle(ref)
    if handle.execution_state != 'idle' or handle.execution_generation != generation:
        return
    config = _config(authority)
    actions = menu(config)
    offer = Offer(uuid4().hex, principal, generation, handle.revision, time.monotonic() + WAIT_SECONDS, actions)
    live.next_action_offer = offer
    live.event_stream.publish(ref.session_id, {
        'id': offer.id,
        'deadline_ms': int(time.time() * 1000) + WAIT_SECONDS * 1000,
        'show_countdown': config.get('show_countdown') is not False,
        'actions': [{'id': a.id, 'label': a.label, 'description': a.description} for a in actions],
    }, event_type='interaction.next_action')


def _take(connection, ref, live, offer_id, action_id):
    authority, actor = connection.authority, connection.actor
    if (authority.sessions.get(ref.session_id) is not live or ref.session_id not in connection.subscriptions):
        raise AuthorityRefusal('permission_denied')
    authority.authorize(actor, ref, 'session:respond')
    offer = live.next_action_offer
    if offer is None or offer.id != offer_id or time.monotonic() >= offer.deadline:
        raise AuthorityRefusal('stale_generation')
    if not same_owner(offer.principal, actor.subject):
        raise AuthorityRefusal('permission_denied')
    handle = authority._handle(ref)
    if (handle.execution_generation != offer.generation or handle.revision != offer.revision
            or handle.execution_state != 'idle' or authority._pending_rows(ref)):
        raise AuthorityRefusal('stale_generation')
    action = next((action for action in offer.actions if action.id == action_id), None)
    if action is None:
        raise AuthorityRefusal('invalid_params')
    live.next_action_offer = None
    live.event_stream.publish(ref.session_id, {'id': offer.id}, event_type='interaction.next_action_closed')
    return action


async def respond(connection, ref, params):
    authority, actor = connection.authority, connection.actor
    authority.authorize(actor, ref, 'session:respond')
    live = authority.sessions[ref.session_id]
    async with live.mutation_lock:
        action = _take(connection, ref, live, params['offer_id'], params['action_id'])
        if action.prompt:
            from gateway.session_ingress_media import capture_lease
            authority.authorize(actor, ref, 'session:submit')
            request = connection.submission(ref, {'submission_id': f"next-action:{params['offer_id']}",
                                                  'text': action.prompt, 'clarify_batch': True})
            # Already holding the ordinary submit mutation lock; do not release it between
            # consuming the offer and admitting the prompt, or typed input can overtake it.
            with capture_lease():
                await authority._submit(actor, request)
            return {}
    if action.id == 'review_changes':
        from gateway.session_commands import execute_command
        result = await execute_command(connection, ref, {'command': 'diff'})
        return {'output': result.get('output', '')}
    return {}


def handlers(connection):
    return {'interaction.next_action.respond': partial(respond, connection)}
