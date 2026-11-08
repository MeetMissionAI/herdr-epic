"""`task add` and `repo add`: the orchestrator's commands for an epic's tasks and repositories."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from herdr_epic import Fail, config, herdr, infoblock, orchestrator, store, who
from herdr_epic.i18n import t


def own_epic(cfg: dict, key: str | None, command: str, roles: tuple = ("orchestrator",)) -> dict:
    """The epic a caller in `roles` means: --epic KEY, else the caller's own. Workers and, unless listed,
    the root are refused and pointed to `herdr-epic request`: new work goes through the orchestrator."""
    me = who.resolve(cfg, herdr.caller(), Path.cwd())
    lang = cfg["language"]
    if me["role"] == "worker":
        raise Fail(t("epic.worker_refused", lang, command=command))
    if me["role"] == "root" and "root" not in roles:
        raise Fail(t("epic.root_refused", lang, command=command))
    if me["role"] not in roles:
        raise Fail(t("epic.not_orchestrator", lang, command=command))
    if key:
        return store.find(cfg, key)
    if me["epic"] is None:
        raise Fail(t("epic.needs_epic", lang))
    return me["epic"]


def cmd_task_add(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    lang = cfg["language"]
    positional, opts = orchestrator.parse_opts(argv, (
        "--repo", "--title", "--prompt", "--prompt-file", "--agent", "--after", "--base", "--set", "--requested-by",
        "--epic"))
    one = {name: values[-1] if values else None for name, values in opts.items()}
    if positional or not one["--repo"] or not one["--title"] or bool(one["--prompt"]) == bool(one["--prompt-file"]):
        raise Fail("usage: herdr-epic task add --repo NAME --title TITLE (--prompt TEXT | --prompt-file FILE) "
                   "[--agent A] [--after REFS] [--base BRANCH] [--set K=V]... [--requested-by REF] [--epic KEY]")
    epic = own_epic(cfg, one["--epic"], "task add")
    if epic.get("archived"):
        raise Fail(t("epic.archived", lang, title=epic["title"], id=epic["id"]))
    repo = next((r for r in epic["repos"] if r["name"] == one["--repo"]), None)
    if repo is None:
        raise Fail(t("task.unknown_repo", lang, repo=one["--repo"], title=epic["title"],
                     choices=", ".join(r["name"] for r in epic["repos"]) or "-"))
    prompt = one["--prompt"] or orchestrator.read_file(one["--prompt-file"])
    epic = orchestrator.ensure_space(epic)
    agent = ["--agent", one["--agent"]] if one["--agent"] else []
    # Its own --base (where this one task goes) wins over the repository's.
    base = one["--base"] or repo["base"]

    with tempfile.TemporaryDirectory() as tmp:
        brief = Path(tmp) / "brief.md"
        brief.write_text(prompt.rstrip("\n") + "\n\n" + infoblock.render(epic, epic["tasks"], lang) + "\n",
                         encoding="utf-8")
        ref = herdr.task(
            "add", one["--title"], "--prompt-file", str(brief), "--folder", repo["path"], "--space", epic["space"],
            *(["--base", base] if base else []),
            *(["--after", one["--after"]] if one["--after"] else []),
            *[arg for kv in opts["--set"] for arg in ("--set", kv)],
            *agent)["ref"]

    entry = {"ref": ref, "repo": repo["name"], "title": one["--title"],
             "added": datetime.now().astimezone().isoformat(timespec="seconds"),
             "requested_by": one["--requested-by"]}
    store.update(Path(epic["folder"]), lambda e: e["tasks"].append(entry))
    out = {"ref": ref, "repo": repo["name"], "folder": repo["path"]}

    try:
        parent = orchestrator.find(epic) or os.environ.get("HC_TERMINAL_ID")
        started = herdr.task("start", ref, "--folder", repo["path"], "--space", epic["space"],
                             *(["--parent", parent] if parent else []), *agent)
    except Fail as err:
        # The task exists and belongs to the epic; say which one so the caller can start it.
        message = t("task.start_failed", lang, ref=ref, error=str(err), folder=repo["path"], space=epic["space"])
        if as_json:
            print(json.dumps({"error": message, **out}, ensure_ascii=False))
        else:
            print(message, file=sys.stderr)
        return 1

    if started.get("queued"):
        out.update(queued=True, reason=started["reason"])
        text = t("task.queued", lang, ref=ref, reason=started["reason"])
    else:
        out["terminal"] = started["terminal"]
        text = t("task.started", lang, ref=ref, terminal=started["terminal"], folder=repo["path"])
    print(json.dumps(out, ensure_ascii=False) if as_json else text)
    return 0


def cmd_repo_add(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    positional, opts = orchestrator.parse_opts(argv, ("--epic",))
    if len(positional) != 1:
        raise Fail("usage: herdr-epic repo add NAME=PATH[@BASE] [--epic KEY]")
    repo = orchestrator.parse_repo(positional[0])
    epic = own_epic(cfg, opts["--epic"][-1] if opts["--epic"] else None, "repo add")
    if not (Path(repo["path"]) / ".git").exists():
        raise Fail(f"not a git repository: {repo['name']} ({repo['path']})")

    def add(e):
        if any(r["name"] == repo["name"] for r in e["repos"]):
            raise Fail(t("repo.exists", cfg["language"], repo=repo["name"], title=e["title"]))
        e["repos"].append(repo)

    store.update(Path(epic["folder"]), add)
    out = {"epic": epic["id"], "repo": repo}
    print(json.dumps(out, ensure_ascii=False) if as_json
          else t("repo.added", cfg["language"], repo=repo["name"], title=epic["title"], path=repo["path"]))
    return 0
