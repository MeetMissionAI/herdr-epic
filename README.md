# herdr-epic

herdr-epic is a free, MIT-licensed add-on for the Herdr desktop app. It sits on Shepherdr 3.1.3 and adds epics. An epic is one requirement: the tasks for that requirement can live in several git repositories, and one orchestrator agent owns the epic.

## The problem

Shepherdr groups work by repository. A space is a folder, its Assistant starts tasks, and each task is an agent in that repository. Several requirements at once, each of them touching several repositories, become a flat list of repository tasks. Which agents belong to the same requirement stays in your head. An agent that sees a change needed in another repository has nowhere to hang that work, and it is easy to look at the wrong agent.

herdr-epic is that grouping. People and agents look up one epic, and new work for the epic goes through its orchestrator. A requirement called Gift cards might have a task in `shop-api`, a task in `shop-web`, and a task in `shop-ios`, and those three show up together.

## How it works

An epic is a Herdr space plus a folder. The folder lives under an epic root, a directory you choose. The space's Assistant is the orchestrator. It splits the requirement, creates the tasks, and keeps the epic's notes.

A root assistant sits on the root folder, outside every epic. You ask it to start an epic, or to say what needs you today. A new requirement is `herdr-epic new`, which creates the folder, opens the space, and starts the orchestrator with your request as its first message. Work for an epic that is already open is relayed:

```
herdr-epic request --epic "Gift cards" --repo shop-ios --agent claude --context "Add the redeem screen on iOS."
```

The root assistant shows you that command's output and passes the orchestrator's `<herdr-epic-reply>` on when it arrives. "What needs you" across open epics is `herdr-epic list`, then `herdr-epic show` on each one: tasks that need you and why, Ready tasks waiting for Accept, and questions an orchestrator still has open.

Workers are ordinary Shepherdr tasks. The orchestrator creates them with `herdr-epic task add`. That command calls `herdr-task add` with `--folder` set to the target repository and `--space` set to the epic's space, appends an info block to the brief, records the task on the epic, and starts it. `--space` is the epic's space, so Shepherdr sends that task's news to the orchestrator. `--base` is sent when `task add` gets one for that task, else when that repository has a base branch stored on the epic.

The info block is the worker's picture of the epic. It is English, wrapped in `<herdr-epic>` tags, and it names the epic folder, the three files to read before starting, the sibling tasks at the moment the task was added, and the repositories on the epic. When you ask for more work under the epic, or for a change in another repository, the worker runs:

```
herdr-epic request --repo shop-ios --agent claude --context "Add the redeem screen on iOS."
```

`request` finds the epic from the worker's terminal, or from `--epic` when the root assistant sends it. It appends a request (`r-1`, `r-2`, ...) to `epic.json`, makes sure an orchestrator is running, and delivers the text as `<herdr-epic-request>`. The orchestrator writes the brief, runs `herdr-epic task add`, and answers with `herdr-epic reply`. The reply is typed into the worker's terminal as `<herdr-epic-reply>`, and the worker passes it on to you. A reply to the root assistant is delivered to the root's terminal the same way.

Delivery works like this:

- An orchestrator that is already running receives the request through `herdr-task tell-assistant`. If someone is typing in that chat, Shepherdr holds the message until the draft is sent or cleared.
- With no orchestrator running, herdr-epic starts Shepherdr's `assistant` template in the epic folder (`hc run --keep`, with the coordinator labels) and passes the request as the first message. A closed space is created again first. A terminal Shepherdr is reviving (a live shell labeled `herdr.resume` in that space) is waited on for up to 90 seconds.
- When the message cannot be delivered, the command exits 1. If the request was saved, the error says so and the request stays queued. `herdr-epic pending` lists it. The orchestrator's rules tell it to handle that list on start. A request that could not be saved exits 1 and says nothing was sent. A request for a Done epic is not saved: a closed epic does not come back, and the root assistant starts a new one instead. `task add` refuses a Done epic the same way. Both also refuse an archived epic, and say to run `herdr-epic unarchive` first.

The info block tells the worker to leave other repositories alone, and to leave `herdr-task add` and `herdr-task start` to the orchestrator.

## The sidebar

`herdr-epic install` writes a layout named `shepherdr-epics`. It is your installed Shepherdr layout with the spaces list wrapped in a stack. A switch in the stack header offers **By epic**, **By repo**, and **Spaces**. The choice is remembered on this window. Spaces is Shepherdr's own spaces sidebar (new space, rename, pin, groups, machines).

By epic lists each root assistant first (name and path). Under that, each open epic: the title, and a summary such as `4 agents · 2 working`. The agent count is how many of the epic's tasks have a live terminal. While any task needs you, the row stays marked when the epic is folded, and a `! N` badge shows how many. Expanding an epic shows its tasks, two lines each. The first line is a status mark, the repository name, the task id, the title, and, while the task is working, progress such as `60% 42m`. The second line is what the task is waiting on, drawn in the theme's yellow, or the dimmer line Shepherdr reports for what it is doing. Tasks that belong to no epic and are not done sit under **Unassigned**, grouped by repository. Epics whose state is `done` sit under **Done**, folded. Archived epics, open or Done, show only under **Archived (N)** at the bottom, folded.

By repo lists every repository named by an epic or a root. A row shows the name, the current branch, and how many commits `HEAD` is behind its upstream (`↓3` when `git` can count them). Its tasks use the same two lines, with the epic's title at the end of the second line. Tasks of archived epics are left out.

Click a root or an epic title to open that space. If the space is gone, the sidebar runs `herdr-epic root show` or `herdr-epic show` and then selects the space those commands report. `show` creates a missing space again only while the epic is open. A Done epic's space stays closed. Click a task to open Shepherdr's task view when this layout has one, or to peek the task's terminal. A task with no terminal and no log shows "This task hasn't started yet."

Hover an epic row to see an archive button at its end; inside **Archived** the button unarchives. The sidebar runs `herdr-epic archive` or `unarchive` and reads the board again. When the command refuses, for example because a task is still running, a warning shows its message, which names those tasks. Unarchiving puts the epic back where its state says, open or Done.

When Shepherdr's task list cannot be read, the board keeps each epic's tasks with status `unknown`, sets `error`, and the sidebar shows a warning line above the list. The list reloads when a file under an epic root or under `~/.herdr/tasks` changes, and on a 10-second timer. Words are English. `"language": "zh"` in the config switches the sidebar and the CLI messages to Chinese.

## Requirements

- Herdr app 0.1.12 or newer
- herdr-core 0.1.17 or newer (`hc`)
- Shepherdr 3.1.x, tested on 3.1.3, already installed, so `~/.config/herdr-app/layouts/shepherdr.json` exists and `herdr-task` is on `PATH`
- Herdr's files in `~/.config/herdr-app`. A `HERDR_APP_HOME` pointing anywhere else is not supported in v0.1: `install` and `doctor` stop and say so
- `~/.local/bin` on `PATH`, so the sidebar finds the `herdr-epic` that `install` links there
- Python 3.8 or newer (`python3`, standard library only)
- Git, for any repository you attach to an epic
- macOS or Linux

`herdr-epic doctor` checks the app, core, and Shepherdr versions, and that `herdr-task` is on `PATH`. It does not probe Python.

## Install

From a clone of this repository, after the Herdr app has been opened once and Shepherdr is installed:

```
./install.sh --dry-run
./install.sh
herdr-epic doctor
```

`install.sh` runs `python3` on this clone's `bin/herdr-epic install`. The dry run prints every action and writes nothing. Install writes into `~/.config/herdr-app` and records itself under `~/.config/herdr-epic`. It does not write `config.json`; `root add` does. This install is for macOS and Linux.

`~/.local/bin` has to be on `PATH`. The sidebar mod and the agents run `herdr-epic` by that name, with no directory in front of it. The Herdr app has to be running for `root add`, `new`, `request`, `show`, and `root show`: those commands open spaces and start agents through it.

Install then:

- Writes `~/.config/herdr-app/layouts/shepherdr-epics.json`, generated from the Shepherdr layout. Shepherdr's own files are left untouched.
- Copies this repository's `mods/epics/` to `~/.config/herdr-app/mods/epics/` (dotfiles, `.d.ts`, and `jsconfig.json` are skipped).
- Links `~/.local/bin/herdr-epic` to this clone's `bin/herdr-epic`. Later runs use that clone, so leave the clone where it was at install time.
- Records file hashes and the link target in `~/.config/herdr-epic/installed.json`.

A file this tool installed earlier, still unchanged, is replaced. The old bytes are copied to `~/.config/herdr-epic/backup/<time>/`. A file you have edited, or a file that was already there and was not installed by herdr-epic, stays put. The new bytes go beside it with a `.new` suffix. A `~/.local/bin/herdr-epic` that already points somewhere else is left alone.

The same actions are `herdr-epic install --dry-run` and `herdr-epic install`.

## Uninstall

```
herdr-epic uninstall --dry-run
herdr-epic uninstall
```

Uninstall removes every recorded file that still matches its hash. That includes an unchanged `.new` file it wrote beside something it refused to overwrite. It removes the symlink when it still points at this clone, and it removes `~/.config/herdr-app/mods/epics` when that folder is left empty. Edited files stay on disk. When nothing edited was kept, it deletes `~/.config/herdr-epic/installed.json`. When something was kept, `installed.json` stays and lists those files. Epic roots stay. `~/.config/herdr-epic/config.json` stays too: install never creates it, `root add` does. Backup copies stay, and the command prints where they are. When it removes `shepherdr-epics.json`, it also prints a reminder to leave that layout first with Ctrl+Shift+L.

## Doctor

```
herdr-epic doctor
```

Each line is `ok` or `FAIL`. The process exits 0 when every check passes, and 1 otherwise. It checks:

- `herdr-app` is at least 0.1.12, `hc` at least 0.1.17, and `shepherdr` at least 3.1
- `herdr-task` is on `PATH`
- `~/.config/herdr-epic/config.json` parses, or says the defaults are in use
- each configured root directory exists
- `shepherdr-epics.json` still matches the Shepherdr layout it was generated from
- the installed Shepherdr sources still contain `herdr.task.space`, `herdr.coordinator.space`, `herdr.role`, `herdr.chat.slot`, and `tell-assistant`

`--json` prints `{"checks": [...]}` and uses the same exit code.

## First steps

Register a root. The folder is wherever you want epic folders to live. `--name` is the label on the root assistant. Each `--repo` is optional and has the form `NAME=PATH` or `NAME=PATH@BRANCH`.

```
herdr-epic root add ~/work/shop --name shop --repo shop-api=~/work/shop-api --repo shop-web=~/work/shop-web --repo shop-ios=~/work/shop-ios
```

`@BRANCH` is stored only when the branch name contains no slash. `shop-api=~/work/shop-api@dev` stores base `dev`. A name like `feature/gift` is kept as part of the path, and the base stays empty.

The command creates the folder, writes `root.json` and `AGENTS.md`, makes `CLAUDE.md` a link to `AGENTS.md`, opens a Herdr space for the folder, and saves the root in the config.

In Herdr, press Ctrl+Shift+L until the layout is `shepherdr-epics`.

Open the root assistant (the top row of By epic, or the new space) and ask it to start an epic. For example: start an epic called Gift cards for shop-api, shop-web, and shop-ios, so a shopper can buy and redeem a card. The assistant runs `herdr-epic new`. The same command from a shell:

```
herdr-epic new "Gift cards" --root shop --repo shop-api=~/work/shop-api --repo shop-web=~/work/shop-web --repo shop-ios=~/work/shop-ios --message "Let a shopper buy and redeem a gift card."
```

`new` accepts zero `--repo` flags. Each path you do pass has to contain a `.git` entry. With more than one root, pass `--root`. The orchestrator starts in the new space with your message as its first line. If it cannot be started, the epic folder still exists and the command exits 1 with the reason.

## The epic folder

Configuration is `~/.config/herdr-epic/config.json`:

```json
{
  "roots": [{ "name": "shop", "path": "/home/you/work/shop" }],
  "language": "en"
}
```

`language` is `en` or `zh`. With no file, the only root is named `epics` at `~/.herdr/epics`, and the language is English. The first `root add` drops that default when `~/.herdr/epics` does not exist. `HERDR_EPIC_CONFIG` selects another config file.

A root looks like this:

```
shop/
  AGENTS.md
  CLAUDE.md
  root.json
  Gift cards/
    epic.json
    epic.md
    decisions.md
    handoff.md
```

`root add` writes `AGENTS.md` from herdr-epic's template. The file is the rules for the root assistant and the orchestrator, plus a short worker section that points at the info block. `root.json` records the sha256 of the template it wrote (`agents_sha256`). Running `root add` again, or `herdr-epic install` (for every configured root), replaces an `AGENTS.md` that still matches that hash with the current template. If `AGENTS.md` is there and you have changed it, your file stays and the template is written to `AGENTS.md.new`. `CLAUDE.md` is a symbolic link to `AGENTS.md`, so the rules have one source: Claude Code reads them through the link, other harnesses read `AGENTS.md` on their own. It is a link and not an `@AGENTS.md` import on purpose: orchestrators work one folder below the root, and Claude Code treats an import from a parent folder as external and stops at a confirmation screen before the session starts. A root still holding the old one-line `@AGENTS.md` file from herdr-epic 0.1.0 gets the link on the next `root add` or `install`; a `CLAUDE.md` of your own is left alone (then link it yourself: `ln -sf AGENTS.md CLAUDE.md`). `root.json` holds the root's name, its space id, the default repositories, and that hash.

`herdr-epic new` names the epic folder from the title. `/`, `\`, `:`, and control characters become `-`. Leading and trailing spaces and dots are stripped. Other characters stay, including spaces, so the folder can be `Gift cards`.

`epic.json` is the record the CLI keeps: `version` (1), `id` (`e-` plus four hex digits), `title`, `space`, `machine` (`null`), `state` (`open` or `done`), `created`, `closed`, `archived` (when the epic was archived, or `null`; a file without it reads as not archived), `repos` (`name`, `path`, `base`), `tasks`, and `requests`, plus `former_spaces` once the epic's space has been created again (earlier space ids, still matched when a task's label names one). The CLI is the only writer. Each update holds an exclusive lock on `epic.json.lock` in the epic folder and replaces the file atomically. Task entries store the ref, repository, title, time added, and `requested_by`. They do not store task status. Request entries store the id, the requester's terminal and task, the repository, the agent, the context, and the state (`queued`, `delivered`, `done`, or `replied-undelivered`).

`herdr-epic new` also writes the three markdown files: `epic.md` from a fixed skeleton (background, goal, scope, what is out of scope, acceptance, related docs, and a summary heading for when the epic closes), and empty `decisions.md` and `handoff.md`. After that, the orchestrator is the writer. It keeps `decisions.md` and `handoff.md` append-only, each entry under a `## <date time>` heading. `epic.md` is the requirement. `decisions.md` is what you decided and why. `handoff.md` is where the work stands and how the tasks connect. The folder has no `tasks.md`. Live task state stays in Shepherdr, and `herdr-epic show` reads it from there.

Workers are told to read those three files before they start, and to leave the epic folder unchanged. That rule is in the info block. The files themselves are ordinary files.

`herdr-epic whoami` says which role the current terminal has. A worker carries Shepherdr's task label and its task space is an epic space. An orchestrator is the coordinator of an epic space, or any terminal whose working directory is the epic folder or somewhere inside it. The root assistant's working directory is the root. Any other terminal is role `none`. It is shown a `request` example that includes `--epic`, and `herdr-epic list` is how it learns the ids and titles.

## Commands

Every command accepts `--json` in any position. The flag is removed before the command runs. Success prints one JSON object on stdout. A usage or lookup error prints `{"error": "..."}` on stdout and exits 1. Without `--json`, that error goes to stderr. `task add`, `new`, and `request` add extra fields when part of the work was saved before the failure.

The names below are every command registered in `herdr-epic`.

| Command | What it does |
|---|---|
| `root add PATH [--name NAME] [--repo NAME=PATH[@BASE]]...` | Create the root, write `root.json`, `AGENTS.md`, link `CLAUDE.md` to it, open its space, and save it in the config. |
| `new TITLE [--root NAME] [--repo NAME=PATH[@BASE]]... [--message TEXT]` | Create the epic folder and its files, open its space, and start the orchestrator. `--repo` may be omitted. Each repository you do pass must contain `.git`. When the orchestrator starts but does not receive the opening message, that message is saved as a queued request with no repository (`pending` lists it) and the command exits 1. |
| `restart [EPIC]` | Replace an epic's orchestrator with a fresh one (for one stuck on a start screen, closed, or not answering). The new one starts with the person's Shepherdr Assistant settings and is told to handle `herdr-epic pending` first. An orchestrator cannot restart itself; a closed epic is refused. |
| `task add --repo NAME --title TITLE --prompt TEXT or --prompt-file FILE [--agent A] [--after REFS] [--base BRANCH] [--set K=V]... [--requested-by REF] [--epic KEY]` | Orchestrator only. A root is refused and relays with `herdr-epic request --epic <id|title> --repo ...`. Adds a Shepherdr task in that repository, appends the info block, records the task, and starts it. `--base` is the branch this one task starts from and merges into; without it the repository's `@BASE` applies. |
| `repo add NAME=PATH[@BASE] [--epic KEY]` | Orchestrator only. A root is refused and relays with `herdr-epic request --epic <id|title> --repo ...`. Adds a git repository to the epic. |
| `board` | Print the sidebar's data. Text mode is one line per epic. `--json` is the full board. |
| `list [--archived]` | One line per epic: id, state, task count, how many are working, how many need you, title. Archived epics are left out, and a last line says how many. `--archived` lists them too, marked `archived`. `--json` gives each epic's `archived` (time or `null`). |
| `show [EPIC]` | One epic, its repositories, and each task's live status. Creates a missing space again, except when the epic is Done (`state` `done`): that space stays closed. |
| `close [EPIC]` | Orchestrator or root. The root passes the epic. Set state to `done`, set `closed`, and keep the folder. |
| `archive [EPIC]` | The person (any terminal outside an epic, such as the sidebar), the root, or the orchestrator; a worker is refused. Set `archived` to the current time. Open and Done epics can both be archived; `state`, the tasks, the folder and the space stay as they are. Refused (exit 1) while any of the epic's tasks is `queued`, `working`, `needs`, `ready` or `merging`, or `unknown` because the task store can't be read; the message names each one (ref, status, title) and `--json` adds them as `blocking`. Tasks that are `todo`, `done` or `missing` don't block. An archived epic refuses `request` and `task add` until it is unarchived; `show` still works. |
| `unarchive [EPIC]` | Same callers as `archive`. Set `archived` back to `null`. Archiving an archived epic, or unarchiving one that isn't, changes nothing and exits 0 (`--json`: `changed` is `false`). `--json` for both: `id`, `title`, `archived`, `changed`. |
| `root show [NAME]` | Print one root. Pass the name when several roots exist. Creates the space again when it is gone. |
| `request --repo NAME [--agent A] [--epic KEY] --context TEXT or --context-file FILE` | Save a request and deliver it to the orchestrator, starting one when none is running. A worker omits `--epic`. The root assistant uses it to relay work for an open epic, and answers "what needs you" across epics with `list` and `show`. |
| `reply REQUEST_ID TEXT [--result REF] [--epic KEY]` | Orchestrator only. Record the answer and send it to the requester's terminal when that terminal is live and idle, working, or done. |
| `pending [--epic KEY]` | List requests still `queued`, and replies stored as `replied-undelivered`. |
| `whoami` | Print this terminal's role. In an epic, also the id, folder, repositories, and files to read, then a `request` example. |
| `install [--dry-run]` | Install the mod, the generated layout, and the `herdr-epic` link. |
| `uninstall [--dry-run]` | Remove unchanged recorded files, including unchanged `.new` files, and this clone's link. Removes an empty `mods/epics` and deletes `installed.json` when nothing was kept. Roots and `config.json` stay. |
| `doctor` | Check versions, config, roots, the generated layout, the Shepherdr strings this tool looks for (labels, `tell-assistant`, `tasks.thread`, `tasks-thread`, `panel-body`, `coordinator.slots`), that `herdr-epic` on `PATH` is this clone, and that `HERDR_APP_HOME` is unset or `~/.config/herdr-app`. |

`EPIC` and `--epic` match an id (`e-7f3a`), a title (`Gift cards`), a folder name, or an absolute folder path. Two matches print both and exit 1. From inside an epic, the orchestrator's commands use that epic when `--epic` is omitted. `task add`, `repo add`, and `reply` refuse both a worker and the root assistant. Only `close`, `archive` and `unarchive` accept the root. A worker is pointed at `herdr-epic request --repo ...`. A root that runs `task add` or `repo add` is pointed at `herdr-epic request --epic <id|title> --repo ...`.

`reply` exits 0 even when the text never reached the terminal. The output says the reply was recorded and not delivered, `epic.json` stores `replied-undelivered`, and `pending` lists it so it can be sent again. A blocked agent, or a terminal sitting on a start screen, is left alone on purpose: typing there can dismiss a prompt.

`task add` forwards every `--set K=V` to `herdr-task`. The orchestrator's rules say to record an epic-wide wish (review, proof, merge) in `decisions.md` and to pass the matching `--set` on later tasks. Other review and merge settings stay on Shepherdr's settings for that repository. If the task is recorded and then fails to start, `task add` exits 1 and prints the `herdr-task start` line to run. A start that Shepherdr queues exits 0 and includes the reason.

## What this version does not cover

A repository space's own Shepherdr Assistant loads no epic rules. A worker whose task belongs to no epic gets no info block. An agent you start by hand gets no info block either. Those agents can run `herdr-epic request --epic "Gift cards" --repo shop-ios --context "..."` when you give them the epic. herdr-epic does not edit a global `CLAUDE.md` or a global `AGENTS.md`.

An epic and its tasks stay on this machine. `epic.json` stores `"machine": null`, and the search for an orchestrator skips any terminal address that contains a slash.

The epic grouping is the desktop layout `shepherdr-epics`. A phone, and Shepherdr's web app, show tasks the way Shepherdr already shows them.

Each task is still reviewed and merged by itself, under Shepherdr's rules for its repository. herdr-epic does not commit the epic folder.

## Known risks

**Shepherdr upgrades.** This tree was tried against Shepherdr 3.1.3. A later Shepherdr can change label names, which Assistant receives task news, or the assistant template. `herdr-epic doctor` checks that the installed `shepherdr` is at least 3.1 and that the labels and `tell-assistant` still appear in the installed files. A `FAIL` on those lines means request delivery and the sidebar may no longer match that Shepherdr.

**The generated layout goes stale.** `shepherdr-epics.json` copies the Shepherdr layout from install time and swaps the sidebar. When `layouts/shepherdr.json` changes, `doctor` reports the mismatch and tells you to run `herdr-epic install` again. If you have edited `shepherdr-epics.json`, install keeps your file and writes `shepherdr-epics.json.new`.

**Worktrees from another tool.** Shepherdr can report worktrees under `~/.herdr/worktrees` that it does not recognize, and it sends that report to its Home Assistant. The check is Shepherdr's. herdr-epic does not scan those directories and does not delete them. An older task tool still creating worktrees on the same machine can make those reports appear. herdr-epic does not read that tool's tasks.

**Parent `CLAUDE.md` files.** The orchestrator runs with the epic folder as its working directory. Claude Code also loads `CLAUDE.md` from parent directories. A root placed inside a tree that already has one (notes, a repository) brings that text into the orchestrator's context. That background is often what you want. The root's own `CLAUDE.md` is a link to its `AGENTS.md` (see above).

**Tasks from before a space was created again.** When an epic's space is closed and herdr-epic creates it again, tasks started before that keep sending Shepherdr news to the old space, which no Assistant receives. Their workers still find the epic (`former_spaces`). Reopen or restart those tasks, or tell the orchestrator to re-scan the epic (`herdr-epic show --json`).

**One orchestrator per epic.** Each open epic can keep its own Assistant session, so model usage grows with the number of epics. herdr-epic starts an orchestrator when the epic is created, and when a request arrives and none is running. It does not call the model on a timer while the orchestrator is idle. The sidebar's 10-second timer only re-reads the board.

## Evaluation

Results: pending.
