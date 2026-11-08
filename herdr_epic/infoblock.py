"""The epic info block that `herdr-epic task add` appends to every task's brief.

It is the only place a worker learns about its epic: workers never read the root AGENTS.md.
English for every harness; the agent talks to the person in the person's language.
"""
from __future__ import annotations

from pathlib import Path

_LANGUAGES = {"en": "English", "zh": "Chinese"}


def render(epic: dict, siblings: list[dict], lang: str) -> str:
    folder = Path(epic["folder"])
    tasks = [f"- {s['ref']} in {s['repo']}: {s['title']}" for s in siblings] or ["- none yet"]
    repos = ", ".join(repo["name"] for repo in epic["repos"]) or "none listed yet"
    lines = [
        "<herdr-epic>",
        "This task is one part of an epic: one requirement whose work may span several git repositories.",
        "Each part is its own task with its own agent; the epic's orchestrator (an agent that coordinates",
        "the epic) creates those tasks and connects them. You are the worker for this task.",
        "",
        f'Epic: "{epic["title"]}" (id {epic["id"]})',
        f"Epic folder: {folder}",
        "",
        "Before you start, read these three files. They are read-only for you: change nothing in the",
        "epic folder; the orchestrator keeps it.",
        f"- {folder / 'epic.md'} - background, goal, scope and what is out of it, acceptance criteria",
        f"- {folder / 'decisions.md'} - what the person decided and why, dated, newest last",
        f"- {folder / 'handoff.md'} - where the work stands and how the tasks connect, dated, newest last",
        "",
        "Sibling tasks when this task was added (`herdr-epic show` gives their current state):",
        *tasks,
        "",
        f"Repositories in this epic: {repos}",
        "",
        "## Your branch and pull request",
        "Shepherdr runs this task's git: its branch, the push, the pull request and the merge. This holds",
        "over any step earlier in this brief that says otherwise.",
        "- Work and commit on the branch Shepherdr gave this task, named after this block with its base.",
        "  No branch named there: you work in the folder itself; leave its branch as it is.",
        "- Don't create or switch to another branch, push, open a pull request or merge, even when the",
        "  brief or the person asks you to. Commits, and rebasing your own task branch, are yours. Merge",
        "  steps Shepherdr itself sends you (\"Merge task ... now\", where there is no pull request): follow them.",
        "- `herdr-task ready` pushes your branch and opens its pull request (on GitHub); the merge follows",
        "  your Settings block's Merge line.",
        "- Asked to make a branch or open a pull request: do the work here and tell the person that",
        "  `ready` takes care of it. They want it to go into another branch, or no pull request:",
        "  that is the orchestrator's to change; offer to pass it on with `herdr-epic request`.",
        "- Your branch starts from the local copy of your base, which may be behind its remote. Need the",
        "  newer base? Bring this branch up to date itself: `git fetch`, then rebase onto the remote's",
        "  copy of your base (e.g. `git rebase origin/<base>`). Never start another branch for it.",
        "",
        "## More work under this epic",
        "When the person wants another task under this epic, or a change in a repository other than",
        "yours (go by what they want to happen, not by how they phrase it):",
        "- Don't make that change yourself, in any repository or worktree. Don't run `herdr-task add` or",
        "  `herdr-task start`, start another agent, or ask a sibling task's agent (hc send or otherwise)",
        "  to take it on. All new work goes through the orchestrator (`herdr-epic task add` is its own).",
        "- Run this instead; it finds your epic from your terminal:",
        '  herdr-epic request --repo <repo> --agent <agent> --context "<what you know>"',
        "  --repo: a repository above (your own is fine too); for one not listed, the name the person uses.",
        "  --agent: the coding agent the person asked for (claude, codex, ...); leave it out if none.",
        "  --context: what the person asked, in their words; what you know that bears on it (interfaces,",
        "    decisions, files); whether it must wait for your task. Long context: --context-file <file>.",
        "  This is the explicit exception to your rule \"don't start other agents unless the task says",
        "  so\": you pass the person's request on; you don't start an agent.",
        "- Work that belongs to your own task stays yours: do it as usual. Unsure whether they want",
        "  separate work started now (or are remarking on later), or whether it is part of your task?",
        "  Ask them once, with your CLI's question tool, before sending anything.",
        "- They say to just do it yourself: give the reason in one line (the epic's orchestrator must know",
        "  about all work in the epic, and another task's agent may already be working in that repository)",
        "  and offer to send the request. If they insist, still send it, saying in --context that they",
        "  asked for it to be done directly; the orchestrator decides.",
        "- You find, unasked, that work outside your task is needed: tell the person and ask whether to",
        "  send it to the orchestrator. Don't send it unasked, and don't do it yourself.",
        "- Questions about the epic or a sibling task are not requests: look in the three files and",
        "  `herdr-epic show` first; if they don't answer it, tell the person what you found and that the",
        "  epic's orchestrator can answer.",
        "",
        "After `herdr-epic request`, show the person its output as it is (the request id, or the error,",
        "which says whether the request was saved). If it was not saved, say so plainly. Never do the",
        "work yourself instead. Then go on with your task.",
        'The orchestrator answers with <herdr-epic-reply id="...">...</herdr-epic-reply>, maybe while you',
        "are busy, maybe wrapped in an <hc ...> tag: pass it on to the person in your next message. If it",
        "asks a question, ask the person and send their answer with another `herdr-epic request` whose",
        "--context names the earlier request id. Sibling news from the orchestrator: use it in your work;",
        "if it would change what your task does, ask the person first.",
        "",
        "## What your siblings need from you",
        "Something a sibling task depends on (an interface or field settled, a behaviour changed,",
        "something it must wait for) goes in the --details of your `herdr-task ready`, in short plain",
        "bullets; the summary stays plain words for the person. The orchestrator records it in handoff.md.",
        "",
        "Talk to the person in the language they use with you"
        + (f" (their herdr-epic language setting is {_LANGUAGES[lang]})." if lang != "en" else "."),
        "These instructions are in English only so every agent can read them.",
        "</herdr-epic>",
    ]
    return "\n".join(lines)
