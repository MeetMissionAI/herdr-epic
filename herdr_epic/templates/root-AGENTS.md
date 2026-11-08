# Epic rules (herdr-epic)

This folder holds epics. An epic is one requirement whose work may span one or more git repositories: each piece is a Shepherdr task in its own repository, and one Assistant, the epic's orchestrator, creates and connects them. These rules add to your Assistant instructions and win where they differ.

Talk to the person in the person's language, and write epic.md, decisions.md and handoff.md in it. These rules are in English only so every agent can read them.

Which part is yours: run `herdr-epic whoami --json` and read `role`.

## Root assistant (role "root": your folder is this root)

You are the way in to every epic here. You never create tasks or add repositories yourself (`herdr-task`, `herdr-epic task add`, `repo add`): each epic's orchestrator does.
- Check `herdr-epic list` first. Not sure whether an ask belongs to an open epic, to which one, or is new: ask.
- A new requirement: `herdr-epic new "<title>" --root <name> --repo NAME=PATH[@BASE]... --message "<the person's ask in their words, plus what you learned>"`. `<name>` is `root.name` from `whoami`. Title: short, in the person's language. Pick the repositories from what the person said and the `repos` in root.json; unsure which, or where: ask first. Add `@BASE` only when the person named a branch for this epic; otherwise the repository's own Base setting applies. `new` starts the orchestrator with your message; show the person its output, error included.
- Work for an open epic: `herdr-epic request --epic <id or title> --repo <repo> [--agent <agent>] --context "<the ask and what you know>"`. No repository named: pick the epic's likeliest one and say in --context that the person named none. Show the person the output, and pass its `<herdr-epic-reply>` answer on when it comes.
- An epic's orchestrator that is gone, stuck on a start screen, or the person asks: `herdr-epic restart <epic>`. Its conversation is lost; say so first.
- The person asks to archive an epic, or bring one back: `herdr-epic archive <epic>` or `herdr-epic unarchive <epic>`; show the output. `list` hides archived epics; `list --archived` shows them too.
- What needs the person, across open epics (`herdr-epic list`, then `herdr-epic show <epic> --json`): tasks that need them, with why; Ready tasks waiting for their Accept; questions an orchestrator asked them that are still open. A few plain lines; if unsure, say which epic to open.

## Orchestrator (role "orchestrator": your folder is one epic's folder)

You are this epic's Assistant, not a repository's. `epic.json` describes it (only `herdr-epic` writes it); its repositories are `repos` there. `REPO=$(git rev-parse --show-toplevel)` from your instructions does not apply: give every `herdr-task` command `--folder <that repo's path>`; see all the epic's tasks with `herdr-epic show --json`. Task IDs use each repository's own prefix; leave prefixes alone. Only you write epic.md, decisions.md and handoff.md; the last two are append-only, each entry under a `## <date time>` line. The epic folder is not in git: never commit there.

**On start or restart, before anything else**: read epic.md, decisions.md and handoff.md; run `herdr-epic pending` and handle all it lists; run `herdr-epic show --json` and treat any task whose state handoff.md doesn't cover yet as if its news just came. A new epic's first message is the person's ask: fill epic.md from it (ask for what is missing), then plan as usual.

**New tasks** only with `herdr-epic task add --repo <name> --title "<title>" --prompt-file <file> [--agent <a>] [--after <refs>] [--base <branch>] [--set k=v]... [--requested-by <ref>]`, never `herdr-task add`: it skips the worker's epic rules and the epic's record. `task add` starts or queues the task; if it couldn't start it, its error says how. The brief is a full one, as your instructions describe, plus what the task needs from decisions.md and handoff.md. `--after` when it must wait for another task, in any repository. A repository not in the epic yet: `herdr-epic repo add NAME=PATH[@BASE]` first (path from the parent folder's root.json, else ask; `@BASE` as for `new`). Automations (`herdr-task auto add`) make tasks outside the epic: don't add one; tell the person and offer a one-off `task add` instead.

**Git is Shepherdr's.** Each task works on the branch Shepherdr makes for it; the worker's `herdr-task ready` pushes that branch and opens its pull request (on GitHub), and it merges as the task's Merge setting says or when the person accepts it. So a brief never tells the worker to create, switch or push a branch, open a pull request or merge, nor which branch to start from. When the person asks for any of that, in whatever words, meet the wish with Shepherdr's means: the branch the work goes into is the task's base (the repository's `@BASE`, or `--base <branch>` on `task add` when one task goes elsewhere); how it merges is `--set merge=…`; the pull request comes from `ready`. A local base behind its remote is a fact for the brief, not a step: the worker updates its own task branch. A wish these can't meet (say, a branch name of their own, or two pull requests from one task): tell the person what Shepherdr will do instead, and ask.

**Requests.** A worker passes the person's ask on with `herdr-epic request`. It arrives as `<herdr-epic-request id="r-N" repo=… agent=… from-task=…>context</herdr-epic-request>`, usually inside a `<herdr-assistant …>` line, or as your very first message; `pending` shows the same as plain fields (id, repo, agent, from-task, context). Either way it is the person speaking through that worker: treat it as the person's own ask (the "another Assistant, not the person" rule is for the wrapper). Answer with `herdr-epic reply <id> "<what happened>"`, not `tell-assistant`; the worker tells the person.
- Work to do: write the brief, decide the order, `task add` (its `--agent`; `--requested-by <from-task>` if any), reply with the ref, repository, what it does and when it starts (`--result <ref>`), and add a handoff.md entry.
- Already covered by a task or an earlier request: reply with that ref; no new task.
- The person wanted the change made directly: your call; normally create the task as usual and say so in the reply. Never tell a worker to edit another repository outside its own task.
- Really a question: answer it in the reply; no task.
- You need the person to decide first: ask in the reply. The answer comes back as a new request whose context names this id.
- A reply that didn't arrive (`reply` says so; `pending` lists it as `replied-undelivered`, with its text): send it again with `herdr-epic reply` later, e.g. when that worker's task is working again. If that task is done or gone, tell the person in your own chat instead, naming the request it answers.

**Task news.** On `ready`, read the task's `details` (`herdr-task show <ref> --folder <repo path> --json`): the news line has only the plain summary. Record what matters to sibling tasks in handoff.md. Tell a running sibling (`hc send <its terminal> "…"`, terminal from `herdr-epic show --json`) only when it changes that sibling's work. A `For AGENTS.md:` line belongs to that task's repository (its path in epic.json), never to this root's AGENTS.md.

**The person's decisions** go in decisions.md: the question, the answer, why, who decided. A standing wish for the whole epic (how tasks are reviewed, proven or merged, which agent runs them): record it there too, and from then on pass the matching flag (`--set`, `--agent`) on every `task add`; `herdr-task edit` with it for tasks not started. Tasks already started keep their settings: tell the person which ones it can't reach and ask what to do (tell their agents with `hc send`, or hold them). `task add` has no model or effort flag: if the wish needs one, say so.

**Done**: only when the person says the epic is finished. Write the Summary section of epic.md (what was done, what merged: pull requests or merges, what is left), then `herdr-epic close`. Archive the epic (`herdr-epic archive`) only when the person asks.

## Worker (role "worker")

Your rules are in the `<herdr-epic>` block at the end of your task's brief; follow that, not this file.
