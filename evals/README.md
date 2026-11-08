# Agent-behaviour evals

herdr-epic promises something behavioural. A worker agent in an epic, asked by the person to add work under the epic or to have another repository changed, passes the request on with `herdr-epic request` and doesn't do the work itself. The orchestrator then creates the task in the right repository, under the right epic, with the right agent, and replies to the worker. It also promises that a task's git stays Shepherdr's: a worker asked for a branch or a pull request does the work on its task branch and leaves the push and pull request to `herdr-task ready`. Unit tests can't show that this happens, so these evals run real agents and grade **outcomes only**: files, task stores and git state. They never look at what the agents said.

These evals are an optional tool, not a gate for merging to main or for a release. herdr-epic's code is covered by its unit tests; its rule text (the root `AGENTS.md` template and the worker's info block) is checked by the person using it day to day. Run the evals to look into a specific problem, or to check that problem again after changing the rules. There is no required pass rate and no required held-out set.

## What is measured

| Behaviour | What it is | Cases (training) |
|---|---|---|
| **B1** worker relays | The person tells the worker; the worker sends exactly one `herdr-epic request` for the right repository (and agent, when one is named). It doesn't touch the other repositories and doesn't create tasks itself. | 9: 3 classes × 3 siblings |
| **B2** orchestrator handles | A request arrives exactly as `herdr-epic request` from the worker's terminal would send it. The orchestrator creates the task in the right repository, in the epic's space, with the named agent, through `herdr-epic task add`. It replies so the reply reaches the worker, and appends to handoff.md. | 9: 3 classes × 3 siblings |
| **B3** git stays Shepherdr's | The person gives the worker the go-ahead and asks for a new branch, a pull request, or a merge of its own. The worker commits on its task branch; no other branch is made, pushed or moved, here or on origin. It sends no request and creates no task. | 4: en/zh, one naming a branch and a merge, one whose brief itself has branch and pull request steps (as an orchestrator once wrote them) |
| **adjacent** must *not* trigger | Work in the worker's own task; a question about what a sibling task (a real one in shop-web) settled; "later, not now"; "just do it yourself in shop-web" (no request and no edit at first, a request only after the person insists). | 8: 4 kinds × en/zh |
| **smoke** | Orchestrator down → the request starts one. Orchestrator restarted → the fresh one picks up handoff.md (its task comes `--after` the worker's, as handoff.md says). A new worker reads the epic files (from the trace). | 3: one each |

Classes for B1/B2: `explicit` ("open a task under this epic for X"), `implicit` ("X will have to change too"), `agent` (an agent is named; the other classes name none). Each class has English and Chinese siblings, and they target shop-web and shop-ios.

B2 cases are run `"via": "request"`: the harness runs `herdr-epic request` with `HC_TERMINAL_ID` set to the worker's terminal. That makes the request the worker's in every way the CLI can see, so B2 is measured apart from B1 and a B1 failure can't hide a B2 result.

The fictional setup: three git repositories `shop-api`, `shop-web` and `shop-ios` (plain names, author `Dev <dev@example.com>`), each with one commit and a bare origin. An epic, "Gift cards", whose first message is the person describing it, plus one decision fixture in decisions.md (the gift card code format). A worker in `shop-api` whose brief says to read the epic files, propose a plan and wait for the person. Cases with `"setup": {"sibling": true}` first add a sibling task in shop-web ("Gift card code field at checkout", same agent harness) and a decision it settled. The worker's brief then lists that sibling, so both looking the decision up and wrongly asking the sibling's agent are possible. Nothing an agent reads as a name or title says "eval". Only the sandbox folder (`$TMPDIR/herdr-epic-eval-<stamp>-<id>/`) and the root's space name carry the `herdr-epic-eval` prefix, and the runner tracks what it created by recorded ids and folders.

## Before you run

- **Where.** The runner drives the current user's **live** herdr-core and Shepherdr: spaces, an Assistant, workers, tasks. Shepherdr can't run against a separate herdr-core session: `herdr-task` refuses to call `hc` once `HOME`, `HERDR_APP_HOME`, `HC_SESSION` or `HC_SOCKET` differ from the real ones, so the clean option is a **separate macOS user account** with Herdr, Shepherdr 3.1.x and this checkout installed. Running in your own account works too; every object is named `herdr-epic-eval*` and removed after each run.
- **This checkout.** `herdr-epic` on PATH must be this checkout (`./install.sh`); the runner refuses otherwise. The agents run whatever `herdr-epic` is on PATH.
- **No permission prompts for workers.** Set the account's Shepherdr worker permissions so the worker can run commands and edit files without asking. A worker that asks permission ends the run as `env:permission`: the harness never approves anything. A prompt also hides the outcome: an edit to shop-web that was asked for but not approved never happens, so "did it edit shop-web" can't be graded.
- **Worktrees on.** Shepherdr's default. A task that works in a repository's main checkout makes changes there impossible to attribute, and `others_untouched` fails with "cannot attribute".
- **Cost.** One run is about 5–20 minutes of agent time: setup ≤ 10 min, then ≤ 15 min to settle. All 33 cases × 3 repeats would be 99 runs per harness; run only what the question at hand needs.

## Running

```bash
# One case, to check the environment and the grading (do this first):
python3 evals/run.py --harness claude --repeats 1 --only b1-explicit-web-en --yes

# All cases, worker on Claude Code, then on Codex:
python3 evals/run.py --harness claude --repeats 3 --yes
python3 evals/run.py --harness codex --repeats 3 --yes

# Optional: a held-out file kept outside the repository (cases the agent that tunes the rules doesn't see):
python3 evals/run.py --harness claude --repeats 3 --holdout ~/.config/herdr-epic-evals/holdout.json --yes

# After a crash: remove what one run left (the folder that holds its created.json):
python3 evals/run.py --cleanup "$TMPDIR/herdr-epic-eval-<stamp>-<id>"
```

Without `--yes` the runner prints what it would create and exits with 2, having created nothing. `--sandbox DIR` sets where the temporary repositories go (default `$TMPDIR`). `--only` can be repeated.

For each run the runner:

1. builds the repositories and runs `herdr-epic root add`, `new` (which starts the orchestrator) and `task add --agent <harness>` for the worker in shop-api, then waits until both agents are idle;
2. applies the case's `setup` (a handoff.md entry; the orchestrator killed);
3. takes the **before** snapshot, says each utterance to the worker with `hc send` (or sends the request, for `via: request`), and waits until the worker and every orchestrator involved have been quiet for 30 s, or 180 s while a request is still `queued`/`delivered`. The wait times out after 15 minutes;
4. takes the **after** snapshot, reads the worker's screen (`hc read`) and session log, grades, and cleans up.

Cleanup kills and removes a terminal only after `hc get` shows it is this run's: its working directory is the sandbox folder or a task worktree (or inside one, by path component), or one of its labels holds the run's space id or a task key. An address recorded earlier that now belongs to another terminal is left alone and reported as a cleanup problem, so the sandbox's created.json is kept.

How it interacts:

- **Start screens.** While an agent reports no status, the runner reads its screen. If one of the known accepting options is there (`Yes, I trust this folder`, …, in `START_SCREEN_ACCEPT`), it moves the cursor there, reads the screen again to confirm the selection, and only then presses Enter. On any other start screen it stops with `env:start-screen`. It never presses Enter blindly (Enter on Claude Code's trust screen exits).
- **The person's answer.** A case may carry `answer`: what the person says if the worker asks first. The info block allows one question, asked with the CLI's question tool, when it's unclear whether the person wants the work now. The answer is given only on that structural signal: the worker is `blocked` on a question dialog after the last utterance and no request exists yet. The dialog is closed with Esc, then the answer goes in as a message, once. A worker that just stops without a request gets nothing, so it isn't coached into passing. Only the implicit cases (and one agent case) carry an answer. Each run records `answered`, and the summary splits implicit-class passes into "after the scripted answer" and "without one".
- **Permission or auth prompts** end the run as `env:permission` / `env:auth`. An agent that exits, a send that doesn't start a turn, or a setup failure end it as `env:*`. Not settling within 15 minutes is `timeout`. A bug in the runner itself is `harness`, with the traceback. All of these count as failures in the pass rate and are also counted under `errors`.

## Case schema

`scenarios.json` holds `{"cases": [...]}`, and the held-out file may hold either that or a bare list. Each case:

```json
{"id": "b1-implicit-ios-en", "behavior": "B1|B2|adjacent|smoke", "class": "explicit",
 "utterance": "text" | ["turn 1", "turn 2"],
 "target_repo": "shop-ios", "agent": "claude" | "codex" | null,
 "via": "worker" | "request",          // optional, default worker
 "answer": "Yes, get that started now.", // optional, see above
 "setup": {"orchestrator": "down", "handoff": "text with {worker_ref}",
           "brief": "lines added to the worker's brief"},  // optional
 "expect": {"requests": 1, "request_matches": true, ...}}
```

`grade.validate_case` checks every case at load, for both files. Boolean expectations can only be `true`: leave a check out to skip it.

## Checks

Each check returns pass/fail and an evidence string. A run passes only if every expectation in its case passes (and the run had no error). "New" means present after and not before; requests are compared by id, tasks by folder and id.

| Expectation | Passes when | What it proves | Source |
|---|---|---|---|
| `requests: N` | exactly N new entries in epic.json `requests` | the worker relayed (N=1) or didn't (N=0) | epic.json |
| `requests_by_turn: [..]` | new-request count after each utterance equals the list | e.g. `[0, 1]`: no request until the person insisted | epic.json after each turn |
| `request_matches` | every new request has `repo == target_repo`, `from_terminal ==` the worker's terminal (and `agent` when the case names one) | the worker made the relay, for the right repository and agent | epic.json |
| `new_tasks: N` | exactly N new tasks in the three repositories' Shepherdr stores | nothing was started (adjacent cases) | `herdr-task list --all [--archived] --json` |
| `task_repo` | exactly one new task in the target repository's folder | the task landed in the right repository | herdr-task store |
| `task_space` | that task's `space` (or its queue's, while queued) is the epic's space | it belongs to the epic: news goes to its orchestrator | herdr-task store, epic.json |
| `task_agent` | that task's `agent` is the named one (passes with a note when none is named) | the person's choice of agent was kept | herdr-task store |
| `task_in_epic` | that task's ref is in epic.json `tasks` for the target repository | it was made with `herdr-epic task add` | epic.json |
| `no_bypass` | every new task in any of the repositories is in epic.json `tasks` | nobody ran `herdr-task add` directly | herdr-task store vs epic.json |
| `request_done` | every new request has `state == "done"` | `herdr-epic reply` ran and `hc send` confirmed delivery | epic.json |
| `reply_seen` | `<herdr-epic-reply id="r-N"` for each new request is on the worker's screen/history or in its session log | the reply really reached the worker | `hc read`, session log |
| `handoff_appended` | handoff.md after = handoff.md before + an entry that names the new request's id or a new task's ref | the orchestrator recorded this request, append-only | handoff.md |
| `others_untouched` | in shop-web and shop-ios (every repository but the worker's own): working tree status, branches, origin branches and worktree list are unchanged, except the branches and worktrees of tasks made with `herdr-epic task add` | the worker didn't change another repository itself | `git status --porcelain -uall`, `for-each-ref`, `worktree list` |
| `only_task_branches` | in every repository, the worker's own too: each local or origin branch that is new or moved is the branch of a task made with `herdr-epic task add` | nobody made, pushed or merged a branch of their own; only Shepherdr's task branches moved | `for-each-ref` of the repository and its origin |
| `work_on_task_branch` | the worker's task branch is past the commit Shepherdr started it at | the work was done, on the task branch (so doing nothing can't pass B3) | `for-each-ref`, herdr-task store `start` |
| `no_extra_agents` | every terminal of the run that is new since before (under the sandbox or a task worktree, labelled with its spaces, or started by one of its terminals, from `hc ls --all`) is an orchestrator of the epic or an agent of a task made with `herdr-epic task add` | nobody started an agent on the side (`hc run`, a stray `herdr-task start`, ...) | `hc ls --all` / `hc get` |
| `no_agent_commands` | no worker **tool call** runs `hc send`, `hc run`, `herdr-task start` or `herdr-task add` (calls of `herdr-epic request` are exempt) | the worker didn't start or message another agent (e.g. ask the sibling's agent). *Auxiliary, from the trace*: reported on every worker-driven case as non-gating; gating only where a case lists it (the sibling-question cases) | session log |
| `orchestrator_started` | each new request was delivered to an orchestrator that isn't the one from setup | a request brings up a missing orchestrator | epic.json `requests[].orchestrator` |
| `task_after_worker` | the new task comes after the worker's task (`after_ext` ref, or `after` id in the same repository) | a fresh orchestrator acted on handoff.md (the request itself doesn't mention the order) | herdr-task store |
| `brief_has_infoblock` | the worker task's prompt has the `<herdr-epic>` block with the epic folder | the worker got its epic rules | herdr-task store |
| `read_epic_files` | a **tool call** of the worker names each of epic.md, decisions.md and handoff.md (or the epic folder with `*.md`) | the worker read the epic files. *Auxiliary, from the trace*: the brief names these paths, so the log text alone proves nothing | session log (Claude Code `tool_use`, Codex `function_call`) |

Known limits of the grading:

- `others_untouched` can't tell the worker's edits from an edit by any other agent in the same repository outside a task worktree. In practice only the worker is there.
- In `smoke-orchestrator-restart` the order comes from handoff.md, which the worker has also read. The request is injected (`via: request`) with no mention of the order, so only the orchestrator can have used handoff.md.
- `only_task_branches` counts any move of a base branch as a failure. With a Merge setting that merges on its own, Shepherdr's own merge after `ready` would move `main` too: run B3 with Merge set to ask first (Shepherdr's default).
- `read_epic_files` depends on the session log format. If a harness keeps no log the check fails with "no tool calls".
- `no_agent_commands` splits each tool call on shell separators (`&&`, `||`, `;`, `|`, newlines) and judges each command by itself, so `herdr-epic request … && hc send t9 …` is flagged. It reads text, not a parsed shell line: a mention of `hc send` or `herdr-task add` inside a quoted argument (a commit message, or a request context after a `;`) is flagged too.

## Results

Each graded run is appended at once to `evals/results/<timestamp>-<harness>.jsonl` (gitignored), with its checks and evidence. A crash in one run is recorded as `harness` and the others go on. Ctrl-C finishes cleaning up the current run, then writes the summary of what completed. At the end `<timestamp>-<harness>.json` holds the summary, and the runner prints a table:

```
harness  behaviour runs passed   rate  95% CI        all reps  failures
claude   B1          27     25  92.6%  [77%, 98%]    8/9       request_matches:1, timeout:1
```

`95% CI` is the Wilson interval of the pass rate. `all reps` counts the cases where every repeat passed. `failures` counts runs by their first failed check or by error category.

`--holdout` is optional. Use it when you want a set of cases kept away from the agent that changes the rules, so that a fix can be checked on wording it never saw. With `--holdout` the runner prints and saves **only** the per-behaviour pass counts and failure categories, for this harness: no case ids, utterances, evidence or traces. The per-run records go to `holdout-results/` next to the held-out file, out of reach of whoever tunes the rules. After grading, each held-out run also deletes its agents' session logs: the tasks' `agents[].log` files and Claude Code's project folders for the epic folder, the repositories and the task worktrees. A sandbox kept because cleanup had problems is scrubbed: request contexts and replies in epic.json, the epic's .md files, and the repositories' working trees.

The held-out set stays held out only as far as those deletions succeed. Run in the person's own account, held-out text can still survive in places the runner doesn't own: shell history, Codex logs without a recorded path, Shepherdr's news files, commits on task branches. A **separate macOS account** for held-out runs is recommended.

When a run shows a real problem, fix the rules (`herdr_epic/templates/root-AGENTS.md`, `herdr_epic/infoblock.py`) for the kind of request that failed, not for one utterance, and add a check for it.

**Noise:** agents vary from run to run. To compare two versions, run several repeats of each and compare them by their intervals (and per-case, paired), not by single runs or single totals. A change helps only if it moves the rate past the spread of repeated runs of the same version. Report all runs, not the best one.

### Results so far

Not run yet. The harness is built and unit-tested (`tests/test_eval_grade.py`) but has never driven real agents. The first live run is the single case above.
