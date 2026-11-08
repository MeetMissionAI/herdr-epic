"""The data behind the sidebar (`board`), and the `list`, `show`, `close`, `archive`, `unarchive`,
`root show` commands."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from herdr_epic import Fail, config, herdr, orchestrator, store, tasks, who
from herdr_epic.i18n import t


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _left(ms) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return ms if isinstance(ms, str) and ms else None
    minutes = max(1, round(ms / 60000))
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def _terminal(item: dict) -> str | None:
    live = [entry for entry in item.get("live") or [] if entry.get("address")]
    pick = next((e for e in live if e.get("role") == "worker"), live[0] if live else None)
    return pick["address"] if pick else None


def _key_and_log(item: dict, terminal: str | None) -> tuple[dict | None, dict | None]:
    slug, sep, number = (item.get("key") or "").rpartition("#")
    key = {"machine": None, "folder": item.get("folder"), "slug": slug,
           "id": int(number) if number.isdigit() else item.get("id")} if sep else None
    agent = next((a for a in reversed(item.get("agents") or []) if a.get("log")), None)
    # address: that agent's ended terminal. Shepherdr's task view opens an ended task by it and
    # reads the conversation from the log (its Tasks list does the same); without it, "Opening…".
    log = {"path": agent["log"], "kind": agent.get("agent"), "address": agent.get("address")} \
        if agent and terminal is None else None
    return key, log


def _row(item: dict, repo: str, epic: dict | None, lang: str) -> dict:
    status = item.get("status") or "todo"
    report = item.get("report") if isinstance(item.get("report"), dict) else {}
    terminal = _terminal(item)
    key, log = _key_and_log(item, terminal)
    line2 = item.get("why") if status == "needs" else report.get("text") or item.get("reason")
    return {"ref": item.get("ref"), "repo": repo, "title": item.get("title") or "", "status": status,
            "needs": status == "needs", "line2": line2 or "",
            "progress": {"done": report.get("done"), "left": _left(report.get("left"))},
            "epic": {"id": epic["id"], "title": epic["title"]} if epic else None,
            "terminal": terminal, "key": key, "log": log}


def _missing_row(entry: dict, epic: dict, lang: str, unknown: bool) -> dict:
    """A recorded task the task store does not list: gone ("missing"), or "unknown" when the store
    could not be read at all (board["error"] says why)."""
    return {"ref": entry["ref"], "repo": entry.get("repo"), "title": entry.get("title") or "",
            "status": "unknown" if unknown else "missing", "needs": False,
            "line2": "" if unknown else t("board.missing", lang), "progress": {"done": None, "left": None},
            "epic": {"id": epic["id"], "title": epic["title"]}, "terminal": None, "key": None, "log": None}


def _items(args: tuple) -> dict:
    """(realpath folder, ref) -> task item, from one `herdr-task list` call."""
    found = {}
    for group in herdr.task("list", *args):
        for item in group.get("tasks") or []:
            found[(_real(item.get("folder") or group.get("folder") or ""), item.get("ref"))] = item
    return found


def _root_meta(root: dict) -> dict:
    path = Path(root["path"]).expanduser()
    try:
        meta = json.loads((path / "root.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    return meta if isinstance(meta, dict) else {}


def build(cfg: dict) -> dict:
    lang = cfg["language"]
    epics = store.all_epics(cfg)
    metas = [(root, _root_meta(root)) for root in cfg["roots"]]
    error = None
    try:
        live = _items(("--all",))
    except Fail as err:
        live, error = {}, str(err)

    # Repository names: an epic's or a root's own name for a path, else the folder's name.
    names = {}
    paths = {}
    for repo in [r for e in epics for r in e.get("repos", [])] + [r for _, m in metas for r in m.get("repos", [])]:
        names.setdefault(_real(repo["path"]), repo["name"])
        paths.setdefault(_real(repo["path"]), repo["path"])

    def repo_name(folder: str) -> str:
        return names.get(folder) or os.path.basename(folder)

    wanted = [(_real(next((r["path"] for r in e.get("repos", []) if r["name"] == entry.get("repo")), "")), entry)
              for e in epics for entry in e.get("tasks", [])]
    archived = {}
    if error is None and any((folder, entry["ref"]) not in live for folder, entry in wanted):
        try:
            archived = _items(("--all", "--archived"))
        except Fail:
            archived = {}

    claimed = set()
    placed = []
    out_epics = []
    for epic in epics:
        rows = []
        for entry in epic.get("tasks", []):
            repo = next((r for r in epic.get("repos", []) if r["name"] == entry.get("repo")), None)
            folder = _real(repo["path"]) if repo else ""
            item = live.get((folder, entry["ref"])) or archived.get((folder, entry["ref"]))
            claimed.add((folder, entry["ref"]))
            rows.append(_row(item, entry.get("repo") or repo_name(folder), epic, lang) if item
                        else _missing_row(entry, epic, lang, error is not None))
            if repo:
                placed.append((folder, rows[-1]))
        out_epics.append({"id": epic["id"], "title": epic["title"], "folder": epic["folder"],
                          "space": epic.get("space"), "state": epic.get("state", "open"),
                          "archived": epic.get("archived"),
                          "counts": {"agents": sum(1 for r in rows if r["terminal"]),
                                     "working": sum(1 for r in rows if r["status"] == "working"),
                                     "needs": sum(1 for r in rows if r["needs"])},
                          "tasks": rows})

    unassigned = {}
    for (folder, ref), item in live.items():
        if (folder, ref) not in claimed and item.get("status") != "done":
            unassigned.setdefault(folder, []).append(_row(item, repo_name(folder), None, lang))

    by_repo = {}
    for folder, row in placed:
        by_repo.setdefault(folder, []).append(row)
    for folder, rows in unassigned.items():
        by_repo.setdefault(folder, []).extend(rows)
    for folder in names:
        by_repo.setdefault(folder, [])
    repos = []
    for folder, rows in by_repo.items():
        path = paths.get(folder, folder)
        behind = herdr.git(path, "rev-list", "--count", "HEAD..@{upstream}")
        repos.append({"name": repo_name(folder), "path": path,
                      "branch": herdr.git(path, "rev-parse", "--abbrev-ref", "HEAD"),
                      "behind": int(behind) if behind and behind.isdigit() else None, "tasks": rows})

    board = {"language": lang,
             "roots": [{"name": r["name"], "path": r["path"], "space": m.get("space")} for r, m in metas],
             "epics": out_epics,
             "unassigned": [{"repo": repo_name(f), "tasks": rows} for f, rows in unassigned.items()],
             "repos": repos}
    if error:
        board["error"] = error
    return board


def _print(data, as_json: bool, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False) if as_json else text)


def cmd_board(argv: list[str], as_json: bool) -> int:
    if argv:
        raise Fail("usage: herdr-epic board --json")
    board = build(config.load())
    _print(board, as_json, "\n".join(f"{e['id']} {e['title']} [{e['state']}]" for e in board["epics"]))
    return 0


def _summary(epic: dict) -> dict:
    return {"id": epic["id"], "title": epic["title"], "state": epic["state"], "archived": epic["archived"],
            "tasks": len(epic["tasks"]), "counts": epic["counts"]}


def cmd_list(argv: list[str], as_json: bool) -> int:
    """Archived epics are left out unless --archived, which lists them along with the rest."""
    if argv not in ([], ["--archived"]):
        raise Fail("usage: herdr-epic list [--archived] [--json]")
    cfg = config.load()
    epics = build(cfg)["epics"]
    summaries = [_summary(e) for e in epics if argv or not e["archived"]]
    lines = [f"{s['id']}  {s['state']:<4}{'  archived' if s['archived'] else ''}  {s['tasks']} tasks  "
             f"{s['counts']['working']} working  {s['counts']['needs']} needs  {s['title']}" for s in summaries]
    hidden = len(epics) - len(summaries)
    if hidden:
        lines.append(t("list.hidden", cfg["language"], count=hidden))
    _print({"epics": summaries}, as_json, "\n".join(lines))
    return 0


def _target(cfg: dict, argv: list[str], command: str) -> dict:
    """The epic named by the one positional argument, else the caller's own."""
    if len(argv) > 1:
        raise Fail(f"usage: herdr-epic {command} [EPIC] [--json]")
    if argv:
        return store.find(cfg, argv[0])
    me = who.resolve(cfg, herdr.caller(), Path.cwd())
    if me["epic"] is None:
        raise Fail(t("epic.needs_epic", cfg["language"]))
    return me["epic"]


def cmd_show(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    epic = _target(cfg, argv, "show")
    # ensure_space refuses a closed epic; show still prints it, without repairing its space.
    if epic["state"] != "done":
        epic = orchestrator.ensure_space(epic)
    board = build(cfg)
    entry = next(e for e in board["epics"] if e["id"] == epic["id"])
    mine = {r["name"] for r in epic.get("repos", [])}
    repos = [{**r, "tasks": [row for row in r["tasks"] if row["epic"] and row["epic"]["id"] == epic["id"]]}
             for r in board["repos"] if r["name"] in mine]
    lines = [f"{entry['id']} {entry['title']} [{entry['state']}{', archived' if entry['archived'] else ''}]  "
             f"{entry['space']}"]
    lines += [f"  {r['ref']}  {r['status']:<8}  {r['repo']}  {r['title']}" for r in entry["tasks"]]
    _print({**entry, "repos": repos}, as_json, "\n".join(lines))
    return 0


def cmd_close(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    positional, _ = orchestrator.parse_opts(argv, ())
    if len(positional) > 1:
        raise Fail("usage: herdr-epic close [EPIC] [--json]")
    epic = tasks.own_epic(cfg, positional[0] if positional else None, "close", ("orchestrator", "root"))
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    epic = store.update(Path(epic["folder"]), lambda e: e.update(state="done", closed=stamp))
    _print({"id": epic["id"], "title": epic["title"], "state": "done", "closed": stamp}, as_json,
           t("close.done", cfg["language"], title=epic["title"]))
    return 0


# Task statuses that let an epic be archived: not started, finished, or gone from the task store. Any
# other status (working, queued, needs, ready, merging, or unknown when the store can't be read) blocks.
_SETTLED = ("todo", "done", "missing")


def _set_archived(argv: list[str], as_json: bool, archive: bool) -> int:
    """`archive` and `unarchive`: for the person (role none), the root and the orchestrator; workers are
    refused. Archiving only sets `archived`; the epic's state, tasks and space stay as they are. Archiving
    an archived epic, or unarchiving one that isn't, changes nothing and exits 0."""
    cfg = config.load()
    lang = cfg["language"]
    command = "archive" if archive else "unarchive"
    positional, _ = orchestrator.parse_opts(argv, ())
    if len(positional) > 1:
        raise Fail(f"usage: herdr-epic {command} [EPIC] [--json]")
    epic = tasks.own_epic(cfg, positional[0] if positional else None, command, ("orchestrator", "root", "none"))
    if bool(epic.get("archived")) == archive:
        _print({"id": epic["id"], "title": epic["title"], "archived": epic.get("archived"), "changed": False},
               as_json, t(f"{command}.already", lang, title=epic["title"]))
        return 0
    if archive:
        rows = next(e for e in build(cfg)["epics"] if e["id"] == epic["id"])["tasks"]
        blocking = [{"ref": r["ref"], "title": r["title"], "status": r["status"]}
                    for r in rows if r["status"] not in _SETTLED]
        if blocking:
            message = t("archive.blocked", lang, title=epic["title"], tasks="\n".join(
                f"  {b['ref']}  {b['status']}  {b['title']}" for b in blocking))
            if as_json:
                print(json.dumps({"error": message, "id": epic["id"], "blocking": blocking}, ensure_ascii=False))
            else:
                print(message, file=sys.stderr)
            return 1
    stamp = datetime.now().astimezone().isoformat(timespec="seconds") if archive else None
    epic = store.update(Path(epic["folder"]), lambda e: e.update(archived=stamp))
    _print({"id": epic["id"], "title": epic["title"], "archived": stamp, "changed": True}, as_json,
           t(f"{command}.done", lang, title=epic["title"], id=epic["id"]))
    return 0


def cmd_archive(argv: list[str], as_json: bool) -> int:
    return _set_archived(argv, as_json, True)


def cmd_unarchive(argv: list[str], as_json: bool) -> int:
    return _set_archived(argv, as_json, False)


def cmd_root_show(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    if len(argv) > 1:
        raise Fail("usage: herdr-epic root show [NAME] [--json]")
    if argv:
        root = next((r for r in cfg["roots"] if r["name"] == argv[0]), None)
        if root is None:
            raise Fail(f"unknown root: {argv[0]}")
    elif len(cfg["roots"]) == 1:
        root = cfg["roots"][0]
    else:
        raise Fail("several roots; pass NAME. Roots: " + ", ".join(r["name"] for r in cfg["roots"]))
    path = Path(root["path"]).expanduser()
    meta = {"name": root["name"], "space": None, "repos": [], **_root_meta(root)}
    if not orchestrator._space_alive(meta["space"]):
        meta["space"] = herdr.app_do("space.new", name=root["name"], folder=str(path))["space"]
        path.mkdir(parents=True, exist_ok=True)
        (path / "root.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out = {**meta, "path": str(path)}
    _print(out, as_json, f"{out['name']}: {out['path']}  {out['space']}")
    return 0
