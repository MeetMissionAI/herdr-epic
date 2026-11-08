"""`request`, `reply`, `pending` and `whoami`: how a worker's ask reaches the orchestrator and back."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from herdr_epic import Fail, config, herdr, orchestrator, store, who
from herdr_epic.i18n import t

_REPLY_STATUSES = ("idle", "working", "done")
_EXAMPLE = 'herdr-epic request --repo <repo> --agent <agent> --context "<what you know>"'


def _tag(name: str, attrs: dict, text: str) -> str:
    """<name k="v"...>text</name>, escaped as Shepherdr's `tell` escapes: attribute values get &, " and <
    escaped; the text is kept as written except a closing tag, which is neutralised. None attributes are left out."""
    esc = lambda v: str(v).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    head = " ".join([name, *(f'{k}="{esc(v)}"' for k, v in attrs.items() if v is not None)])
    return f"<{head}>" + text.replace(f"</{name}", f"&lt;/{name}") + f"</{name}>"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _caller_epic(cfg: dict, me: dict, key: str | None) -> dict | None:
    """--epic KEY, else the epic of a worker or orchestrator caller, else None."""
    if key:
        return store.find(cfg, key)
    return me["epic"] if me["role"] in ("worker", "orchestrator") else None


def _print(as_json: bool, data: dict, text: str, stream=None) -> None:
    print(json.dumps(data, ensure_ascii=False) if as_json else text, file=None if as_json else stream)


def cmd_request(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    lang = cfg["language"]
    try:
        positional, opts = orchestrator.parse_opts(argv, ("--repo", "--agent", "--epic", "--context", "--context-file"))
        one = {name: values[-1] if values else None for name, values in opts.items()}
        if positional or not one["--repo"] or (one["--context"] is None) == (one["--context-file"] is None):
            raise Fail("usage: herdr-epic request --repo NAME [--agent A] [--epic KEY] "
                       "(--context TEXT | --context-file FILE)")
        context = one["--context"] if one["--context"] is not None else orchestrator.read_file(one["--context-file"])
        me = who.resolve(cfg, herdr.caller(), Path.cwd())
        epic = _caller_epic(cfg, me, one["--epic"])
        if epic is None:
            raise Fail(t("request.needs_epic", lang))
        if epic.get("state") == "done":
            raise Fail(t("epic.closed", lang))
        if epic.get("archived"):
            raise Fail(t("epic.archived", lang, title=epic["title"], id=epic["id"]))
        from_task = None
        if me["task_key"]:
            # Only for the orchestrator's information: a failed lookup must not stop the request.
            try:
                folders = herdr.task("list", "--all")
            except Fail:
                folders = []
            from_task = next((task.get("ref") for folder in folders for task in folder.get("tasks") or []
                              if task.get("key") == me["task_key"]), None)
        request = {"id": None, "from_terminal": os.environ.get("HC_TERMINAL_ID"), "from_task": from_task,
                   "repo": one["--repo"], "agent": one["--agent"], "context": context, "state": "queued", "at": _now()}
        epic = store.add_request(Path(epic["folder"]), request)
    except Fail as err:
        message = t("request.not_saved", lang, error=str(err))
        _print(as_json, {"error": message, "saved": False}, message, sys.stderr)
        return 1

    rid = request["id"]
    message = _tag("herdr-epic-request", {"id": rid, "from-task": from_task, "repo": request["repo"],
                                          "agent": request["agent"]}, context)
    result = orchestrator.ensure(epic, first_message=message)
    error, held = result["error"], False
    if result["started"] and not result["sent"]:
        error = t("request.not_received", lang)
    if result["address"] and not result["started"]:
        try:
            held = bool(herdr.task("tell-assistant", store.load(Path(epic["folder"]))["space"], message).get("held"))
        except Fail as err:
            error = f"could not deliver to the orchestrator {result['address']}: {err}"
    delivered = error is None

    def mark(e):
        req = next(r for r in e["requests"] if r.get("id") == rid)
        req["orchestrator"] = result["address"]
        if delivered:
            req.update(state="delivered", delivered=_now(), error=None)
        else:
            req["error"] = error

    store.update(Path(epic["folder"]), mark)
    out = {"request": rid, "state": "delivered" if delivered else "queued", "orchestrator": result["address"],
           "saved": True}
    if not delivered:
        message = t("request.saved_not_delivered", lang, id=rid, title=epic["title"], error=error)
        _print(as_json, {"error": message, **out}, message, sys.stderr)
        return 1
    key = "request.started" if result["started"] else "request.held" if held else "request.delivered"
    _print(as_json, out, t(key, lang, id=rid, title=epic["title"], address=result["address"]))
    return 0


def cmd_reply(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    lang = cfg["language"]
    positional, opts = orchestrator.parse_opts(argv, ("--result", "--epic"))
    if len(positional) != 2:
        raise Fail("usage: herdr-epic reply REQUEST_ID TEXT [--result REF] [--epic KEY]")
    rid, text = positional
    me = who.resolve(cfg, herdr.caller(), Path.cwd())
    if me["role"] == "worker":
        raise Fail(t("epic.worker_refused", lang, command="reply"))
    if me["role"] != "orchestrator":
        raise Fail(t("reply.not_orchestrator", lang))
    epic = _caller_epic(cfg, me, opts["--epic"][-1] if opts["--epic"] else None)
    req = next((r for r in epic["requests"] if r.get("id") == rid), None)
    if req is None:
        raise Fail(t("reply.unknown", lang, id=rid, title=epic["title"],
                     choices=", ".join(r["id"] for r in epic["requests"]) or "-"))

    address = req.get("from_terminal")
    message = _tag("herdr-epic-reply", {"id": rid}, text)
    reason = None
    if not address:
        reason = "the request has no requester terminal"
    else:
        try:
            term = herdr.terminal(address)
            # `hc get` reports status as {app, state, ...} (null for a plain shell); `hc ls` as a string.
            status = term.get("status")
            status = status.get("state") if isinstance(status, dict) else status
            if term.get("state") != "live" or status not in _REPLY_STATUSES:
                # A null status is the agent's own start screen (e.g. a folder-trust prompt, where
                # Enter quits the agent); blocked means it waits for the person. Typing there does harm.
                reason = (f"terminal {address} is {term.get('state')} with status {status}; "
                          "sending only when it is idle, working or done")
            elif not herdr.hc("send", address, message).get("started"):
                reason = f"hc send to {address} did not confirm the message arrived"
        except Fail as err:
            reason = f"terminal {address}: {err}"
    delivered = reason is None

    def mark(e):
        r = next(r for r in e["requests"] if r.get("id") == rid)
        r.update(reply=text, replied=_now(), state="done" if delivered else "replied-undelivered",
                 undelivered=reason)
        if opts["--result"]:
            r["result"] = opts["--result"][-1]

    store.update(Path(epic["folder"]), mark)
    out = {"request": rid, "state": "done" if delivered else "replied-undelivered", "delivered": delivered}
    if delivered:
        out["to"] = address
        _print(as_json, out, t("reply.delivered", lang, id=rid, address=address))
    else:
        out["reason"] = reason
        _print(as_json, out, t("reply.undelivered", lang, id=rid, reason=reason))
    return 0


def cmd_pending(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    positional, opts = orchestrator.parse_opts(argv, ("--epic",))
    if positional:
        raise Fail("usage: herdr-epic pending [--epic KEY]")
    me = who.resolve(cfg, herdr.caller(), Path.cwd())
    epic = _caller_epic(cfg, me, opts["--epic"][-1] if opts["--epic"] else None)
    if epic is None:
        raise Fail(t("epic.needs_epic", cfg["language"]))
    # Requests still waiting on the orchestrator: not handled yet, or answered but the reply never arrived.
    queued = [r for r in epic["requests"] if r.get("state") in ("queued", "replied-undelivered")]
    lines = [f"{r['id']} {r['state']} repo={r.get('repo') or '-'} agent={r.get('agent') or '-'} "
             f"from-task={r.get('from_task') or '-'} at={r.get('at')}\n{r.get('context')}"
             + (f"\nreply not delivered ({r.get('undelivered')}): {r.get('reply')}"
                if r["state"] == "replied-undelivered" else "")
             for r in queued]
    _print(as_json, {"epic": epic["id"], "requests": queued},
           "\n\n".join(lines) or t("pending.none", cfg["language"], title=epic["title"]))
    return 0


def cmd_whoami(argv: list[str], as_json: bool) -> int:
    cfg = config.load()
    lang = cfg["language"]
    if argv:
        raise Fail("usage: herdr-epic whoami")
    me = who.resolve(cfg, herdr.caller(), Path.cwd())
    epic = me["epic"]
    out = {"role": me["role"], "epic": None, "read": [], "request_example": _EXAMPLE}
    if epic:
        folder = Path(epic["folder"])
        out["epic"] = {k: epic[k] for k in ("id", "title", "folder", "repos")}
        out["read"] = [str(folder / name) for name in ("epic.md", "decisions.md", "handoff.md")]
    else:
        out["request_example"] = _EXAMPLE.replace("herdr-epic request", "herdr-epic request --epic <id|title>")
        out["hint"] = t("whoami.no_epic", lang)
    if me["root"]:
        out["root"] = me["root"]
    lines = [f"role: {me['role']}"]
    if epic:
        lines += [f'epic: {epic["id"]} "{epic["title"]}"', f"folder: {epic['folder']}",
                  "repos: " + (", ".join(r["name"] for r in epic["repos"]) or "-"), "read:",
                  *(f"  {p}" for p in out["read"])]
    else:
        lines.append(out["hint"])
    lines.append(f"request: {out['request_example']}")
    _print(as_json, out, "\n".join(lines))
    return 0
