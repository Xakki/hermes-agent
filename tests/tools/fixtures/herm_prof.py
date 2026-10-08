"""Run a Hermes CLI command in another profile with a minimal environment."""

import argparse
import os


def _setup_parser(parser):
    parser.add_argument("profile", help="Hermes profile name")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="Hermes CLI arguments")


def _run(args):
    if not args.args:
        raise SystemExit("Usage: hermes herm-prof <profile> <Hermes CLI args...>")

    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "TERM": os.environ.get("TERM") or "dumb",
        "TMPDIR": "/var/tmp",
    }
    from tools.approval import is_approval_bypass_active

    if is_approval_bypass_active():
        clean_env["HERMES_YOLO_MODE"] = "1"
    argv = [
        "env",
        "-i",
        *(f"{key}={value}" for key, value in clean_env.items()),
        "hermes",
        "-p",
        args.profile,
        *args.args,
    ]
    os.execv("/usr/bin/env", argv)


def register(ctx):
    ctx.register_cli_command(
        name="herm-prof",
        help="Run Hermes CLI arguments in a selected profile with a clean environment",
        setup_fn=_setup_parser,
        handler_fn=_run,
        description="Run Hermes CLI arguments in a selected profile with a clean environment",
    )
