"""Synthetic JWT and completed Anthropic stream fixtures for auxiliary client tests."""
import base64
import json
from types import SimpleNamespace


def _jwt_with_claims(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


class _FakeAnthropicStream:
    def __init__(self, final_message):
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        # A completed Messages stream ends in message_stop (#121320 gate).
        yield SimpleNamespace(type="message_stop")

    def get_final_message(self):
        return self._final_message
