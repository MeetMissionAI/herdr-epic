from __future__ import annotations

import json
import sys

from herdr_epic import Fail
from herdr_epic import config
from herdr_epic.i18n import t

# Full command path ("task add") -> handler(argv, as_json) -> int.
# argv is what follows the command words; "--json" is not included.
_commands = {}


def add_command(name: str, handler) -> None:
    """Register a subcommand. `name` may be several words, e.g. "task add"."""
    _commands[name] = handler


def subcommand_names() -> set[str]:
    """Every registered command path, plus the first word of each path."""
    names = set()
    for name in _commands:
        names.add(name)
        names.add(name.split()[0])
    return names


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [arg for arg in argv if arg != "--json"]
    try:
        return _run(args, as_json)
    except Fail as err:
        return _fail(str(err), as_json)


def _run(args: list[str], as_json: bool) -> int:
    if not args:
        raise Fail(t("error.usage", _lang()))
    name = _match(args)
    if name is None:
        raise Fail(t("error.unknown_command", _lang(), name=args[0]))
    parts = name.split()
    return _commands[name](args[len(parts):], as_json)


def _match(args: list[str]) -> str | None:
    best = None
    best_len = 0
    for name in _commands:
        parts = name.split()
        if args[:len(parts)] == parts and len(parts) > best_len:
            best = name
            best_len = len(parts)
    return best


def _lang() -> str:
    return config.load()["language"]


def _fail(message: str, as_json: bool) -> int:
    if as_json:
        sys.stdout.write(json.dumps({"error": message}, ensure_ascii=False) + "\n")
    else:
        print(message, file=sys.stderr)
    return 1


def _register() -> None:
    from herdr_epic import board, install, orchestrator, relay, tasks

    add_command("root add", orchestrator.cmd_root_add)
    add_command("new", orchestrator.cmd_new)
    add_command("restart", orchestrator.cmd_restart)
    add_command("task add", tasks.cmd_task_add)
    add_command("repo add", tasks.cmd_repo_add)
    add_command("board", board.cmd_board)
    add_command("list", board.cmd_list)
    add_command("show", board.cmd_show)
    add_command("close", board.cmd_close)
    add_command("archive", board.cmd_archive)
    add_command("unarchive", board.cmd_unarchive)
    add_command("root show", board.cmd_root_show)
    add_command("request", relay.cmd_request)
    add_command("reply", relay.cmd_reply)
    add_command("pending", relay.cmd_pending)
    add_command("whoami", relay.cmd_whoami)
    add_command("install", install.cmd_install)
    add_command("uninstall", install.cmd_uninstall)
    add_command("doctor", install.cmd_doctor)


_register()
