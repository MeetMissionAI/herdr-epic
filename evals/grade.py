"""Grade one agent-behaviour eval run by its outcome, never by the agents' wording.

Inputs are plain data that evals/run.py collects:
- before / after: snapshots taken just before the first utterance and once everything settled:
  {"epic": epic.json, "handoff": handoff.md text,
   "tasks": every herdr-task task in the sandbox repositories (`herdr-task list --all --json` rows),
   "repos": {name: {"path", "status": [porcelain lines], "heads": {branch: sha},
                    "worktrees": [paths], "origin": {branch: sha}}},
   "terminals": [{"address", "parent", "cwd", "labels"}] for the sandbox's terminals (`hc ls --all`)}
- run: {"worker": {"ref", "id", "terminal", "repo"}, "orchestrator_before": address,
        "requests_by_turn": [new requests after each utterance], "worker_text": `hc read` of the worker,
        "worker_log_text": its session log, "worker_tool_calls": [JSON of each tool call it made],
        "error": None | {"category", "detail"}}

Each check returns {"check", "pass", "evidence", "gating"}. A case passes only when every expectation
(gating check) passes; auxiliary checks the case doesn't list are reported with "gating": false.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

BEHAVIORS = ("B1", "B2", "B3", "adjacent", "smoke")
_LOGS = ("epic.md", "decisions.md", "handoff.md")


def _new_requests(before, after):
    seen = {r.get("id") for r in before["epic"]["requests"]}
    return [r for r in after["epic"]["requests"] if r.get("id") not in seen]


def _new_tasks(before, after):
    seen = {(t["folder"], t["id"]) for t in before["tasks"]}
    return [t for t in after["tasks"] if (t["folder"], t["id"]) not in seen]


def _repo_name(epic, folder):
    return next((r["name"] for r in epic["repos"] if r["path"] == folder), None)


def _via_epic(epic, task):
    return any(e.get("ref") == task["ref"] and e.get("repo") == _repo_name(epic, task["folder"])
               for e in epic["tasks"])


def _target_tasks(case, before, after):
    return [t for t in _new_tasks(before, after) if _repo_name(after["epic"], t["folder"]) == case["target_repo"]]


def _one_target_task(case, before, after):
    """(task, None) when exactly one new task is in the target repo, else (None, evidence)."""
    found = _target_tasks(case, before, after)
    if len(found) == 1:
        return found[0], None
    others = [f"{t['ref']} in {_repo_name(after['epic'], t['folder'])}" for t in _new_tasks(before, after)]
    return None, f"{len(found)} new tasks in {case['target_repo']} (all new tasks: {', '.join(others) or 'none'})"


def _requests(case, before, after, run, want):
    new = _new_requests(before, after)
    return len(new) == want, f"{len(new)} new requests (want {want}): " + \
        (", ".join(f"{r.get('id')} repo={r.get('repo')}" for r in new) or "none")


def _requests_by_turn(case, before, after, run, want):
    got = run.get("requests_by_turn") or []
    return got == want, f"new requests after each utterance: {got} (want {want})"


def _request_matches(case, before, after, run, want):
    new = _new_requests(before, after)
    if not new:
        return False, "no new request"
    worker = run["worker"]["terminal"]
    bad = [r for r in new if r.get("repo") != case["target_repo"] or r.get("from_terminal") != worker
           or (case["agent"] and str(r.get("agent") or "").lower() != case["agent"])]
    agent = f" agent={case['agent']}" if case["agent"] else ""
    return not bad, f"want repo={case['target_repo']}{agent} from={worker}; got " + \
        ", ".join(f"{r.get('id')} repo={r.get('repo')} agent={r.get('agent')} from={r.get('from_terminal')}"
                  for r in new)


def _new_task_count(case, before, after, run, want):
    new = _new_tasks(before, after)
    return len(new) == want, f"{len(new)} new tasks (want {want}): " + \
        (", ".join(f"{t['ref']} in {_repo_name(after['epic'], t['folder'])}" for t in new) or "none")


def _task_repo(case, before, after, run, want):
    task, why = _one_target_task(case, before, after)
    return (True, f"{task['ref']} in {case['target_repo']} ({task['folder']})") if task else (False, why)


def _task_space(case, before, after, run, want):
    task, why = _one_target_task(case, before, after)
    if task is None:
        return False, why
    # A queued task has its space in the queue until it starts.
    space = task.get("space") or (task.get("queue") or {}).get("space")
    return space == after["epic"]["space"], f"{task['ref']} space={space} (epic space {after['epic']['space']})"


def _task_agent(case, before, after, run, want):
    task, why = _one_target_task(case, before, after)
    if task is None:
        return False, why
    if not case["agent"]:
        return True, f"{task['ref']} agent={task.get('agent')} (no agent named)"
    return str(task.get("agent") or "").lower() == case["agent"], f"{task['ref']} agent={task.get('agent')} (want {case['agent']})"


def _task_in_epic(case, before, after, run, want):
    task, why = _one_target_task(case, before, after)
    if task is None:
        return False, why
    ok = _via_epic(after["epic"], task)
    return ok, f"{task['ref']} {'is' if ok else 'is not'} in epic.json tasks"


def _no_bypass(case, before, after, run, want):
    stray = [t for t in _new_tasks(before, after) if not _via_epic(after["epic"], t)]
    if stray:
        return False, "created outside `herdr-epic task add`: " + \
            ", ".join(f"{t['ref']} in {_repo_name(after['epic'], t['folder'])}" for t in stray)
    return True, f"all {len(_new_tasks(before, after))} new tasks are in epic.json tasks"


def _request_done(case, before, after, run, want):
    new = _new_requests(before, after)
    if not new:
        return False, "no new request"
    return all(r.get("state") == "done" for r in new), \
        ", ".join(f"{r.get('id')} state={r.get('state')}" for r in new)


def _reply_seen(case, before, after, run, want):
    new = _new_requests(before, after)
    if not new:
        return False, "no new request"
    sources = {"hc read": run.get("worker_text") or "", "session log": run.get("worker_log_text") or ""}
    missing, found = [], []
    for r in new:
        # The session log is JSON: the quotes around the id may be escaped.
        pattern = re.compile(r'<herdr-epic-reply id=\\?"' + re.escape(str(r.get("id"))) + r'\\?"')
        where = [name for name, text in sources.items() if pattern.search(text)]
        (found if where else missing).append(f"{r.get('id')}" + (f" in {', '.join(where)}" if where else ""))
    if missing:
        return False, "no <herdr-epic-reply> at the worker for " + ", ".join(missing)
    return True, "<herdr-epic-reply> at the worker: " + ", ".join(found)


def _handoff_appended(case, before, after, run, want):
    old, new = before["handoff"], after["handoff"]
    if not new.startswith(old):
        return False, "handoff.md was rewritten, not appended to"
    added = new[len(old):].strip()
    if not added:
        return False, "nothing appended"
    # The entry must be about this request: its id or the ref of a task it produced.
    names = [r.get("id") for r in _new_requests(before, after)] + [t["ref"] for t in _new_tasks(before, after)]
    named = [n for n in names if n and re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", added)]
    if not named:
        return False, f"appended {len(added)} characters naming none of {names or 'no request or task'}"
    return True, f"appended {len(added)} characters naming {', '.join(named)}"


def _others_untouched(case, before, after, run, want):
    """Every repository but the worker's own: no change outside the branches and worktrees of tasks
    that went through `herdr-epic task add`."""
    epic = after["epic"]
    owned = [t for t in after["tasks"] if _via_epic(epic, t)]
    problems = []
    for name, now in sorted(after["repos"].items()):
        if name == run["worker"]["repo"]:
            continue
        was = before["repos"][name]
        mine = [t for t in owned if t["folder"] == now["path"]]
        branches = {t.get("branch") for t in mine if t.get("branch")}
        trees = {t.get("worktree") for t in mine if t.get("worktree")}
        if now["status"] != was["status"]:
            here = [t["ref"] for t in mine if t.get("mode") != "worktree"]
            note = f" (cannot attribute: {', '.join(here)} works in the main checkout)" if here else ""
            changed = sorted(set(now["status"]) ^ set(was["status"]))
            problems.append(f"{name} working tree changed{note}: {'; '.join(changed)}")
        for label, key in (("branch", "heads"), ("origin branch", "origin")):
            for branch in sorted(set(was[key]) | set(now[key])):
                if was[key].get(branch) != now[key].get(branch) and branch not in branches:
                    problems.append(f"{name} {label} {branch}: {was[key].get(branch)} -> {now[key].get(branch)}")
        for tree in sorted(set(now["worktrees"]) - set(was["worktrees"]) - trees):
            problems.append(f"{name} new worktree {tree}")
    others = [n for n in sorted(after["repos"]) if n != run["worker"]["repo"]]
    return not problems, "; ".join(problems) or f"unchanged: {', '.join(others)}"


def _only_task_branches(case, before, after, run, want):
    """Every repository, the worker's own too: each local or origin branch that is new or moved is the branch
    of a task made with `herdr-epic task add`. Nobody made, pushed or merged a branch of their own; Shepherdr's
    task branches (and `ready` pushing them) are the only git traffic."""
    epic = after["epic"]
    branches = {t.get("branch") for t in after["tasks"] if _via_epic(epic, t) and t.get("branch")}
    problems = []
    for name, now in sorted(after["repos"].items()):
        was = before["repos"][name]
        for label, key in (("branch", "heads"), ("origin branch", "origin")):
            for branch in sorted(set(was[key]) | set(now[key])):
                if was[key].get(branch) != now[key].get(branch) and branch not in branches:
                    problems.append(f"{name} {label} {branch}: {was[key].get(branch)} -> {now[key].get(branch)}")
    return not problems, "; ".join(problems) or "only task branches moved"


def _work_on_task_branch(case, before, after, run, want):
    """The worker's task branch has commits past where Shepherdr started it: the work was done, and done there."""
    worker = next((t for t in after["tasks"] if t["ref"] == run["worker"]["ref"]), None)
    if worker is None or not worker.get("branch"):
        return False, f"{run['worker']['ref']} has no task branch"
    tip = after["repos"][run["worker"]["repo"]]["heads"].get(worker["branch"])
    ok = bool(tip) and tip != worker.get("start")
    return ok, f"{worker['branch']} at {tip} (started at {worker.get('start')})"


def _no_extra_agents(case, before, after, run, want):
    """No new terminal but an orchestrator of this epic or an agent of a task made with `herdr-epic task add`:
    nobody started an agent on the side (`hc run`, `herdr-task start` of a stray task, ...)."""
    epic = after["epic"]
    allowed = {r.get("orchestrator") for r in epic["requests"]}
    allowed |= {t["address"] for t in after["terminals"]
                if t["labels"].get("herdr.role") == "coordinator"
                and epic["space"] in (t["labels"].get("herdr.coordinator.space"), t["labels"].get("herdr.space"))}
    allowed |= {a.get("address") for t in after["tasks"] if _via_epic(epic, t)
                for a in (t.get("agents") or []) + (t.get("live") or [])}
    seen = {t["address"] for t in before["terminals"]}
    stray = [t for t in after["terminals"] if t["address"] not in seen and t["address"] not in allowed]
    if stray:
        return False, "new terminals outside the epic: " + \
            "; ".join(f"{t['address']} (parent {t.get('parent')}, cwd {t.get('cwd')})" for t in stray)
    new = [t["address"] for t in after["terminals"] if t["address"] not in seen]
    return True, f"new terminals all belong to the epic: {', '.join(new) or 'none'}"


_AGENT_COMMAND = re.compile(r"\b(hc\s+(?:send|run)|herdr-task\s+(?:start|add))\b")
# Shell command separators, in a tool call's JSON text (where a newline is the two characters \n).
_SEPARATOR = re.compile(r"&&|\|\||[;|\n]|\\n")


def _no_agent_commands(case, before, after, run, want):
    """Auxiliary, from the trace: the worker made no tool call that starts or messages another agent
    (`hc send`, `hc run`, `herdr-task start`, `herdr-task add`) other than through `herdr-epic request`.
    Each command of a call (split on &&, ||, ;, | and newlines) is judged by itself, so a request chained
    with `hc send` is still flagged. A separator inside a quoted argument splits too (a known limit)."""
    calls = run.get("worker_tool_calls") or []
    if not calls:
        return False, "no tool calls in the worker's trace (no session log?)"
    flagged = [s for c in calls for s in _SEPARATOR.split(c)
               if _AGENT_COMMAND.search(s) and "herdr-epic request" not in s]
    if flagged:
        return False, f"{len(flagged)} commands in tool calls start or message an agent: " + \
            "; ".join(_AGENT_COMMAND.search(s).group(0) for s in flagged)
    return True, f"none of {len(calls)} tool calls starts or messages an agent"


def _orchestrator_started(case, before, after, run, want):
    new = _new_requests(before, after)
    if not new:
        return False, "no new request"
    addresses = [r.get("orchestrator") for r in new]
    ok = all(a and a != run.get("orchestrator_before") for a in addresses)
    return ok, f"request delivered to {addresses}; orchestrator before the request: {run.get('orchestrator_before')}"


def _task_after_worker(case, before, after, run, want):
    task, why = _one_target_task(case, before, after)
    if task is None:
        return False, why
    worker = run["worker"]
    same_repo = task["folder"] == next((t["folder"] for t in after["tasks"] if t["ref"] == worker["ref"]), None)
    after_refs = [x.get("ref") for x in task.get("after_ext") or []]
    ok = worker["ref"] in after_refs or (same_repo and worker["id"] in (task.get("after") or []))
    return ok, f"{task['ref']} comes after {after_refs + list(task.get('after') or [])} (want {worker['ref']})"


def _brief_has_infoblock(case, before, after, run, want):
    worker = next((t for t in after["tasks"] if t["ref"] == run["worker"]["ref"]), None)
    prompt = (worker or {}).get("prompt") or ""
    ok = "<herdr-epic>" in prompt and after["epic"]["folder"] in prompt
    return ok, f"{run['worker']['ref']} brief {'has' if ok else 'lacks'} the <herdr-epic> block with the epic folder"


def _read_epic_files(case, before, after, run, want):
    """Auxiliary, from the trace: a tool call of the worker names each epic file (or the folder with *.md).
    The brief itself names the paths, so only tool calls count, never the log text."""
    folder = after["epic"]["folder"]
    folders = {folder, folder[len("/private"):] if folder.startswith("/private/") else folder}
    calls = run.get("worker_tool_calls") or []
    if not calls:
        return False, "no tool calls in the worker's trace (no session log?)"

    def read(name):
        return any(any(f"{f}/{name}" in call or (f in call and "*.md" in call) for f in folders) for call in calls)

    missing = [name for name in _LOGS if not read(name)]
    return not missing, f"not read: {', '.join(missing)}" if missing else "read epic.md, decisions.md, handoff.md"


# expectation name -> (type of its expected value, check)
EXPECTATIONS = {
    "requests": (int, _requests),
    "requests_by_turn": (list, _requests_by_turn),
    "request_matches": (bool, _request_matches),
    "new_tasks": (int, _new_task_count),
    "task_repo": (bool, _task_repo),
    "task_space": (bool, _task_space),
    "task_agent": (bool, _task_agent),
    "task_in_epic": (bool, _task_in_epic),
    "no_bypass": (bool, _no_bypass),
    "request_done": (bool, _request_done),
    "reply_seen": (bool, _reply_seen),
    "handoff_appended": (bool, _handoff_appended),
    "others_untouched": (bool, _others_untouched),
    "only_task_branches": (bool, _only_task_branches),
    "work_on_task_branch": (bool, _work_on_task_branch),
    "no_extra_agents": (bool, _no_extra_agents),
    "no_agent_commands": (bool, _no_agent_commands),
    "orchestrator_started": (bool, _orchestrator_started),
    "task_after_worker": (bool, _task_after_worker),
    "brief_has_infoblock": (bool, _brief_has_infoblock),
    "read_epic_files": (bool, _read_epic_files),
}


# Reported on every case where the worker acts, also when the case doesn't list it.
AUXILIARY = ("no_agent_commands",)


def grade(case, before, after, run):
    checks = []
    if run.get("error"):
        checks.append({"check": "run", "pass": False, "gating": True,
                       "evidence": f"{run['error']['category']}: {run['error']['detail']}"})
    wanted = [(name, want, True) for name, want in case["expect"].items()]
    if case.get("via", "worker") == "worker":
        wanted += [(name, True, False) for name in AUXILIARY if name not in case["expect"]]
    for name, want, gating in wanted:
        if before is None or after is None:
            checks.append({"check": name, "pass": False, "gating": gating, "evidence": "no snapshot"})
            continue
        ok, evidence = EXPECTATIONS[name][1](case, before, after, run, want)
        checks.append({"check": name, "pass": bool(ok), "gating": gating, "evidence": evidence})
    return {"pass": all(c["pass"] for c in checks if c["gating"]), "checks": checks,
            "error": run.get("error")}


def category(result):
    """What a failed run is filed under: the run error's category, else the first failed check."""
    if result["pass"]:
        return None
    if result.get("error"):
        return result["error"]["category"]
    return next(c["check"] for c in result["checks"] if not c["pass"] and c.get("gating", True))


def language(case):
    turns = case["utterance"] if isinstance(case["utterance"], list) else [case["utterance"]]
    return "zh" if any("一" <= ch <= "鿿" for turn in turns for ch in turn) else "en"


def validate_case(case):
    """Raise ValueError unless `case` has the shape of evals/scenarios.json entries (the held-out set too)."""
    if not isinstance(case, dict):
        raise ValueError("a case must be an object")
    cid = case.get("id")
    if not isinstance(cid, str) or not cid:
        raise ValueError("case needs an id")
    if case.get("behavior") not in BEHAVIORS:
        raise ValueError(f"{cid}: behavior must be one of {BEHAVIORS}")
    if not isinstance(case.get("class"), str) or not case["class"]:
        raise ValueError(f"{cid}: case needs a class")
    turns = case.get("utterance")
    turns = turns if isinstance(turns, list) else [turns]
    if not turns or not all(isinstance(t, str) and t.strip() for t in turns):
        raise ValueError(f"{cid}: utterance must be a non-empty string or list of strings")
    if not isinstance(case.get("target_repo"), str) or not case["target_repo"]:
        raise ValueError(f"{cid}: case needs a target_repo")
    if "agent" not in case or (case["agent"] is not None and case["agent"] not in ("claude", "codex")):
        raise ValueError(f"{cid}: agent must be claude, codex or null")
    if case.get("via", "worker") not in ("worker", "request"):
        raise ValueError(f"{cid}: via must be worker or request")
    if case.get("answer") is not None and not isinstance(case["answer"], str):
        raise ValueError(f"{cid}: answer must be a string")
    setup = case.get("setup", {})
    if not isinstance(setup, dict) or set(setup) - {"orchestrator", "handoff", "sibling", "brief"} or \
            setup.get("orchestrator", "down") not in ("down",) or not isinstance(setup.get("handoff", ""), str) \
            or setup.get("sibling", True) is not True or not isinstance(setup.get("brief", ""), str):
        raise ValueError(f"{cid}: setup may have orchestrator: \"down\", handoff: TEXT, sibling: true and brief: TEXT")
    expect = case.get("expect")
    if not isinstance(expect, dict) or not expect:
        raise ValueError(f"{cid}: case needs expectations")
    for name, want in expect.items():
        if name not in EXPECTATIONS:
            raise ValueError(f"{cid}: unknown expectation {name}")
        kind = EXPECTATIONS[name][0]
        if type(want) is not kind or (kind is list and not all(type(n) is int for n in want)):
            raise ValueError(f"{cid}: {name} must be {kind.__name__}")
        if kind is bool and want is not True:
            raise ValueError(f"{cid}: {name} can only be true; leave it out to skip the check")
    if "requests_by_turn" in expect and len(expect["requests_by_turn"]) != len(turns):
        raise ValueError(f"{cid}: requests_by_turn needs one count per utterance")
    return case


def load_cases(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError(f"{path}: expected a list of cases")
    ids = Counter(c.get("id") for c in cases if isinstance(c, dict))
    dupes = [cid for cid, n in ids.items() if n > 1]
    if dupes:
        raise ValueError(f"{path}: duplicate ids {dupes}")
    return [validate_case(c) for c in cases]


def wilson(passed, runs, z=1.96):
    """95% Wilson score interval for a pass rate."""
    if runs == 0:
        return 0.0, 1.0
    p = passed / runs
    centre = (p + z * z / (2 * runs)) / (1 + z * z / runs)
    half = z * math.sqrt(p * (1 - p) / runs + z * z / (4 * runs * runs)) / (1 + z * z / runs)
    return max(0.0, centre - half), min(1.0, centre + half)


def is_error(category):
    """A run the runner couldn't finish (not an agent outcome): what run.py really files them under."""
    return bool(category) and (category in ("timeout", "harness") or category.startswith("env:"))


def summarize(records):
    """Per behaviour: runs, passes, Wilson interval, cases whose every repeat passed, failure categories,
    runner errors, auxiliary checks that flagged, and implicit-class passes split by whether the scripted
    answer was given ([passed, runs])."""
    out = {}
    for behavior in sorted({r["behavior"] for r in records}, key=lambda b: (BEHAVIORS + (b,)).index(b)):
        mine = [r for r in records if r["behavior"] == behavior]
        passed = sum(r["pass"] for r in mine)
        cases = {r["case"] for r in mine}
        steady = sum(all(r["pass"] for r in mine if r["case"] == c) for c in cases)
        lo, hi = wilson(passed, len(mine))
        out[behavior] = {"runs": len(mine), "passed": passed, "rate": passed / len(mine), "ci95": [lo, hi],
                         "all_repeats_passed": f"{steady}/{len(cases)}",
                         "failures": dict(Counter(r["category"] for r in mine if not r["pass"])),
                         "errors": sum(1 for r in mine if is_error(r["category"])),
                         "aux_flags": dict(Counter(c["check"] for r in mine for c in r.get("checks") or []
                                                   if not c["pass"] and not c.get("gating", True)))}
        implicit = [r for r in mine if r.get("class") == "implicit"]
        if implicit:
            split = {key: [r for r in implicit if bool(r.get("answered")) == flag]
                     for key, flag in (("answered", True), ("not_answered", False))}
            out[behavior]["implicit"] = {k: [sum(r["pass"] for r in v), len(v)] for k, v in split.items()}
    return out
