# Changelog

## 0.1.0 (unreleased)

### New

- **Epics.** `herdr-epic new` creates one folder per requirement, opens a Herdr space for it, and starts that epic's orchestrator.
- **Roots.** `herdr-epic root add` registers an epic root, writes `AGENTS.md`, a one-line `CLAUDE.md`, and `root.json`, and opens the root assistant's space.
- **Tasks across repositories.** The orchestrator uses `herdr-epic task add` to create a Shepherdr task in a named repository, append an epic info block to the brief, record the task, and start it. `repo add` attaches another git repository. The root assistant is refused on both and relays with `herdr-epic request --epic`.
- **Requests.** A worker relays further work with `herdr-epic request`. The root assistant relays work for an open epic with `request --epic`, and uses `list` and `show` for what needs you. The command starts the orchestrator when it is down. `reply` is the orchestrator's. `close` accepts the orchestrator or the root. `pending` lists what is still waiting.
- **Archive.** `herdr-epic archive` and `unarchive` set or clear an epic's `archived` mark, open or Done, from a terminal, the root or the orchestrator. Refused while a task of the epic is still running, naming those tasks. `list` leaves archived epics out (`--archived` lists them too), `board --json` carries `archived` on each epic, and `request` and `task add` refuse an archived epic.
- **Sidebar.** Install generates the `shepherdr-epics` layout: By epic, By repo, and Shepherdr's Spaces list.
- **Archive in the sidebar.** By epic keeps archived epics in a folded **Archived (N)** group at the bottom. A button on a hovered epic row archives it, and inside Archived it unarchives. A refusal shows as a warning naming the running tasks. By repo leaves out tasks of archived epics.
- **Git is Shepherdr's.** The orchestrator rules and the worker's info block say that a task's branch, push, pull request and merge belong to Shepherdr: briefs carry no such steps, and a worker asked for a branch or pull request works on its task branch and lets `herdr-task ready` open it. A wish for a target branch becomes the task's base: `task add --base BRANCH` for one task, over the repository's. A worker whose local base is behind its remote rebases its own task branch instead of starting another.
- **Agent-behaviour evals are optional.** `evals/` runs real agents and grades outcomes, to look into a specific problem; it is not a gate for merging or releasing. There is no pass bar: `--holdout` stays available as an option and reports counts only.
- **Install.** `./install.sh` copies the mod and layout into the Herdr folder, links `herdr-epic`, and can dry-run. `uninstall` removes unchanged installed files. `doctor` checks versions, config, the layout, and the Shepherdr strings this tool uses.

### Fixed

- **Clicking a task in the sidebar opens it again after Herdr rewrote the layout.** Herdr 0.1.13 rewrites `shepherdr-epics.json` when a terminal shown in it ends, folding Shepherdr's one-child `panel` column away, and the click stopped at "no node \"panel\"". The sidebar now shows the task view through whatever holds it. `install` repairs a layout that differs from the generated one only by that rewrite (backing up the old copy) and still keeps a layout you edited; `doctor` names the nodes a layout lost and says which of the two it is.
