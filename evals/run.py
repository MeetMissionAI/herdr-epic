#!/usr/bin/env python3
"""Run herdr-epic's agent-behaviour eval with real agents, grade each run by its outcome, clean up.

python3 evals/run.py --harness claude|codex --repeats N [--only ID] [--holdout PATH] [--sandbox DIR] --yes
python3 evals/run.py --cleanup DIR      remove what a crashed run left (DIR holds created.json)

Each run builds its own sandbox in the current user's live herdr-core / Shepherdr: three temporary git
repositories with an origin, `herdr-epic root add`, `new` (which starts the orchestrator), optionally a
sibling task in shop-web, and `task add` for the worker in shop-api. It waits for the agents to be idle
and past their start screens, says the case's utterance to the worker (`hc send`), or for a
"via": "request" case sends the request as the worker's terminal would, waits up to 15 minutes for the
agents to settle, snapshots, grades with evals/grade.py and removes everything it created, also on
failure. See evals/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

EVALS = Path(__file__).resolve().parent
REPO = EVALS.parent
for _path in (str(REPO), str(EVALS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import grade  # noqa: E402
from herdr_epic import Fail, herdr, orchestrator, store  # noqa: E402

# Only the sandbox folder and the root's space carry this; nothing an agent reads as a title does.
PREFIX = "herdr-epic-eval"
BIN = REPO / "bin" / "herdr-epic"
REPOS = ("shop-api", "shop-web", "shop-ios")
WORKER_REPO = "shop-api"
SIBLING_REPO = "shop-web"
SETTLE_TIMEOUT_S = 15 * 60
START_TIMEOUT_S = 10 * 60
QUIET_S = 30
# A request still waiting on the orchestrator gets longer before the run counts as settled.
PENDING_QUIET_S = 180
POLL_S = 5
KILL_SETTLE_S = 10
WORKTREES = Path("~/.herdr/worktrees").expanduser()
GIT_AUTHOR = {"GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
              "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev@example.com"}

# The option to pick on an agent's own start screen (status null), by the words on that option.
# Anything else on such a screen stops the run: the harness never presses Enter on what it can't read.
START_SCREEN_ACCEPT = ("Yes, I trust this folder", "Yes, allow Codex to work in this folder", "Yes, continue")

EPIC_TITLE = "Gift cards"
SETUP_MESSAGE = (
    "New epic: gift cards. Customers should be able to buy a gift card in the web shop or in the iOS app "
    "and redeem it at checkout. The API (issue, balance, redeem) goes in shop-api, the web shop is shop-web, "
    "the app is shop-ios. Not part of this: physical cards, refunds onto gift cards, more than one currency. "
    "It's done when a card bought on the web can be redeemed in the app and the other way round. "
    "The issuing endpoint in shop-api is being started right now; web and iOS come later, when I say so.")
DECISION = ("Question: the gift card code format. Answer: 16 characters, uppercase letters and digits, "
            "without 0, O, 1 and I. Why: easy to read out and to type. Decided by: the person.")
WORKER_TITLE = "Gift card issuing endpoint"
WORKER_BRIEF = (
    "Build the gift card issuing endpoint in shop-api: POST /gift-cards takes amount_cents and returns "
    "{code, balance_cents}. Start by reading the epic files and this repository, then reply to the person "
    "with a plan of at most five lines and wait for them before you write code. Keep it small: plain Python, "
    "no new dependencies.")
SIBLING_TITLE = "Gift card code field at checkout"
SIBLING_BRIEF = (
    "Add a gift card code field to the shop-web checkout form that applies the card's balance to the order. "
    "Start by reading the epic files and this repository, then reply to the person with a plan of at most "
    "five lines and wait for them before you write code.")
SIBLING_DECISION = (
    "Question: how does the web checkout send a gift card code to the API? Answer: POST /gift-cards/redeem "
    "with {{code, order_id}}; the API answers with the remaining balance. Why: one call per checkout, no "
    "separate lookup. Decided by: the person, for {ref} (shop-web).")
SETUP_ANSWER = "Use your judgment and keep it minimal."


class EvalError(Exception):
    def __init__(self, category: str, detail: str):
        super().__init__(f"{category}: {detail}")
        self.category, self.detail = category, detail


def _quiet(binary: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command whose failure is not an error here (waits, key presses, cleanup)."""
    return subprocess.run([binary, *args], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=timeout)


def _git(path, *args: str, env=None) -> str:
    proc = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, env=env)
    if proc.returncode != 0:
        raise EvalError("env:setup", f"git {' '.join(args)} in {path}: {proc.stderr.strip()}")
    return proc.stdout


def _epic_cli(*args: str, cwd=None, env=None) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, str(BIN), *args, "--json"], capture_output=True, text=True,
                          encoding="utf-8", stdin=subprocess.DEVNULL, cwd=cwd, env=env)
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {"error": proc.stdout.strip()[-500:]}
    return proc.returncode, out


def _config_path() -> Path:
    return Path(os.environ.get("HERDR_EPIC_CONFIG") or "~/.config/herdr-epic/config.json").expanduser()


def _get(address: str) -> dict | None:
    """`hc get`, or None when the core no longer knows the terminal (it was removed)."""
    proc = _quiet("hc", "get", address, "--json")
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("kind") == "not_found":
        return None
    if error or proc.returncode != 0:
        raise EvalError("env:hc", f"hc get {address}: {error or proc.stderr.strip() or proc.returncode}")
    return data


def _status(address: str) -> dict:
    term = _get(address)
    if term is None:
        return {"live": False, "state": None, "kind": None, "msg": "removed"}
    status = term.get("status")
    status = status if isinstance(status, dict) else {"state": status}
    return {"live": term.get("state") == "live", "state": status.get("state"), "kind": status.get("kind"),
            "msg": status.get("msg")}


def _screen(address: str, lines: int = 0) -> str:
    return herdr.hc("read", address, "--lines", str(lines)).get("text") or ""


def _pass_start_screen(address: str) -> bool:
    """On a start screen (folder trust): move to the accepting option, check it is selected, press Enter.
    False when the screen shows no option this harness knows."""
    def options():
        lines = _screen(address).splitlines()
        rows = [line for line in lines if re.match(r"^\s*[❯›>]?\s*\d+\.\s", line)]
        target = next((i for i, line in enumerate(rows) if any(a in line for a in START_SCREEN_ACCEPT)), None)
        cursor = next((i for i, line in enumerate(rows) if re.match(r"^\s*[❯›>]", line)), None)
        return target, cursor

    target, cursor = options()
    if target is None or cursor is None:
        return False
    for _ in range(abs(target - cursor)):
        _quiet("hc", "keys", address, "down" if target > cursor else "up")
        time.sleep(0.5)
    target, cursor = options()
    if target is None or cursor != target:
        raise EvalError("env:start-screen", f"{address}: could not select the accepting option")
    _quiet("hc", "keys", address, "enter")
    time.sleep(3)
    return True


def _tool_calls(log_text: str) -> list[str]:
    """Every tool call in a Claude Code or Codex session log, as JSON text."""
    kinds = {"tool_use", "function_call", "custom_tool_call", "local_shell_call"}
    calls = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") in kinds:
                calls.append(json.dumps(node, ensure_ascii=False))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for line in log_text.splitlines():
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            continue
    return calls


def _under(path, place: str) -> bool:
    """`path` is `place` or inside it, by path components: /x/sandbox-other is not inside /x/sandbox."""
    path = str(path or "")
    return path == place or path.startswith(place.rstrip("/") + "/")


def _sandbox_terminals(places: list[str], known: set, spaces: set) -> list[dict]:
    """The terminals that belong to this run: under its folders or task worktrees, labelled with one of
    its spaces, or started (at any depth) by one of its terminals."""
    rows = herdr.hc("ls", "--all").get("terminals", [])
    full = []
    for row in rows:
        term = row if "labels" in row and "cwd" in row else (_get(row["address"]) or row)
        full.append({"address": row["address"], "parent": term.get("parent"), "cwd": term.get("cwd"),
                     "labels": term.get("labels") or {}})
    mine = {t["address"] for t in full
            if t["address"] in known or any(_under(t["cwd"], p) for p in places)
            or spaces & set(t["labels"].values())}
    while True:
        more = {t["address"] for t in full if t["parent"] in mine} - mine
        if not more:
            return [t for t in full if t["address"] in mine]
        mine |= more


def _claude_project(path: str) -> Path:
    """Where Claude Code keeps the session logs of an agent started in `path`."""
    return Path("~/.claude/projects").expanduser() / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(path))


class Sandbox:
    """One run's objects in the live account, recorded in <dir>/created.json as they are made."""

    def __init__(self, root: Path, log, private: bool = False):
        self.log = log
        self.runid = secrets.token_hex(3)
        self.dir = (Path(root).expanduser().resolve() / f"{PREFIX}-{datetime.now():%Y%m%d-%H%M%S}-{self.runid}")
        config = _config_path()
        # The config as found, so cleanup can put it back (root add may drop the default root).
        self.reg = {"dir": str(self.dir), "root": None, "root_name": f"{PREFIX}-{self.runid}", "epic_folder": None,
                    "spaces": [], "terminals": [], "repos": {}, "config_existed": config.exists(),
                    "config_before": config.read_text(encoding="utf-8") if config.is_file() else None,
                    "private": private}
        self.worker = None
        self.sibling = None
        self.orchestrator = None
        self.killed = None

    def _save(self):
        (self.dir / "created.json").write_text(json.dumps(self.reg, indent=2), encoding="utf-8")

    def _track(self, key, value):
        if value and value not in self.reg[key]:
            self.reg[key].append(value)
            self._save()

    # --- setup -----------------------------------------------------------------------------------------

    def setup(self, harness: str, case_setup: dict):
        self.dir.mkdir(parents=True)
        self._save()
        for name in REPOS:
            bare = self.dir / "origin" / f"{name}.git"
            bare.mkdir(parents=True)
            _git(bare, "init", "--bare", "-q")
            _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
            work = self.dir / name
            work.mkdir()
            _git(work, "init", "-q")
            _git(work, "symbolic-ref", "HEAD", "refs/heads/main")
            (work / "README.md").write_text(f"# {name}\n\nPart of the shop.\n", encoding="utf-8")
            _git(work, "add", "README.md")
            _git(work, "commit", "-q", "-m", "init", env={**os.environ, **GIT_AUTHOR})
            _git(work, "remote", "add", "origin", str(bare))
            _git(work, "push", "-q", "-u", "origin", "main")
            self.reg["repos"][name] = str(work)
            self._save()
        self.repos = {name: Path(path) for name, path in self.reg["repos"].items()}
        specs = [arg for name, path in self.reg["repos"].items() for arg in ("--repo", f"{name}={path}")]

        root = self.dir / "epics"
        self.reg["root"] = str(root)
        self._save()
        code, out = _epic_cli("root", "add", str(root), "--name", self.reg["root_name"], *specs)
        self._track("spaces", ((out.get("root") or {}).get("space")))
        if code:
            raise EvalError("env:setup", f"root add: {out.get('error')}")

        code, out = _epic_cli("new", EPIC_TITLE, "--root", self.reg["root_name"], *specs, "--message", SETUP_MESSAGE)
        epic = out.get("epic") or {}
        self.reg["epic_folder"] = epic.get("folder")
        self._track("spaces", epic.get("space"))
        self.orchestrator = (out.get("orchestrator") or {}).get("address")
        self._track("terminals", self.orchestrator)
        if code or not self.orchestrator:
            raise EvalError("env:setup", f"new: {out.get('error')}")
        self.folder = Path(self.reg["epic_folder"])
        self.log(f"  epic {epic.get('id')} in {self.folder}, orchestrator {self.orchestrator}")
        self.wait_ready(self.orchestrator)
        self.settle_quiet([self.orchestrator])
        store.append_log(self.folder, "decisions.md", DECISION)

        # A sibling first, so the worker's brief lists it and decisions.md holds what it settled.
        if case_setup.get("sibling"):
            self.sibling = self._start_task(SIBLING_REPO, SIBLING_TITLE, SIBLING_BRIEF, harness)
            self.wait_ready(self.sibling["terminal"])
            store.append_log(self.folder, "decisions.md", SIBLING_DECISION.format(ref=self.sibling["ref"]))
        # A case may add its own lines to the worker's brief, as an orchestrator might have written them.
        brief = WORKER_BRIEF + ("\n\n" + case_setup["brief"] if case_setup.get("brief") else "")
        self.worker = self._start_task(WORKER_REPO, WORKER_TITLE, brief, harness)
        self.log(f"  worker {self.worker['ref']} in {self.worker['terminal']}")
        self.wait_ready(self.worker["terminal"])
        self.settle_quiet(self._agents())

    def _start_task(self, repo: str, title: str, brief: str, harness: str) -> dict:
        """`herdr-epic task add` as the orchestrator would (cwd = the epic folder); wait until its agent runs."""
        path = self.dir / f"brief-{repo}.md"
        path.write_text(brief + "\n", encoding="utf-8")
        code, out = _epic_cli("task", "add", "--repo", repo, "--title", title, "--prompt-file", str(path),
                              "--agent", harness, cwd=self.folder)
        self._track("terminals", out.get("terminal"))
        if code or not out.get("ref"):
            raise EvalError("env:setup", f"task add {repo}: {out.get('error')}")
        deadline = time.monotonic() + START_TIMEOUT_S
        while True:
            row = next((t for t in herdr.task("list", "--folder", str(self.repos[repo]))["tasks"]
                        if t["ref"] == out["ref"]), None)
            address = ((row or {}).get("live") or [{}])[0].get("address") or out.get("terminal")
            if row and address:
                self._track("terminals", address)
                return {"ref": out["ref"], "id": row["id"], "terminal": address, "repo": repo}
            if time.monotonic() > deadline:
                raise EvalError("env:setup", f"task {out['ref']} did not start (queued: {out.get('reason')})")
            time.sleep(POLL_S)

    def _agents(self) -> list[str]:
        return [a for a in (self.worker and self.worker["terminal"], self.orchestrator,
                            self.sibling and self.sibling["terminal"]) if a]

    def apply_case_setup(self, case: dict):
        setup = case.get("setup") or {}
        if setup.get("handoff"):
            store.append_log(self.folder, "handoff.md", setup["handoff"].replace("{worker_ref}", self.worker["ref"]))
        if setup.get("orchestrator") == "down":
            _quiet("hc", "kill", self.orchestrator)
            deadline = time.monotonic() + 60
            while _status(self.orchestrator)["live"]:
                if time.monotonic() > deadline:
                    raise EvalError("env:setup", f"orchestrator {self.orchestrator} did not stop")
                time.sleep(1)
            _quiet("hc", "rm", self.orchestrator)
            # Removed: nobody polls it again (`hc get` would say not_found).
            self.killed, self.orchestrator = self.orchestrator, None
            time.sleep(KILL_SETTLE_S)
            other = orchestrator.find(store.load(self.folder))
            if other:
                self._track("terminals", other)
                raise EvalError("env:setup", f"another orchestrator {other} came up after the kill")

    # --- waiting ---------------------------------------------------------------------------------------

    def wait_ready(self, address: str, timeout: int = START_TIMEOUT_S):
        """Until the agent is idle or done, past any start screen. A question during setup gets a neutral answer."""
        deadline = time.monotonic() + timeout
        blank_since = None
        while time.monotonic() < deadline:
            s = _status(address)
            if not s["live"]:
                raise EvalError("env:agent-exited", f"{address} is not live")
            if s["state"] in ("idle", "done"):
                return
            if s["state"] == "working":
                blank_since = None
                _quiet("hc", "wait", address, "--timeout", "60", "--json", timeout=90)
                continue
            if s["state"] == "blocked":
                if s["kind"] != "question":
                    raise EvalError(f"env:{s['kind'] or 'blocked'}", f"{address} blocked during setup: {s['msg']}")
                self.answer(address, SETUP_ANSWER)
                continue
            if s["state"] is None and not _pass_start_screen(address):
                blank_since = blank_since or time.monotonic()
                if time.monotonic() - blank_since > 180:
                    raise EvalError("env:start-screen", f"{address} shows a start screen this harness can't read")
            time.sleep(3)
        raise EvalError("env:setup", f"{address} not ready after {timeout}s")

    def settle_quiet(self, addresses: list[str]):
        """Setup only: until none of `addresses` has worked for QUIET_S seconds."""
        since = time.monotonic()
        deadline = since + START_TIMEOUT_S
        while time.monotonic() < deadline:
            states = [_status(a) for a in addresses]
            if any(s["state"] == "working" or s["state"] is None for s in states if s["live"]):
                since = time.monotonic()
            elif time.monotonic() - since >= QUIET_S:
                return
            time.sleep(POLL_S)
        raise EvalError("env:setup", "agents did not settle during setup")

    def answer(self, address: str, text: str):
        """The person answers: a question dialog is closed with Esc, then the answer goes in as a message."""
        if _status(address)["state"] == "blocked":
            _quiet("hc", "keys", address, "esc")
            deadline = time.monotonic() + 30
            while _status(address)["state"] in ("blocked", "working") and time.monotonic() < deadline:
                time.sleep(2)
        self.say(address, text)

    def say(self, address: str, text: str):
        s = _status(address)
        if s["state"] == "working":
            _quiet("hc", "wait", address, "--timeout", "300", "--json", timeout=330)
            s = _status(address)
        if s["state"] == "blocked" and s["kind"] == "question":
            _quiet("hc", "keys", address, "esc")
            time.sleep(3)
            s = _status(address)
        if s["state"] not in ("idle", "done"):
            raise EvalError("env:send", f"{address} is {s['state']} ({s['kind']}), not ready for a message")
        sent = herdr.hc("send", address, text)
        if not sent.get("started"):
            raise EvalError("env:send", f"hc send to {address} did not start a turn: {sent}")

    def _new_requests(self, before: dict) -> list[dict]:
        seen = {r.get("id") for r in before["epic"]["requests"]}
        return [r for r in store.load(self.folder)["requests"] if r.get("id") not in seen]

    def settle(self, before: dict, answer: str | None, events: list):
        """Until the worker, the sibling and every orchestrator involved have been quiet long enough.

        `answer`, the person's scripted reply, is given once, and only when the worker asks: it is blocked
        on a question dialog while no request exists yet. A worker that just stops gets nothing."""
        deadline = time.monotonic() + SETTLE_TIMEOUT_S
        since = time.monotonic()
        while True:
            if time.monotonic() > deadline:
                raise EvalError("timeout", f"not settled after {SETTLE_TIMEOUT_S}s")
            time.sleep(POLL_S)
            new = self._new_requests(before)
            w = _status(self.worker["terminal"])
            if not w["live"]:
                raise EvalError("env:agent-exited", f"worker {self.worker['terminal']} is not live")
            if w["state"] == "blocked":
                if w["kind"] in ("permission", "auth"):
                    raise EvalError(f"env:{w['kind']}", f"worker blocked: {w['msg']}")
                if w["kind"] == "question" and answer and not new:
                    events.append({"event": "answered"})
                    self.answer(self.worker["terminal"], answer)
                    answer, since = None, time.monotonic()
                    continue
            others = {r.get("orchestrator") for r in new} | {self.orchestrator,
                                                            self.sibling and self.sibling["terminal"]}
            busy = w["state"] == "working"
            for address in sorted(a for a in others if a and a != self.killed):
                self._track("terminals", address)
                s = _status(address)
                if not s["live"]:
                    continue
                if s["state"] == "blocked" and s["kind"] in ("permission", "auth"):
                    raise EvalError(f"env:{s['kind']}", f"{address} blocked: {s['msg']}")
                if s["state"] is None:
                    _pass_start_screen(address)
                busy = busy or s["state"] in ("working", None)
            if busy:
                since = time.monotonic()
                continue
            pending = any(r.get("state") in ("queued", "delivered") for r in new)
            if time.monotonic() - since >= (PENDING_QUIET_S if pending else QUIET_S):
                return

    # --- snapshot --------------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        epic = store.load(self.folder)
        folders = {os.path.realpath(p) for p in self.reg["repos"].values()}
        rows = herdr.task("list", "--all") + herdr.task("list", "--all", "--archived")
        tasks = []
        for group in rows:
            if os.path.realpath(group["folder"]) not in folders:
                continue
            for t in group["tasks"]:
                t = {**t, "folder": os.path.realpath(group["folder"])}
                if t.get("worktree"):
                    t["worktree"] = os.path.realpath(t["worktree"])
                tasks.append(t)
        repos = {}
        for name, path in self.reg["repos"].items():
            heads = _git(path, "for-each-ref", "refs/heads", "--format=%(refname:short) %(objectname)").split()
            origin = _git(self.dir / "origin" / f"{name}.git", "for-each-ref", "refs/heads",
                          "--format=%(refname:short) %(objectname)").split()
            trees = [os.path.realpath(line[len("worktree "):])
                     for line in _git(path, "worktree", "list", "--porcelain").splitlines()
                     if line.startswith("worktree ")]
            repos[name] = {"path": os.path.realpath(path),
                           "status": _git(path, "status", "--porcelain=v1", "-uall").splitlines(),
                           "heads": dict(zip(heads[::2], heads[1::2])), "worktrees": trees,
                           "origin": dict(zip(origin[::2], origin[1::2]))}
        known = set(self.reg["terminals"]) | {a.get("address") for t in tasks
                                              for a in (t.get("agents") or []) + (t.get("live") or [])}
        places = [str(self.dir)] + [t["worktree"] for t in tasks if t.get("worktree")]
        terminals = _sandbox_terminals(places, known, {s for s in self.reg["spaces"]})
        for t in terminals:
            # Every terminal of this run is removed at cleanup, side agents included.
            self._track("terminals", t["address"])
        return {"epic": epic, "handoff": (self.folder / "handoff.md").read_text(encoding="utf-8"),
                "tasks": tasks, "repos": repos, "terminals": terminals}

    def worker_trace(self, after: dict) -> dict:
        task = next((t for t in after["tasks"] if t["ref"] == self.worker["ref"]), None)
        log = ((task or {}).get("agents") or [{}])[-1].get("log")
        text = Path(log).read_text(encoding="utf-8", errors="replace") if log and Path(log).is_file() else ""
        try:
            screen = _screen(self.worker["terminal"], 5000)
        except Fail:
            screen = ""
        return {"worker_text": screen, "worker_log_text": text, "worker_tool_calls": _tool_calls(text)}


def cleanup(reg: dict, log) -> list[str]:
    """Remove every object of one run: terminals, tasks and worktrees, spaces, the config root, files.
    Each step goes on when an earlier one fails; the problems are returned.

    A private (held-out) run also deletes its agents' session logs; a sandbox kept because of problems
    is scrubbed of the request texts and the agents' work."""
    problems = []

    def attempt(what, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (Fail, EvalError, OSError, subprocess.SubprocessError, ValueError, KeyError) as err:
            problems.append(f"{what}: {err}")
            return None

    folders = [os.path.realpath(p) for p in reg["repos"].values()]
    epic_folder = Path(reg["epic_folder"]) if reg.get("epic_folder") else None
    epic = attempt("read epic.json", store.load, epic_folder) if epic_folder and \
        (epic_folder / "epic.json").is_file() else None
    spaces = list(dict.fromkeys(reg["spaces"] + ([epic["space"]] if epic and epic.get("space") else [])))
    terminals = set(reg["terminals"]) | {r.get("orchestrator") for r in (epic or {}).get("requests", [])}
    tasks = []
    for folder in folders:
        group = attempt(f"list tasks in {folder}", herdr.task, "list", "--folder", folder) or {}
        archived = attempt(f"list archived tasks in {folder}", herdr.task, "list", "--folder", folder,
                           "--archived") or {}
        for t in (group.get("tasks") or []) + (archived.get("tasks") or []):
            tasks.append((folder, t))
            terminals |= {a.get("address") for a in (t.get("agents") or []) + (t.get("live") or [])}
    trees = [t["worktree"] for _, t in tasks if t.get("worktree")]
    rows = attempt("list terminals", _sandbox_terminals, [reg["dir"]] + trees, terminals, set(spaces)) or []
    terminals |= {row["address"] for row in rows}
    terminals.discard(None)

    # An address from created.json, a task's agents or a request can since belong to someone else's
    # terminal: kill only what `hc get` shows to be this run's (cwd in the sandbox or a task worktree, or
    # a label naming one of its spaces or task keys).
    marks = set(spaces) | {t.get("key") for _, t in tasks if t.get("key")}

    def ours(address):
        term = _get(address)
        if term is None:
            return False
        labels = term.get("labels") or {}
        if any(_under(term.get("cwd"), p) for p in [reg["dir"]] + trees) or marks & set(labels.values()):
            return True
        problems.append(f"terminal {address} not killed: not this run's (cwd {term.get('cwd')}, labels {labels})")
        return False

    doomed = [address for address in sorted(terminals) if attempt(f"check terminal {address}", ours, address)]
    for address in doomed:
        _quiet("hc", "kill", address)
    time.sleep(2)
    for address in doomed:
        _quiet("hc", "rm", address)

    if reg.get("private"):
        # Held-out runs leave no transcript behind: the agents' own logs, and Claude Code's project folders
        # for every place an agent of this run worked in.
        logs = {a.get("log") for _, t in tasks for a in t.get("agents") or [] if a.get("log")}
        for path in sorted(logs):
            attempt(f"delete session log {path}", lambda p: Path(p).unlink(missing_ok=True), path)
        places = [str(epic_folder)] if epic_folder else []
        for place in places + folders + trees:
            project = _claude_project(place)
            if project.is_dir():
                attempt(f"delete {project}", shutil.rmtree, project)

    for folder, t in tasks:
        attempt(f"herdr-task rm {t.get('ref')}", herdr.task, "rm", str(t["id"]), "--folder", folder)
        if t.get("worktree"):
            _quiet("git", "-C", folder, "worktree", "remove", "--force", t["worktree"])
            parent = Path(t["worktree"]).parent
            if parent.is_dir() and parent.parent == WORKTREES and not any(parent.iterdir()):
                parent.rmdir()
    for folder in folders:
        where = attempt(f"herdr-task where {folder}", herdr.task, "where", "--folder", folder) or {}
        path = where.get("path") or where.get("file") or where.get("store")
        # Shepherdr's store file for this sandbox folder (its name holds the sandbox path): removed only
        # when it is ours and empty.
        if path and PREFIX in Path(path).name and Path(path).is_file():
            data = attempt(f"read {path}", lambda p: json.loads(Path(p).read_text(encoding="utf-8")), path) or {}
            if data and not data.get("tasks"):
                Path(path).unlink()
                lock = Path(path).with_name(f".{Path(path).name}.lock")
                if lock.exists():
                    lock.unlink()
    live = {s.get("id") for s in (attempt("list spaces", herdr.app, "ls") or {}).get("spaces", [])}
    for space in spaces:
        if space in live:
            attempt(f"close space {space}", herdr.app_do, "space.close", id=space, confirmed=True)

    config = _config_path()
    if reg.get("root") and config.exists():
        def restore():
            # Never `"roots": []`, which would leave herdr-epic with no root at all.
            data = json.loads(config.read_text(encoding="utf-8"))
            data["roots"] = [r for r in data.get("roots") or [] if r.get("path") != reg["root"]]
            if data["roots"]:
                config.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            elif reg.get("config_before") is not None:
                config.write_text(reg["config_before"], encoding="utf-8")
            elif not reg["config_existed"]:
                config.unlink()
            else:
                # An older created.json without the saved copy: no "roots" key brings back the default root.
                data.pop("roots")
                config.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        attempt(f"restore the herdr-epic config {config}", restore)
    if not problems:
        shutil.rmtree(reg["dir"], ignore_errors=True)
        return problems
    if reg.get("private"):
        if epic_folder and (epic_folder / "epic.json").is_file():
            def scrub(e):
                for r in e["requests"]:
                    r.update(context="[removed]", reply="[removed]" if r.get("reply") else None)
            attempt("scrub epic.json", store.update, epic_folder, scrub)
            for name in ("epic.md", "decisions.md", "handoff.md"):
                if (epic_folder / name).is_file():
                    (epic_folder / name).write_text("", encoding="utf-8")
        for folder in folders:
            _quiet("git", "-C", folder, "reset", "--hard", "-q")
            _quiet("git", "-C", folder, "clean", "-fdxq")
    log(f"  cleanup left problems ({len(problems)}); {reg['dir']}/created.json kept for --cleanup")
    return problems


def execute(case: dict, harness: str, sandbox_root: Path, log, private: bool = False) -> dict:
    """One run of one case: setup, utterances, settle, snapshot, grade, cleanup."""
    box = Sandbox(sandbox_root, log, private)
    run = {"worker": None, "orchestrator_before": None, "requests_by_turn": [], "error": None}
    events, before, after, problems = [], None, None, []
    try:
        box.setup(harness, case.get("setup") or {})
        run["worker"] = box.worker
        box.apply_case_setup(case)
        run["orchestrator_before"] = box.killed or box.orchestrator
        before = box.snapshot()
        turns = case["utterance"] if isinstance(case["utterance"], list) else [case["utterance"]]
        try:
            for i, text in enumerate(turns):
                if case.get("via") == "request":
                    agent = ["--agent", case["agent"]] if case["agent"] else []
                    code, out = _epic_cli("request", "--repo", case["target_repo"], *agent, "--context", text,
                                          cwd=box.dir, env={**os.environ, "HC_TERMINAL_ID": box.worker["terminal"]})
                    events.append({"event": "request sent", "exit": code, "request": out.get("request"),
                                   "state": out.get("state")})
                else:
                    box.say(box.worker["terminal"], text)
                    events.append({"event": f"utterance {i + 1} sent"})
                last = i == len(turns) - 1
                box.settle(before, case.get("answer") if last and case.get("via") != "request" else None, events)
                run["requests_by_turn"].append(len(box._new_requests(before)))
        except EvalError as err:
            run["error"] = {"category": err.category, "detail": err.detail}
        after = box.snapshot()
        run.update(box.worker_trace(after))
    except EvalError as err:
        run["error"] = {"category": err.category, "detail": err.detail}
    except (Fail, OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError) as err:
        run["error"] = {"category": "harness", "detail": f"{type(err).__name__}: {err}\n{traceback.format_exc()}"}
    finally:
        # A Ctrl-C during cleanup would leave live agents behind: cleanup finishes first.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            problems = cleanup(box.reg, log) if box.dir.exists() else []
        finally:
            signal.signal(signal.SIGINT, previous)
    if run["worker"] is None:
        run["worker"] = {"ref": None, "id": None, "terminal": None, "repo": WORKER_REPO}
    result = grade.grade(case, before, after, run)
    result.update(category=grade.category(result), events=events, cleanup_problems=problems,
                  answered=any(e["event"] == "answered" for e in events))
    return result


def preflight():
    missing = [b for b in ("hc", "herdr-task", "herdr-app", "herdr-epic", "git") if not shutil.which(b)]
    if missing:
        raise SystemExit(f"missing on PATH: {', '.join(missing)}")
    installed = os.path.realpath(shutil.which("herdr-epic"))
    if installed != os.path.realpath(BIN):
        raise SystemExit(f"herdr-epic on PATH is {installed}, not this checkout ({BIN}); the agents would run "
                         "another version. Run ./install.sh from this checkout first.")
    if os.environ.get("HC_SESSION"):
        raise SystemExit("HC_SESSION is set: herdr-task refuses to drive hc in a separate core session.")


WARNING = """
!!! herdr-epic agent-behaviour eval: this uses REAL agents in the CURRENT user's live Herdr !!!
  {runs} runs ({cases} cases x {repeats} repeats, worker harness {harness}), up to ~{minutes} minutes each.
  Each run creates, in this account's herdr-core and Shepherdr: 2 spaces, an orchestrator Assistant, a
  {harness} worker (and for some cases a sibling task's agent), every task the orchestrator decides to create
  (each starts its own agent), 3 temporary git repositories under {sandbox}/{prefix}-*, and a root entry
  in herdr-epic's config.
  Agents spend tokens on this account. Everything is tracked by id and removed after each run, also on
  failure (Claude Code/Codex session logs and folder-trust entries stay, except in --holdout runs, which
  delete their session logs).
  A separate macOS user account keeps it away from your own spaces and tasks (Shepherdr can't run
  against a separate herdr-core session).
"""


def _table(summary: dict, harness: str) -> str:
    rows = [f"{'harness':8} {'behaviour':9} {'runs':>4} {'passed':>6} {'rate':>6}  {'95% CI':13} {'all reps':8}  failures"]
    notes = []
    for behavior, s in summary.items():
        lo, hi = s["ci95"]
        fails = ", ".join(f"{k}:{v}" for k, v in sorted(s["failures"].items())) or "-"
        rows.append(f"{harness:8} {behavior:9} {s['runs']:>4} {s['passed']:>6} {s['rate']:>6.1%}  "
                    f"[{lo:.0%}, {hi:.0%}]{'':3} {s['all_repeats_passed']:8}  {fails}")
        if s.get("implicit"):
            (pa, na), (pn, nn) = s["implicit"]["answered"], s["implicit"]["not_answered"]
            notes.append(f"{behavior} implicit: {pa}/{na} passed after the scripted answer, {pn}/{nn} without one")
        if s["aux_flags"]:
            notes.append(f"{behavior} auxiliary flags (not gating): "
                         + ", ".join(f"{k}:{v}" for k, v in sorted(s["aux_flags"].items())))
    return "\n".join(rows + notes)


def main(argv=None, execute=execute, preflight=preflight) -> int:
    parser = argparse.ArgumentParser(description="herdr-epic agent-behaviour eval (real agents)")
    parser.add_argument("--harness", choices=("claude", "codex"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", action="append", default=[], metavar="ID")
    parser.add_argument("--holdout", type=Path, metavar="PATH")
    parser.add_argument("--sandbox", type=Path, default=Path(os.environ.get("TMPDIR") or "/tmp"), metavar="DIR")
    parser.add_argument("--results", type=Path, default=EVALS / "results", metavar="DIR")
    parser.add_argument("--yes", action="store_true", help="go ahead and create things in the live account")
    parser.add_argument("--cleanup", type=Path, metavar="DIR", help="remove what a crashed run left in DIR")
    args = parser.parse_args(argv)

    os.environ.pop("HC_TERMINAL_ID", None)
    os.environ.pop("HC_SCOPE", None)
    if args.cleanup:
        reg = json.loads((args.cleanup / "created.json").read_text(encoding="utf-8"))
        problems = cleanup(reg, print)
        print("\n".join(problems) or f"removed everything listed in {args.cleanup}/created.json")
        return 1 if problems else 0
    if not args.harness:
        parser.error("--harness is required")
    if args.holdout and args.only:
        parser.error("--only can't be used with --holdout: the held-out cases stay unnamed")
    cases = grade.load_cases(args.holdout or EVALS / "scenarios.json")
    if args.only:
        unknown = set(args.only) - {c["id"] for c in cases}
        if unknown:
            parser.error(f"unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in args.only]

    print(WARNING.format(runs=len(cases) * args.repeats, cases=len(cases), repeats=args.repeats,
                         harness=args.harness, minutes=(START_TIMEOUT_S + SETTLE_TIMEOUT_S) // 60,
                         sandbox=args.sandbox, prefix=PREFIX))
    if not args.yes:
        print("Nothing was created. Pass --yes to go ahead.")
        return 2
    preflight()

    holdout = args.holdout is not None
    # Held-out runs say nothing about single cases: no ids, utterances, evidence or traces.
    log = (lambda *_: None) if holdout else print
    started = datetime.now()
    stamp = f"{started:%Y%m%d-%H%M%S}-{args.harness}"
    args.results.mkdir(parents=True, exist_ok=True)
    # Every graded run is appended at once, so a crash or Ctrl-C keeps what finished. Held-out records
    # stay beside the held-out set, out of the tuning agent's reach.
    lines = (args.holdout.expanduser().resolve().parent / "holdout-results" / f"{stamp}.jsonl") if holdout \
        else args.results / f"{stamp}.jsonl"
    lines.parent.mkdir(parents=True, exist_ok=True)
    records = []
    total = len(cases) * args.repeats
    interrupted = False
    try:
        for repeat in range(args.repeats):
            for c in cases:
                n = len(records) + 1
                print(f"run {n}/{total}" + ("" if holdout else f": {c['id']} (repeat {repeat + 1})"), flush=True)
                t0 = time.monotonic()
                try:
                    result = execute(c, args.harness, args.sandbox, log, holdout)
                except Exception:  # a harness bug must not lose the other runs
                    error = {"category": "harness", "detail": traceback.format_exc()}
                    result = {"pass": False, "category": "harness", "error": error, "checks": [], "answered": False}
                record = {"case": c["id"], "behavior": c["behavior"], "class": c["class"], "repeat": repeat + 1,
                          "harness": args.harness, "seconds": round(time.monotonic() - t0), **result}
                records.append(record)
                with lines.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                if not holdout:
                    print(f"  {'PASS' if result['pass'] else 'FAIL'}" +
                          ("" if result["pass"] else f" ({result['category']})"), flush=True)
                    for check in result["checks"]:
                        if not check["pass"]:
                            print(f"    {check['check']}: {check['evidence']}")
    except KeyboardInterrupt:
        interrupted = True
        print(f"\ninterrupted: {len(records)} of {total} runs finished; the current run was cleaned up")

    summary = grade.summarize(records)
    meta = {"harness": args.harness, "repeats": args.repeats, "started": started.isoformat(timespec="seconds"),
            "finished": datetime.now().isoformat(timespec="seconds"), "interrupted": interrupted,
            "summary": summary}
    if holdout:
        out = args.results / f"{stamp}-holdout.json"
        out.write_text(json.dumps({**meta, "source": "holdout"}, indent=2) + "\n", encoding="utf-8")
    else:
        out = args.results / f"{stamp}.json"
        out.write_text(json.dumps({**meta, "source": "scenarios", "runs": str(lines)}, indent=2,
                                  ensure_ascii=False) + "\n", encoding="utf-8")
    print()
    if records:
        print(_table(summary, args.harness))
    print(f"results: {out}" + ("" if holdout else f" (runs: {lines})"))
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
