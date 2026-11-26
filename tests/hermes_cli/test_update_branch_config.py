"""Profile branch selection reaches the stock check and apply routes."""
import argparse
from pathlib import Path

import pytest

from hermes_cli import config, main, update_cmd, update_cmd_check
from hermes_cli.subcommands.update import build_update_parser


@pytest.fixture
def parse_update(monkeypatch):
    parser = argparse.ArgumentParser()
    build_update_parser(parser.add_subparsers(), cmd_update=main.cmd_update)
    monkeypatch.setattr("hermes_cli.update_contract.evaluate_update_admission", lambda _root: None)
    monkeypatch.setattr("hermes_cli.update_channel.handle_metadata_args", lambda *_: False)
    monkeypatch.setattr(config, "is_managed", lambda: False)
    return lambda argv: parser.parse_args(["update", *argv])


@pytest.mark.parametrize(
    "updates, argv, expected, selected, channel",
    [
        ({}, [], "main", False, None),
        ({"branch": None}, [], "main", False, None),
        ({"branch": "  "}, [], "main", False, None),
        ({"branch": " topic/custom "}, [], "topic/custom", True, None),
        ({"branch": "main"}, [], "main", True, None),
        ({"branch": "topic/custom"}, ["--branch", "other"], "other", True, None),
        ({"branch": "topic/custom"}, ["--branch", "  "], "main", True, None),
        ({"branch": "topic/custom"}, ["--channel", "stable"], "main", False, "stable"),
    ],
)
def test_check_uses_profile_branch_without_changing_channel_selection(
    parse_update, monkeypatch, updates, argv, expected, selected, channel,
):
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": updates})
    checks = []
    monkeypatch.setattr(update_cmd, "_cmd_update_check", lambda **kwargs: checks.append(kwargs))
    assert main._update_preflight_handled(parse_update(["--check", *argv]))
    assert checks == [{"branch": expected, "branch_explicit": selected,
                       **({"channel": channel} if channel else {})}]


def test_configured_branch_reaches_apply_target(parse_update, monkeypatch):
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": {"branch": "topic/custom"}})
    args = parse_update(["--no-gateway-restart"])
    assert not main._update_preflight_handled(args)
    assert args.no_gateway_restart
    branch = main._resolve_update_branch(args)
    assert update_cmd_check.select_apply_target(
        args, branch, {"home": str(Path.cwd())}, git_cmd=None, stop=lambda: pytest.fail("update stopped"),
    ) == ("origin/topic/custom", None, False, None)


@pytest.mark.parametrize("value", [False, ["main"], "bad:branch", "topic/*", "-topic"])
def test_invalid_config_branch_refuses_before_dispatch(parse_update, monkeypatch, capsys, value):
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": {"branch": value}})
    monkeypatch.setattr(update_cmd, "_cmd_update_check", lambda **_: pytest.fail("check dispatched"))
    with pytest.raises(SystemExit) as exc:
        main._update_preflight_handled(parse_update(["--check"]))
    assert exc.value.code == 2
    assert "updates.branch" in capsys.readouterr().out
