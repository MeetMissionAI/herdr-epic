"""Install, uninstall and doctor. Every path hangs off the `home` argument."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path
import shutil

from herdr_epic import Fail, VERSION
from herdr_epic import config, herdr, orchestrator

_REPO = Path(__file__).resolve().parents[1]
_HERDR_FILES = (".d.ts", "jsconfig.json")
_SIDEBAR_ID = "epics-sidebar"


def _canonical_sha(layout: dict) -> str:
    text = json.dumps(layout, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Blocks Herdr points at whatever is shown while the layout is in use (Shepherdr's installer calls
# them LIVE_BLOCKS): a layout is compared with their content blanked, so one in use is not "edited".
_LIVE_BLOCKS = ("tasks-thread-term", "artifacts-view-body")


def _fingerprint(rel: str, data: bytes) -> str:
    """What install and uninstall compare: a layout without its live blocks, any other file as is."""
    if not rel.startswith(".config/herdr-app/layouts/") or not rel.endswith(".json"):
        return _sha(data)
    try:
        layout = json.loads(data)
    except ValueError:
        return _sha(data)

    def blank(node):
        if isinstance(node, dict):
            if node.get("id") in _LIVE_BLOCKS:
                node["content"] = None
            for value in node.values():
                blank(value)
        elif isinstance(node, list):
            for value in node:
                blank(value)
    blank(layout)
    return _canonical_sha(layout)


def generate_layout(shepherdr_layout: dict) -> dict:
    """Wrap the spaces-chat block in a stack of by-epic, by-repo and the original spaces view."""
    layout = copy.deepcopy(shepherdr_layout)
    found = []

    def walk(node):
        for index, child in enumerate(node.get("children", [])):
            if child.get("content", {}).get("mod") == "spaces-chat":
                found.append((node["children"], index))
            else:
                walk(child)

    walk(layout)
    if len(found) != 1:
        raise Fail(f"expected exactly one spaces-chat block in the Shepherdr layout, found {len(found)}")
    siblings, index = found[0]
    spaces = siblings[index]
    stack = {"id": _SIDEBAR_ID, "type": "stack", "active": "epics-by-epic"}
    for key in ("size", "min", "max"):
        if key in spaces:
            stack[key] = spaces.pop(key)
    stack["header"] = {"content": {"mod": "epics", "props": {"view": "switch"}}, "height": 44}
    spaces["id"] = "epics-spaces"
    stack["children"] = [
        {"id": "epics-by-epic", "type": "block", "content": {"mod": "epics", "props": {"view": "epic"}}},
        {"id": "epics-by-repo", "type": "block", "content": {"mod": "epics", "props": {"view": "repo"}}},
        spaces,
    ]
    siblings[index] = stack
    layout["cloned_from"] = {
        "name": "shepherdr",
        "version": "unknown",
        "sha256": _canonical_sha(shepherdr_layout),
    }
    return layout


def _paths(home: Path) -> dict:
    app = home / ".config" / "herdr-app"
    state = home / ".config" / "herdr-epic"
    return {
        "app": app,
        "mod": app / "mods" / "epics",
        "layout": app / "layouts" / "shepherdr-epics.json",
        "source_layout": app / "layouts" / "shepherdr.json",
        "link": home / ".local" / "bin" / "herdr-epic",
        "state_dir": state,
        "state": state / "installed.json",
    }


def _app_home_problem(home: Path) -> str | None:
    """Why HERDR_APP_HOME rules this install out, or None: v0.1 installs into ~/.config/herdr-app only."""
    env = os.environ.get("HERDR_APP_HOME")
    if not env or os.path.realpath(os.path.expanduser(env)) == os.path.realpath(str(home / ".config" / "herdr-app")):
        return None
    return (f"HERDR_APP_HOME is set to {env}, which is not supported in herdr-epic v0.1: it installs into "
            "~/.config/herdr-app. Unset HERDR_APP_HOME (or point it at ~/.config/herdr-app).")


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"files": {}, "links": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        state = None
    if not isinstance(state, dict) or not isinstance(state.get("files", {}), dict) \
            or not isinstance(state.get("links", {}), dict):
        raise Fail(f"{path} is corrupt; move it aside and run herdr-epic install again")
    state.setdefault("files", {})
    state.setdefault("links", {})
    return state


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _allowed(home: Path, p: dict, rel: str) -> bool:
    """True when `rel` stays inside home and inside something herdr-epic installs."""
    full = os.path.normpath(os.path.join(str(home), rel))
    exact = [p["layout"], p["layout"].with_name(p["layout"].name + ".new"), p["link"]]
    under = [p["mod"], p["state_dir"]]
    root = os.path.normpath(str(home))
    if os.path.commonpath([root, full]) != root:
        return False
    return full in [os.path.normpath(str(e)) for e in exact] or any(
        os.path.commonpath([os.path.normpath(str(u)), full]) == os.path.normpath(str(u)) for u in under)


def _wanted_files(home: Path) -> dict:
    """Absolute target path -> bytes to install."""
    p = _paths(home)
    if not p["source_layout"].exists():
        raise Fail(f"Shepherdr layout not found: {p['source_layout']}")
    source = json.loads(p["source_layout"].read_text(encoding="utf-8"))
    wanted = {p["layout"]: (json.dumps(generate_layout(source), indent=2, ensure_ascii=False) + "\n").encode("utf-8")}
    src = _REPO / "mods" / "epics"
    for file in sorted(src.rglob("*")):
        if not file.is_file() or file.name.startswith(".") or file.name.endswith(_HERDR_FILES):
            continue
        wanted[p["mod"] / file.relative_to(src)] = file.read_bytes()
    return wanted


def install(home: Path, dry_run: bool) -> list[str]:
    home = Path(home)
    problem = _app_home_problem(home)
    if problem:
        raise Fail(problem)
    p = _paths(home)
    wanted = _wanted_files(home)
    state = _load_state(p["state"])
    files = state["files"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    actions = []

    def write(dest: Path, data: bytes):
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

    for dest, data in wanted.items():
        rel = str(dest.relative_to(home))
        new_sha = _fingerprint(rel, data)
        if not dest.exists():
            actions.append(f"write {rel}")
            write(dest, data)
            files[rel] = new_sha
            continue
        disk = dest.read_bytes()
        disk_sha = _fingerprint(rel, disk)
        if disk_sha == new_sha:
            # Same apart from live blocks at most: leave Herdr's copy as it is.
            if rel not in files:
                actions.append(f"adopt {rel} (already identical)")
            files[rel] = new_sha
        elif files.get(rel) in (disk_sha, _sha(disk)):  # ours, also as recorded before live blocks were ignored
            actions.append(f"update {rel} (previous copy backed up)")
            if not dry_run:
                backup = p["state_dir"] / "backup" / stamp / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup)
            write(dest, data)
            files[rel] = new_sha
        else:
            reason = "edited" if rel in files else "not installed by herdr-epic"
            new_dest = dest.with_name(dest.name + ".new")
            actions.append(f"keep {rel} ({reason}); wrote {new_dest.name} instead")
            write(new_dest, data)
            files[str(new_dest.relative_to(home))] = new_sha

    link_rel = str(p["link"].relative_to(home))
    target = str(_REPO / "bin" / "herdr-epic")
    if p["link"].is_symlink() and os.readlink(p["link"]) == target:
        state["links"][link_rel] = target
    elif p["link"].exists() or p["link"].is_symlink():
        actions.append(f"keep {link_rel} (not ours); did not link")
    else:
        actions.append(f"link {link_rel} -> {target}")
        if not dry_run:
            p["link"].parent.mkdir(parents=True, exist_ok=True)
            p["link"].symlink_to(target)
        state["links"][link_rel] = target

    # Each root made by `root add` (it has root.json): AGENTS.md follows the template while unchanged.
    cfg_path = home / ".config" / "herdr-epic" / "config.json"
    for root in config.load(cfg_path)["roots"] if cfg_path.is_file() else []:
        path = Path(root["path"])
        if not (path / "root.json").is_file():
            continue
        meta = json.loads((path / "root.json").read_text(encoding="utf-8"))
        before = dict(meta)
        outcome = orchestrator.refresh_agents(path, meta, dry_run)
        agents = path / "AGENTS.md"
        if outcome == "kept":
            actions.append(f"keep {agents} (edited); wrote AGENTS.md.new instead")
        elif outcome == "updated":
            actions.append(f"update {agents} (unchanged since herdr-epic wrote it)")
        elif outcome == "wrote":
            actions.append(f"write {agents}")
        claude = orchestrator.link_claude(path, dry_run)
        if claude in ("linked", "replaced"):
            actions.append(f"link {path / 'CLAUDE.md'} -> AGENTS.md"
                           + (" (replacing the old @AGENTS.md import)" if claude == "replaced" else ""))
        elif claude == "kept":
            actions.append(f"keep {path / 'CLAUDE.md'} (your own file; Claude Code reads the epic rules only via a link to AGENTS.md)")
        if meta != before and not dry_run:
            (path / "root.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if not dry_run:
        state.update({"version": VERSION})
        _write_state(p["state"], state)
    actions.append(f"record {p['state'].relative_to(home)}")
    return actions


def uninstall(home: Path, dry_run: bool) -> list[str]:
    """Remove what we installed and left unchanged. Epic roots and config stay."""
    home = Path(home)
    p = _paths(home)
    if not p["state"].exists():
        return []
    state = _load_state(p["state"])
    actions = []
    kept = {}
    for rel, sha in sorted(state["files"].items()):
        if not _allowed(home, p, rel):
            actions.append(f"skip {rel} (outside the herdr-epic install set)")
            continue
        path = home / rel
        if not path.exists():
            continue
        data = path.read_bytes()
        if sha in (_fingerprint(rel, data), _sha(data)):
            actions.append(f"remove {rel}")
            if not dry_run:
                path.unlink()
        else:
            actions.append(f"keep {rel} (edited)")
            kept[rel] = sha
    for rel, target in sorted(state["links"].items()):
        if not _allowed(home, p, rel):
            actions.append(f"skip {rel} (outside the herdr-epic install set)")
            continue
        path = home / rel
        if path.is_symlink() and os.readlink(path) == target:
            actions.append(f"remove {rel}")
            if not dry_run:
                path.unlink()
    if not dry_run:
        for dir in (p["mod"],):
            if dir.is_dir() and not any(dir.iterdir()):
                dir.rmdir()
        if kept:
            state["files"] = kept
            state["links"] = {}
            _write_state(p["state"], state)
        else:
            p["state"].unlink()
    backups = p["state_dir"] / "backup"
    if backups.is_dir():
        actions.append(f"note: backups of replaced herdr-epic files stay in {backups}")
    if any(a.startswith("remove") and "shepherdr-epics.json" in a for a in actions):
        actions.append("note: if Herdr is showing the shepherdr-epics layout, switch to another layout first (ctrl-shift-l)")
    return actions


def _at_least(have: str, want: tuple) -> bool:
    parts = []
    for piece in have.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) >= want


def doctor(home: Path) -> list[dict]:
    home = Path(home)
    p = _paths(home)
    checks = []

    def add(check, ok, detail):
        checks.append({"check": check, "ok": bool(ok), "detail": detail})

    for name, args, floor in (("herdr-app", ["--version"], (0, 1, 12)), ("hc", ["--version"], (0, 1, 17)),
                              ("shepherdr", ["version"], (3, 1))):
        have = herdr.version(name, *args)
        need = ".".join(map(str, floor))
        if have is None:
            add(name, False, f"not found or no version output (need >= {need})")
        else:
            add(name, _at_least(have, floor), f"{have} (need >= {need})")
    add("herdr-task", shutil.which("herdr-task") is not None,
        shutil.which("herdr-task") or "not on PATH")
    problem = _app_home_problem(home)
    add("HERDR_APP_HOME", problem is None, problem or os.environ.get("HERDR_APP_HOME") or "not set")
    # The sidebar runs `herdr-epic` by name: PATH has to reach this clone (install links ~/.local/bin/herdr-epic).
    found, mine = shutil.which("herdr-epic"), str(_REPO / "bin" / "herdr-epic")
    if found is None:
        add("herdr-epic on PATH", False, "not found: ~/.local/bin is not on PATH, or herdr-epic install has not linked it")
    else:
        same = os.path.realpath(found) == os.path.realpath(mine)
        add("herdr-epic on PATH", same, found if same else
            f"{found} resolves to {os.path.realpath(found)}, not this clone's {mine}: the link points elsewhere")

    cfg_path = home / ".config" / "herdr-epic" / "config.json"
    try:
        cfg = config.load(cfg_path)
        add("config", True, str(cfg_path) if cfg_path.exists() else "no config file; using defaults")
        for root in cfg["roots"]:
            add(f"root {root['name']}", Path(root["path"]).is_dir(), root["path"])
    except Fail as err:
        add("config", False, str(err))

    clone = None
    try:
        if p["layout"].exists():
            clone = json.loads(p["layout"].read_text(encoding="utf-8")).get("cloned_from", {}).get("sha256")
        now = _canonical_sha(json.loads(p["source_layout"].read_text(encoding="utf-8"))) \
            if p["source_layout"].exists() else None
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as err:
        add("layout", False, f"cannot read a layout file: {err}")
        clone, now, unreadable = None, None, True
    else:
        unreadable = False
    if unreadable:
        pass
    elif clone is None:
        add("layout", False, "shepherdr-epics.json not installed; run herdr-epic install")
    elif now is None:
        add("layout", False, "Shepherdr layout not found")
    else:
        same = now == clone
        add("layout", same, "matches the Shepherdr layout it was generated from" if same else
            "Shepherdr layout changed since generation; run herdr-epic install to regenerate")

    sources = sorted((p["app"] / "mods" / "tasks" / "bin" / "herdr_task").glob("*.py"))
    sources += sorted((p["app"] / "mods" / "tasks").glob("*.js")) + sorted((p["app"] / "lib").glob("*.js"))
    sources += [p["source_layout"]]
    texts = {s: s.read_text(encoding="utf-8", errors="replace") for s in sources if s.exists()}
    # Labels and commands request delivery relies on; then the shared keys and layout ids the sidebar's
    # task view and the orchestrator's chat slot rely on.
    for label in ("herdr.task.space", "herdr.coordinator.space", "herdr.role", "herdr.chat.slot", "tell-assistant",
                  "tasks.thread", "tasks-thread", "panel-body", "coordinator.slots"):
        hits = [s.name for s, text in texts.items() if label in text]
        add(f"shepherdr {label}", hits, ", ".join(hits) or "not found in installed Shepherdr files")
    return checks


def _emit(as_json: bool, key: str, items: list) -> None:
    if as_json:
        sys.stdout.write(json.dumps({key: items}, ensure_ascii=False) + "\n")
    else:
        for item in items:
            print(item)


def _dry_run(argv: list[str]) -> bool:
    unknown = [a for a in argv if a != "--dry-run"]
    if unknown:
        raise Fail(f"unknown argument: {unknown[0]} (accepted: --dry-run)")
    return bool(argv)


def cmd_install(argv: list[str], as_json: bool) -> int:
    _emit(as_json, "actions", install(Path.home(), _dry_run(argv)))
    return 0


def cmd_uninstall(argv: list[str], as_json: bool) -> int:
    _emit(as_json, "actions", uninstall(Path.home(), _dry_run(argv)))
    return 0


def cmd_doctor(argv: list[str], as_json: bool) -> int:
    if argv:
        raise Fail(f"unknown argument: {argv[0]} (doctor takes none besides --json)")
    checks = doctor(Path.home())
    if as_json:
        _emit(True, "checks", checks)
    else:
        for c in checks:
            print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}")
    return 0 if all(c["ok"] for c in checks) else 1
