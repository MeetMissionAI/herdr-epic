"""The only module that runs hc, herdr-task and herdr-app."""
from __future__ import annotations

import json
import os
import subprocess

from herdr_epic import Fail


def hc(*args: str) -> dict | list:
    """Run `hc <args> --json` and parse stdout.

    A caller that ends with free text (hc run ... assistant <message>) passes --json itself, earlier.
    """
    return _run("hc", list(args) if "--json" in args else [*args, "--json"])


def task(*args: str) -> dict | list:
    """Run `herdr-task <args> --json` and parse stdout."""
    return _run("herdr-task", [*args, "--json"])


def app(*args: str) -> dict | list:
    """Run `herdr-app <args>` and parse stdout. herdr-app prints JSON without a flag."""
    return _run("herdr-app", list(args))


def app_do(command: str, **args) -> dict:
    """Run `herdr-app do <command> --<k> <v>...`. booleans, lists and dicts go as JSON; None values are skipped."""
    argv = ["do", command]
    for key, value in args.items():
        if value is None:
            continue
        if isinstance(value, (bool, list, dict)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        argv += [f"--{key}", text]
    return _run("herdr-app", argv)


def git(path: str, *args: str) -> str | None:
    """Stdout of `git -C <path> <args>`, or None when git fails or cannot run (not a repo, no upstream)."""
    try:
        proc = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def terminal(address: str) -> dict:
    return hc("get", address)


def caller() -> dict | None:
    address = os.environ.get("HC_TERMINAL_ID")
    return terminal(address) if address else None


def _run(binary: str, argv: list[str]):
    try:
        proc = subprocess.run([binary, *argv], capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL)
    except OSError as err:
        raise Fail(f"cannot run {binary}: {err}") from None
    out = proc.stdout.strip()
    try:
        data = json.loads(out) if out else None
    except json.JSONDecodeError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message") or json.dumps(error, ensure_ascii=False)
    if error:
        raise Fail(str(error))
    if proc.returncode != 0:
        raise Fail(proc.stderr.strip() or out or f"{binary} exited with {proc.returncode}")
    if data is None:
        raise Fail(f"{binary} printed no JSON: {out[:200]}")
    return data


def version(binary: str, *args: str) -> str | None:
    """First version-like word (starts with a digit) of `<binary> <args>` output; None when it cannot run."""
    try:
        out = subprocess.run([binary, *(args or ("--version",))], capture_output=True, text=True,
                             encoding="utf-8", stdin=subprocess.DEVNULL, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((word for word in out.split() if word[:1].isdigit()), None)
