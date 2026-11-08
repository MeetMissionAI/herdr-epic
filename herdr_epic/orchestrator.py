"""Find, wait for and start an epic's orchestrator; the `root add` and `new` commands."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

from herdr_epic import Fail, config, herdr, store
from herdr_epic.i18n import t

_TEMPLATES = Path(__file__).resolve().parent / "templates"
# Shepherdr's own list (mods/tasks/bin/herdr_task/base.py SHELLS), copied: an agent name in it is a bare shell.
_SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh", "csh", "nu", "xonsh", "pwsh", "powershell", "elvish"}


def _candidates() -> list[dict]:
    """Live local coordinator terminals as {address, agent, labels}."""
    rows = {}
    for args in (("ls", "--label", "herdr.role=coordinator"), ("ls",)):
        for row in herdr.hc(*args).get("terminals", []):
            labeled = len(args) > 1
            if labeled or row.get("template") in ("assistant", "coordinator"):
                rows[row["address"]] = row
    found = []
    for address, row in rows.items():
        if row.get("state") != "live" or "/" in address:
            continue
        got = herdr.terminal(address)
        program = ((got.get("foreground") or {}).get("argv") or [""])[0]
        found.append({"address": address, "agent": row.get("agent"), "labels": got.get("labels") or {},
                      "foreground": os.path.basename(program).lstrip("-")})
    return found


def _shell_only(term: dict) -> bool:
    """Nothing but a shell runs there. An agent still on a start screen has not reported itself yet
    (its agent reads as the shell that launched it), but its foreground program is already the CLI."""
    return term["agent"] in _SHELLS and (not term["foreground"] or term["foreground"] in _SHELLS)


def _in_space(term: dict, space: str | None) -> bool:
    labels = term["labels"]
    return bool(space) and space in (labels.get("herdr.coordinator.space"), labels.get("herdr.space"))


def space_terminals(epic: dict) -> list[str]:
    """Every local coordinator terminal of this epic's space, live, reviving or a stale shell."""
    return [term["address"] for term in _candidates() if _in_space(term, epic.get("space"))]


def find(epic: dict, exclude: tuple = ()) -> str | None:
    """Address of the live orchestrator for this epic's space, or None.

    A terminal running nothing but a shell is a revive in progress, not a live orchestrator.
    """
    for term in _candidates():
        if _in_space(term, epic["space"]) and not _shell_only(term) and term["address"] not in exclude:
            return term["address"]
    return None


def reviving(epic: dict, exclude: tuple = ()) -> str | None:
    """Address of this epic's orchestrator terminal that Shepherdr is about to revive."""
    for term in _candidates():
        if (_in_space(term, epic["space"]) and _shell_only(term)
                and term["labels"].get("herdr.resume") and term["address"] not in exclude):
            return term["address"]
    return None


def _shepherdr_settings() -> dict:
    path = Path(os.environ.get("HERDR_TASK_SETTINGS") or Path.home() / ".herdr" / "shepherdr.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def coordinator_pick(space: str) -> dict | None:
    """The person's Assistant choice for this space in Shepherdr settings, as the window reads it
    (lib/coordinator.js shepherdrFor): the space's, else this machine's, else Everywhere's; None if unset."""
    cfg = _shepherdr_settings()
    for layer in ((cfg.get("spaces") or {}).get(space), (cfg.get("machines") or {}).get("local"), cfg.get("everywhere")):
        pick = layer.get("coordinator") if isinstance(layer, dict) else None
        if pick:
            return pick
    return None


def agent_argv(pick: dict | None) -> list | None:
    """How Shepherdr starts an agent from a role's settings (herdr_task/settings.py argv_for), copied."""
    if not pick:
        return None
    if pick.get("argv"):
        return [str(x) for x in pick["argv"]]
    agent, perm = pick.get("agent"), pick.get("perm")
    if agent == "claude":
        a = ["claude"] + (["--model", pick["model"]] if pick.get("model") else []) \
            + (["--effort", pick["effort"]] if pick.get("effort") else [])
        a += ["--settings", json.dumps({"fastMode": bool(pick.get("fast"))})]
        if perm == "bypassPermissions":
            a.append("--dangerously-skip-permissions")
        elif perm:
            a += ["--permission-mode", "default" if perm == "manual" else perm]
        return a
    if agent == "codex":
        a = ["codex", "--no-daemon"] + (["-m", pick["model"]] if pick.get("model") else [])
        if pick.get("effort"):
            a += ["-c", f'model_reasoning_effort="{pick["effort"]}"']
        if pick.get("fast"):
            a += ["-c", 'service_tier="fast"']
        if perm == "full":
            a.append("--dangerously-bypass-approvals-and-sandbox")
        elif perm == "read-only":
            a += ["-s", "read-only", "-a", "on-request"]
        elif perm:
            a += ["-s", "workspace-write", "-a", "on-request"]
        return a
    return [agent] if agent else None


def one_off_template(pick: dict | None) -> Path | None:
    """A herdr-core template that is Shepherdr's assistant.md with the person's command, as the
    window makes one (mods/chat/main.js); the caller removes it once hc has started the agent.
    None when there is no choice to apply or no assistant template to copy."""
    argv = agent_argv(pick)
    folder = Path(os.environ.get("HERDR_TASK_TEMPLATES") or Path.home() / ".config" / "herdr-core" / "templates")
    source = folder / "assistant.md"
    if not argv or not source.is_file():
        return None
    text = source.read_text(encoding="utf-8")
    head = re.match(r"---\n.*?\n---\n?", text, re.S)
    name = f"herdr-epic-assistant-{secrets.token_hex(4)}"
    path = folder / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: A one-off Assistant start with the person's Shepherdr "
                    f"settings (herdr-epic), removed after use.\ncommand: {shlex.join(argv)}\n---\n"
                    + (text[head.end():] if head else text), encoding="utf-8")
    return path


def _space_alive(space: str | None) -> bool:
    return bool(space) and any(s.get("id") == space for s in herdr.app("ls").get("spaces", []))


def ensure_space(epic: dict) -> dict:
    """Make sure the epic's space exists; create and record a new one if not.

    A closed epic (state done) is refused: it never comes back through a request, a task or `show`.
    """
    if epic.get("state") == "done":
        raise Fail(t("epic.closed", config.load()["language"]))
    if _space_alive(epic.get("space")):
        return epic
    new = herdr.app_do("space.new", name=epic["title"], folder=epic["folder"])["space"]

    def replace(e):
        # Tasks started earlier keep the old space in their labels; by_space still finds the epic by it.
        if e.get("space"):
            e["former_spaces"] = [*(e.get("former_spaces") or []), e["space"]]
        e["space"] = new

    return store.update(Path(epic["folder"]), replace)


def _slot(space: str, wait_s: float) -> dict | None:
    """Shepherdr's empty Assistant chat slot for this space, read from Herdr's shared.json."""
    home = Path(os.environ.get("HERDR_APP_HOME") or "~/.config/herdr-app").expanduser()
    deadline = time.monotonic() + wait_s
    while True:
        try:
            slots = json.loads((home / "shared.json").read_text(encoding="utf-8")).get("coordinator.slots") or {}
        except (OSError, ValueError):
            slots = {}
        for slot_id, slot in slots.items():
            if isinstance(slot, dict) and slot.get("space") == space:
                return {"id": slot_id, "tab": slot.get("tab"), "pane": slot.get("pane")}
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


# Every fresh orchestrator is told where its rules are: Claude Code reads them through the root's
# CLAUDE.md link, but another CLI (or a person's own CLAUDE.md) may not load them by itself.
_RULES_HINT = ("You are this epic's orchestrator. Your rules are in ../AGENTS.md (the Orchestrator section): "
               "read it first if it is not already in your context.")


def ensure(epic: dict, first_message: str, wait_s: int = 90, exclude: tuple = ()) -> dict:
    """Return {address, started, sent, error}. Start failures are reported, never raised.

    sent: hc run confirmed that first_message reached the started Assistant. It is False when an
    existing orchestrator was found: then nothing was sent and the caller delivers its own message.
    exclude: terminals not to count as the orchestrator (ones just killed).
    """
    try:
        before = epic.get("space")
        epic = ensure_space(epic)
        address = find(epic, exclude)
        if address:
            return {"address": address, "started": False, "sent": False, "error": None}
        if reviving(epic, exclude):
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                time.sleep(1)
                address = find(epic, exclude)
                if address:
                    return {"address": address, "started": False, "sent": False, "error": None}
            return {"address": None, "started": False, "sent": False,
                    "error": f"orchestrator is reviving but did not come back within {wait_s}s"}
        space = epic["space"]
        # A just-created space gets its chat slot within about a second.
        slot = _slot(space, 3 if epic["space"] != before else 0)
        labels = {"herdr.role": "coordinator", "herdr.coordinator.space": space}
        if slot:
            labels.update({"herdr.chat": slot["pane"], "herdr.chat.slot": slot["id"],
                           "herdr.chat.origin": f"slot:{slot['id']}", "herdr.coordinator.tab": slot["tab"]})
        argv = ["run", "--bg", "--json", "--no-parent", "--keep", "--cwd", epic["folder"]]
        for key, value in labels.items():
            argv += ["--label", f"{key}={value}"]
        template = one_off_template(coordinator_pick(space))
        try:
            started = herdr.hc(*argv, template.stem if template else "assistant", f"{_RULES_HINT}\n\n{first_message}")
        finally:
            if template:
                template.unlink(missing_ok=True)
        if not started.get("address"):
            return {"address": None, "started": False, "sent": False, "error": "hc run printed no address"}
        return {"address": started["address"], "started": True,
                "sent": (started.get("first_message") or {}).get("sent") is True, "error": None}
    except (Fail, OSError) as err:
        return {"address": None, "started": False, "sent": False, "error": str(err)}


def parse_opts(argv: list[str], valued: tuple) -> tuple[list[str], dict]:
    positional, opts, i = [], {name: [] for name in valued}, 0
    while i < len(argv):
        if argv[i] in opts:
            if i + 1 >= len(argv):
                raise Fail(f"{argv[i]} needs a value")
            opts[argv[i]].append(argv[i + 1])
            i += 2
        elif argv[i].startswith("--"):
            raise Fail(f"unknown option: {argv[i]}")
        else:
            positional.append(argv[i])
            i += 1
    return positional, opts


def read_file(name: str) -> str:
    """The text of a --context-file or --prompt-file argument. Fail when it is missing or not UTF-8."""
    path = Path(name)
    if not path.is_file():
        raise Fail(f"no such file: {name}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise Fail(f"{name} is not UTF-8 text ({err.reason} at byte {err.start})") from None


def parse_repo(spec: str) -> dict:
    """NAME=PATH[@BASE] -> {name, path (absolute), base}."""
    name, sep, rest = spec.partition("=")
    if not sep or not name or not rest:
        raise Fail(f"repo must look like NAME=PATH[@BASE]: {spec}")
    path, at, base = rest.rpartition("@")
    if not at or "/" in base or not path:
        path, base = rest, None
    return {"name": name, "path": str(Path(path).expanduser().resolve()), "base": base or None}


def refresh_agents(root: Path, meta: dict, dry_run: bool = False) -> str:
    """Bring the root's AGENTS.md up to herdr-epic's template; return what happened.

    "wrote" (there was none), "same", "updated" (unchanged since herdr-epic wrote it, by the sha256
    kept in meta["agents_sha256"]), or "kept": the person's edited file stays and the template goes to
    AGENTS.md.new. meta gets the template's sha256 unless the file was kept.
    """
    body = (_TEMPLATES / "root-AGENTS.md").read_text(encoding="utf-8")
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    agents = root / "AGENTS.md"
    have = hashlib.sha256(agents.read_bytes()).hexdigest() if agents.is_file() else None
    outcome = ("wrote" if have is None else "same" if have == sha
               else "updated" if have == meta.get("agents_sha256") else "kept")
    if not dry_run and outcome != "same":
        (root / ("AGENTS.md.new" if outcome == "kept" else "AGENTS.md")).write_text(body, encoding="utf-8")
    if outcome != "kept":
        meta["agents_sha256"] = sha
    return outcome


_LEGACY_CLAUDE = "@AGENTS.md\n"  # what herdr-epic 0.1.0 wrote; an import Claude Code calls external below the root


def link_claude(root: Path, dry_run: bool = False) -> str:
    """Make the root's CLAUDE.md a link to AGENTS.md, so the rules have one source; return what happened.

    "same" (already the link), "linked" (there was none), "replaced" (it was the old one-line import),
    or "kept": the person's own CLAUDE.md stays untouched.
    """
    claude = root / "CLAUDE.md"
    if claude.is_symlink() and os.readlink(claude) == "AGENTS.md":
        return "same"
    if claude.is_symlink() or claude.exists():
        if claude.is_symlink() or not claude.is_file() or claude.read_bytes() != _LEGACY_CLAUDE.encode():
            return "kept"
        outcome = "replaced"
    else:
        outcome = "linked"
    if not dry_run:
        if outcome == "replaced":
            claude.unlink()
        claude.symlink_to("AGENTS.md")
    return outcome


def cmd_root_add(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    positional, opts = parse_opts(argv, ("--name", "--repo"))
    if len(positional) != 1:
        raise Fail("usage: herdr-epic root add PATH [--name NAME] [--repo NAME=PATH[@BASE]]...")
    path = Path(positional[0]).expanduser().resolve()
    name = opts["--name"][-1] if opts["--name"] else path.name
    repos = [parse_repo(spec) for spec in opts["--repo"]]
    others = [r for r in cfg["roots"] if Path(r["path"]).resolve() != path]
    if any(r["name"] == name for r in others):
        raise Fail(f"root name already in use: {name}")
    path.mkdir(parents=True, exist_ok=True)

    meta_path = path / "root.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    known = {repo["name"] for repo in meta.get("repos", [])}
    meta = {"name": name, "space": meta.get("space"),
            "repos": meta.get("repos", []) + [r for r in repos if r["name"] not in known],
            **({"agents_sha256": meta["agents_sha256"]} if meta.get("agents_sha256") else {})}

    notes = []
    if refresh_agents(path, meta) == "kept":
        notes.append(t("root.agents_differs", cfg["language"], path=str(path / "AGENTS.md.new")))
    if link_claude(path) == "kept":
        notes.append(t("root.claude_differs", cfg["language"], path=str(path / "CLAUDE.md")))

    if not _space_alive(meta["space"]):
        meta["space"] = herdr.app_do("space.new", name=name, folder=str(path))["space"]
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    default = Path("~/.herdr/epics").expanduser()
    if [r["path"] for r in others] == [str(default)] and not default.is_dir():
        others = []
    config.save({**cfg, "roots": others + [{"name": name, "path": str(path)}]})

    out = {"root": {**meta, "path": str(path)}, "notes": notes}
    print(json.dumps(out, ensure_ascii=False) if as_json else "\n".join([f"{name}: {path}", *notes]))
    return 0


def cmd_new(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    positional, opts = parse_opts(argv, ("--root", "--repo", "--message"))
    if len(positional) != 1:
        raise Fail("usage: herdr-epic new TITLE [--root NAME] --repo NAME=PATH[@BASE]... [--message TEXT]")
    title = positional[0]
    if opts["--root"]:
        roots = [r for r in cfg["roots"] if r["name"] == opts["--root"][-1]]
        if not roots:
            raise Fail(f"unknown root: {opts['--root'][-1]}")
    elif len(cfg["roots"]) == 1:
        roots = cfg["roots"]
    else:
        raise Fail("several roots; pass --root NAME. Roots: " + ", ".join(r["name"] for r in cfg["roots"]))
    repos = [parse_repo(spec) for spec in opts["--repo"]]
    for repo in repos:
        if not (Path(repo["path"]) / ".git").exists():
            raise Fail(f"not a git repository: {repo['name']} ({repo['path']})")

    lang = cfg["language"]
    epic = store.create(Path(roots[0]["path"]), title, repos)
    message = opts["--message"][-1] if opts["--message"] else t("new.opening", lang, title=title)
    result = ensure(epic, message)
    error, extra = result["error"], {}
    if result["started"] and not result["sent"]:
        # The Assistant runs but never got the opening message: keep it as a queued request, so that
        # `herdr-epic pending` (which the orchestrator's rules run on start) brings it up.
        request = {"id": None, "from_terminal": os.environ.get("HC_TERMINAL_ID"), "from_task": None, "repo": None,
                   "agent": None, "context": message, "state": "queued",
                   "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "orchestrator": result["address"], "error": t("request.not_received", lang)}
        store.add_request(Path(epic["folder"]), request)
        error = t("request.saved_not_delivered", lang, id=request["id"], title=title, error=request["error"])
        extra = {"request": request["id"], "saved": True}
    epic = store.load(Path(epic["folder"]))
    if error:
        if as_json:
            print(json.dumps({"error": error, "epic": epic, "orchestrator": result, **extra}, ensure_ascii=False))
        else:
            print(f"{epic['id']} {title}: {epic['folder']}")
            print(error, file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps({"epic": epic, "orchestrator": result}, ensure_ascii=False))
    else:
        print(f"{epic['id']} {title}: {epic['folder']}")
        print(f"orchestrator: {result['address']}")
    return 0


def cmd_restart(argv: list[str], as_json: bool) -> int:
    """Replace an epic's orchestrator (stuck on a screen, closed, or misbehaving) with a fresh one,
    which starts by handling `herdr-epic pending`. Run by the person, the root assistant or a worker."""
    from herdr_epic import who

    cfg = config.load()
    positional, _ = parse_opts(argv, ())
    if len(positional) > 1:
        raise Fail("usage: herdr-epic restart [EPIC]")
    me = who.resolve(cfg, herdr.caller(), Path.cwd())
    epic = store.find(cfg, positional[0]) if positional else me["epic"]
    if not epic:
        raise Fail("usage: herdr-epic restart EPIC (see herdr-epic list)")
    lang = cfg["language"]
    if epic.get("state") == "done":
        raise Fail(t("epic.closed", lang))
    targets = space_terminals(epic)
    if os.environ.get("HC_TERMINAL_ID") in targets:
        raise Fail(t("restart.self", lang))
    killed = []
    for address in targets:
        herdr.hc("kill", address)
        herdr.hc("rm", address)
        killed.append(address)
    result = ensure(epic, t("restart.opening", lang, title=epic["title"]), exclude=tuple(killed))
    out = {"epic": epic["id"], "killed": killed, "orchestrator": result}
    error = result["error"] or (None if result["sent"] else t("restart.not_received", lang))
    if as_json:
        print(json.dumps({**out, **({"error": error} if error else {})}, ensure_ascii=False))
    else:
        print(f"{epic['id']}: stopped {', '.join(killed) or 'nothing'}; orchestrator {result['address'] or '-'}")
        if error:
            print(error, file=sys.stderr)
    return 1 if error else 0
