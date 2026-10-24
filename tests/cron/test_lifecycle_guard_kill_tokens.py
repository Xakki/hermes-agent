"""Killer verbs are complete tokens, not fragments of options or helper names."""

import pytest

from cron.lifecycle_guard import contains_gateway_lifecycle_command, scan_gateway_lifecycle


@pytest.mark.parametrize("command", [
    "timeout --signal=TERM --kill-after=20s 2100s hermes chat --in /work/gateway-service",
    "timeout --kill-after 20s 2100s hermes chat --in /work/gateway-service",
    "python3 /opt/capture.py -- timeout --kill-after=20s 2100s hermes --cli herm-prof reviewer chat --in /work/gateway-service",
    "helper --kill=TERM hermes gateway",
    "helper --pkill hermes gateway",
    "helper --taskkill hermes gateway",
    "helper --stop-process hermes gateway",
    "safe-kill-helper hermes gateway",
])
def test_killer_word_fragments_are_not_lifecycle_commands(command, tmp_path):
    assert contains_gateway_lifecycle_command(command) is False
    assert scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (False, None)


@pytest.mark.parametrize("command", [
    "pkill -f 'hermes gateway'",
    "pkill -f 'gateway hermes'",
    "kill $(pgrep -f 'hermes gateway')",
    "/bin/kill $(pgrep -f 'gateway hermes')",
    "/usr/bin/pkill -f 'hermes gateway'",
    "taskkill /F /IM hermes-gateway.exe",
    "taskkill.exe /F /IM gateway-hermes.exe",
    r'"C:\Windows\System32\taskkill.exe" /F /IM hermes-gateway.exe',
    "Stop-Process -Name gateway-hermes -Force",
    "sudo timeout --kill-after=20s 30s pkill -f 'hermes gateway'",
    'p"kill" -f "gateway hermes"',
    "sh -c 'pkill -f \"hermes gateway\"'",
])
def test_real_gateway_killers_remain_blocked(command, tmp_path):
    assert contains_gateway_lifecycle_command(command) is True
    assert scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (True, None)
