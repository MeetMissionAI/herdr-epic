# herdr-epic

In the Herdr window this is Shepherdr's layout with a new sidebar stack. A 44px header switches **By epic**, **By repo**, and **Spaces**. By epic lists each epic root's assistant, then each epic and its tasks. By repo lists repositories, their branch, how far behind they are, and the same tasks tagged with an epic. Spaces is Shepherdr's own spaces sidebar.

![herdr-epic](preview.png)

## What you see

The rest of the window is the Shepherdr layout this was generated from: its tab bar, its panes, its task panel. herdr-epic replaces the one `spaces-chat` block. The sidebar column keeps that layout's width, background, minimum, and maximum. The new stack takes the `size`, `min`, and `max` that belonged to the spaces block.

**Header, 44px.** Theme `chrome`, contents centered. Inset 10px from the left and right is a pill, full width, 28px tall, 2px of padding, 2px between segments, corner radius 7, background theme `raised`. Three equal segments, radius 5, 12px type, one line: By epic, By repo, Spaces. The selected segment is theme `background` with theme `foreground` and medium weight. The others are theme `muted`, and turn `foreground` on hover.

**List, the rest of the stack.** Theme `chrome`, 13px type. The scrolling column has 8px on the sides, 6px on top, 10px on the bottom.

By epic, top to bottom:

- One row per epic root, 30px tall, 8px horizontal padding, 8px gap, radius 7. A 14px folder icon in theme `muted`, the root name at 13px medium in `foreground`, and the path at 11px in `dim`, truncated. Hover paints theme `hover`.
- A 1px rule in theme `border`, 8px inset, 6px above and below, when at least one root exists.
- One block per open epic. The header row is 30px, 4px padding on the left, 8px on the right, 4px gap, radius 7. A 20px fold button holds an 11px chevron in `dim` (down when open, right when folded). The title is 13px medium: theme `ansi3` while any task needs you, `muted` when the epic is done, otherwise `foreground`. On the right, 11px `dim` text such as `4 agents · 2 working` (the working part is omitted at zero). When something needs you, the row background is theme `ansi3` with `1f` appended, the hover is `ansi3` with `33` appended, and a badge reads `! N`: 5px horizontal padding, 16px tall, radius 4, 10.5px semibold, text `ansi3`, background `ansi3` with `33` appended.
- While the pointer is over an epic row, a 20px button at its end, radius 5, hover `raised`, holds a 13px icon in `muted`: `archive` (tooltip "Archive this epic"), or `archive-restore` on an archived epic (tooltip "Unarchive this epic").
- Under an open epic, its tasks, or the 12px `dim` line "No tasks" indented 28px. With no open epics, "No epics yet. Ask a root assistant to start one." indented 12px.
- **Unassigned**, when any such task exists. Section header 28px, 8px horizontal padding, 6px extra on top, 11px medium `dim`. Chevron, the word Unassigned, and the count. Open by default. Each repository name is 11px `muted`, indented 28px, then that group's tasks.
- **Done**, when any epic not archived has state `done`. Same section header, the word Done, and how many epics. Folded by default. Open, it draws those epics the same way as the open ones.
- **Archived**, when any epic has `archived` set, open or done. Same section header, the word Archived, and how many epics. Folded by default. An archived epic shows here only, never among the open epics or under Done. Its block starts folded.

By repo, one block per repository, open by default:

- Header 30px, 4px left, 8px right, 6px gap, radius 7. Chevron in a 20px box, name at 13px medium. The branch, when git reports one, is a 12px branch icon in `dim` plus 11px `muted` text, the pair capped at 110px. When the count behind upstream is greater than zero, 11px `dim` text reads `↓3`. Folded, the task count sits at the end in 11px `dim`.
- Tasks, or "No tasks". Tasks of an archived epic are left out. With no repositories, "No repos yet."

A task is two lines, 8px horizontal padding, 4px vertical, 1px between the lines, radius 7. Hover is theme `hover`.

- Line 1 is 18px tall, 6px gap. The status mark sits in a 14px box so the row does not jump. Then, in By epic only, the repository name, capped at 88px, 11.5px, theme `muted`. The task id is JetBrains Mono at 10.5px in `dim`. The title takes the remaining width at 12.5px, `foreground`, or `muted` when the status is done. While status is `working`, the right side shows progress at 11px in theme `working`: a percent and a time left, joined by a space, either part omitted when absent (`60% 42m`).
- Line 2 is 16px tall, 20px indent, 6px gap, 11.5px. The text is the board's second line, or the status word when that line is empty. Theme `ansi3` when the task needs you, otherwise `dim`. In By repo, the epic title is a chip at the end: at most 110px, 5px horizontal padding, 15px tall, radius 4, background `raised`, 10.5px `muted`.

Status marks:

| Status | Mark |
|---|---|
| `working` | 12px spinner, theme `working`, one turn every 900ms |
| `merging` | 12px spinner, theme `done`, one turn every 1400ms |
| `needs` | filled 8px circle, theme `ansi3` |
| `ready` | filled 8px circle, theme `done` |
| `done` | filled 8px circle, theme `dim` |
| `missing` | 9px ring, 1.5px dashed border, theme `unknown` |
| `todo`, `queued`, `unknown`, anything else | 9px ring, 1.5px solid border, theme `dim` |

Before the first board arrives, the list is the 12px line "Loading…" in `dim`, or "Can't read the board" in `foreground` with the error under it in `dim`. Padding 14px. A board that carries `error` (Shepherdr's task list could not be read; its tasks come back as `unknown`) gets an 11.5px warning line in `ansi3` on `raised` above the list.

Spaces is the `spaces-chat` block the generator moved, unchanged, under the same switch.

## How it behaves

The sidebar does not read Herdr's agent statuses `working`, `blocked`, `done`, and `idle`. Row status is the task status on the board (`todo`, `queued`, `working`, `needs`, `ready`, `merging`, `done`, `missing`, `unknown`). A task that needs the person is `needs`: yellow second line, and the epic row stays yellow while it is folded. A live working task spins. There is no separate idle mark and no separate blocked mark.

Clicks:

- A switch segment runs the layout's stack selection and stores the choice. The selected segment does not handle the click.
- An epic's chevron folds that epic and does not open the space. The title beside it opens the space. Open epics start unfolded. Done and archived epics start folded.
- A root row opens that root's space.
- A repository header in By repo only folds. Repositories start unfolded.
- Unassigned starts unfolded. Done and Archived start folded. A section header toggles it.
- The archive button runs `herdr-epic archive <id> --json` (`unarchive` on an archived epic) without asking, reads the board again and redraws. It doesn't open the space or fold the epic. A non-zero exit notifies a warning titled "Can't archive · <title>" ("Can't unarchive · <title>") with the CLI's message: the JSON `error`, else stderr. The CLI decides what blocks; the mod checks nothing itself.
- A task row opens the task. With no terminal and no log, a notice says "This task hasn't started yet." When the layout has a `tasks-thread` block, the row opens Shepherdr's task thread and shows the side panel. With no such block, a live terminal is peeked. A row with a log and no live terminal still opens the thread when `tasks-thread` exists.

Hover paints theme `hover` on root rows, epic rows, repo rows, task rows, and the fold buttons. An epic that needs you uses the stronger `ansi3` wash instead. Inactive switch labels brighten to `foreground`. Nothing in the mod drags. The chevron swaps icon when a section folds. The spinner is the only continuous motion.

The board is read again when a watched folder changes, and every 10 seconds. Watched folders are each root path on the board and `~/.herdr/tasks`. A burst that only touches dotfiles does not reload; `epic.json` does. Herdr's own live lists are not polled for status. The spinner runs on its own and does not redraw the mod on a timer.

Fold state is remembered per list. The switch's last choice is remembered on the switch. Words follow the board's `language` (`en` or `zh`). Until a list has loaded, the switch uses English.

## Built from

Install reads `~/.config/herdr-app/layouts/shepherdr.json`, deep-copies it, and replaces the single block whose `content.mod` is `spaces-chat`. The stack id is `epics-sidebar`, `active` is `epics-by-epic`, and the header is 44px:

```json
{
  "id": "epics-sidebar",
  "type": "stack",
  "active": "epics-by-epic",
  "header": { "content": { "mod": "epics", "props": { "view": "switch" } }, "height": 44 },
  "children": [
    { "id": "epics-by-epic", "type": "block", "content": { "mod": "epics", "props": { "view": "epic" } } },
    { "id": "epics-by-repo", "type": "block", "content": { "mod": "epics", "props": { "view": "repo" } } },
    { "id": "epics-spaces", "type": "block", "content": { "mod": "spaces-chat" } }
  ]
}
```

`size`, `min`, and `max` move off the old spaces block onto the stack. The third child keeps every other field of that block, including its props, with its id set to `epics-spaces`. The layout root gains `cloned_from` `{ "name": "shepherdr", "version": "unknown", "sha256": "<canonical hash of the source layout>" }`. The file is `layouts/shepherdr-epics.json`. The generator fails when the source does not contain exactly one `spaces-chat` block.

The mod is `mods/epics/`: `main.js`, `views.js`, `i18n.js`, `archive-restore.svg`, and `mod.json` `{ "place": "none", "title": "Epics" }`. `place: none` means Herdr does not dock a fourth copy. The layout places three: `view` `switch`, `epic`, and `repo`. Any other `view` draws the epic list.

`herdr` calls: `exec`, `home`, `layout`, `notify`, `props`, `readTextFile`, `run`, `setShared`, `shared`, `spaces`, `theme`, `watchFolder`.

Commands: `layout.select` `{ stack: "epics-sidebar", child }` for the switch; `space.select` `{ id }`; `terminal.peek` `{ terminal }`; `layout.select` `{ stack: "panel-body", child: "tasks" }`, `{ stack: "tasks", child: "tasks-thread" }`, and, when that stack exists, `{ stack: "tasks-thread-body", child: "tasks-thread-chat" }`; `layout.show` `{ id: "panel" }`.

Programs, through `exec` by name, so `herdr-epic` has to be on `PATH` (install links it into `~/.local/bin`): `herdr-epic board --json` for the list; `herdr-epic show <epic id> --json` when an epic's space is missing; `herdr-epic root show <root name> --json` when a root's space is missing; `herdr-epic archive <epic id> --json` and `herdr-epic unarchive <epic id> --json` from the archive button. `show` creates that space again only when the epic's `state` is not `done`. A Done epic's space stays closed. The mod never calls `space.new`.

`herdr-epic board --json` is one object. `language` is `en` or `zh`. `roots` is `{name, path, space}`. `epics` is `{id, title, folder, space, state, archived, counts, tasks}`; `archived` is when it was archived (an ISO time) or null. `counts` is `{agents, working, needs}`: agents is how many tasks have a `terminal`, working is status `working`, needs is how many rows have `needs` true. `unassigned` is `{repo, tasks}` for live tasks no epic claims whose status is not `done`. `repos` is `{name, path, branch, behind, tasks}`; `branch` is a string or null, `behind` is an integer or null. An optional `error` string is set when `herdr-task list` itself failed. Each task row, in an epic, under unassigned, or under a repo, is:

```json
{
  "ref": "12",
  "repo": "shop-api",
  "title": "Issue a gift card",
  "status": "working",
  "needs": false,
  "line2": "Running the shop-api tests",
  "progress": { "done": 60, "left": "42m" },
  "epic": { "id": "e-7f3a", "title": "Gift cards" },
  "terminal": "t3",
  "key": { "machine": null, "folder": "/home/you/work/shop-api", "slug": "shop-api", "id": 12 },
  "log": null
}
```

`epic` is null on an unassigned row. `progress.done` is the report's percent, or null. `progress.left` is a short string (`42m`, `1h 5m`) or null. `terminal` is the live address, or null. `key` is null when the task has no `slug#id` key; `machine` in that object is null. `log` is `{path, kind, address}` only when there is no live terminal and an agent log exists, and null otherwise: `address` is that agent's ended terminal, which the sidebar hands Shepherdr's task view as `terminal` (as Shepherdr's Tasks list does), so an ended task opens on its conversation. A row for a task still in `epic.json` but gone from the store uses status `missing`, `needs` false, `line2` "Not in the task store any more", and null `terminal`, `key`, and `log`.

Shared keys: `epics.language` (the list copies write the board's language; the switch reads it) and `tasks.thread` (Shepherdr's task view: `terminal`, `task`, `title`, `key`, `log`, `at`). `localStorage` key `view` on the switch, key `open` on a list copy.

Icons, tinted, from Herdr's shipped `mods/workspace-chrome/`: `spinner.svg`, `chevron-down.svg`, `chevron-right.svg`, `folder.svg`, `git-branch.svg`, `archive.svg`. The mod folder does not bundle them. It bundles one icon Herdr doesn't ship, Lucide's `archive-restore.svg` (ISC), as `mods/epics/archive-restore.svg`. Task ids use JetBrains Mono. Every color is a `theme()` name. `props.fixture` is a board JSON path, with `~/` expanded via `home()`, read instead of running `herdr-epic`.

## Watch out

- The stack id has to stay `epics-sidebar`. The switch looks up that id in the layout tree.
- A Shepherdr layout with zero or two `spaces-chat` blocks cannot be wrapped.
- `props.fixture` skips the directory watch after the first read. The 10-second timer still re-reads the file.
- `localStorage` is per mod copy. By epic and By repo keep separate fold maps. The switch's saved view is on the switch copy. On load, if that saved id is one of the three children and the stack's `active` differs, the switch copy selects it.
- Opening a task Shepherdr's way needs layout ids `tasks-thread`, `panel-body`, `tasks`, and `panel`. `tasks-thread-body` is optional. Without `tasks-thread`, the fallback is `terminal.peek`, and a row with only a log shows the not-started notice.
- A missing space on an open epic is repaired by `herdr-epic show`, then `space.select`. A Done epic (`state` `done`) is left closed: `show` does not create its space. A missing root space is repaired by `herdr-epic root show`. A failed repair notifies with title `herdr-epic` and "Can't open this space. Try herdr-epic doctor."
- The mod needs the `herdr-epic` CLI on `PATH`. `exec("herdr-epic", ["board", "--json"])` uses that name. A non-zero exit leaves the list on "Can't read the board" and the stderr text.
- Watch paths are local. `watchFolder` does not take a machine. Dotfile-only events are ignored. The lock file `epic.json.lock` does not start with a dot, so a lock-only change reloads.
- Unassigned rows are live tasks claimed by no epic whose status is not `done`. A task listed in `epic.json` and missing from the store is status `missing`, with the second line "Not in the task store any more".
- "N agents" counts tasks with a live terminal address. It is not a count of `agents()` rows.
- Behind is `git rev-list --count HEAD..@{upstream}` in that repository. No upstream, or a git failure, leaves the number out. The branch is `git rev-parse --abbrev-ref HEAD`.
- The third child is Shepherdr's `spaces-chat`. Pin, rename, groups, and machines stay there. By epic and By repo do not draw them.
- An edited `shepherdr-epics.json` is not overwritten on a later install. The new file is `shepherdr-epics.json.new`, and the window keeps the edited layout until you swap it in.
- The mod ships no `keys.json` and no `focusable` flag. It does not fetch from the network.
- Chinese strings exist for `language` `zh`. An unknown language uses the English table. The switch stays English until a list copy has published `epics.language`.

## Done when

- `herdr-app check` on `mods/epics` exits 0.
- The generated layout has one stack `epics-sidebar`, header height 44, `props.view` `switch`, children `epics-by-epic` (`view` `epic`), `epics-by-repo` (`view` `repo`), and `epics-spaces` (the original `spaces-chat` block), `active` `epics-by-epic`, and the spaces block's `size`, `min`, and `max` on the stack.
- With `props.fixture` pointed at a board file, the epic view shows the root names, epic titles, and task ids from that file, and the first read does not run `herdr-epic`.
- Choosing By repo runs `layout.select` with stack `epics-sidebar` and child `epics-by-repo`. Choosing Spaces selects child `epics-spaces`.
- Clicking an epic title whose space id is already in `spaces()` runs `space.select` with that id. Clicking its chevron folds the tasks and does not select the space.
- Clicking a task that has a terminal, in a layout that contains `tasks-thread`, writes shared `tasks.thread` and selects `panel-body` / `tasks`, then `tasks` / `tasks-thread`, and shows `panel`.
- Clicking a task with no terminal and no log notifies "This task hasn't started yet."
- With a fixture holding an archived epic, By epic shows it only under a folded "Archived 1" header after Done, and By repo leaves its tasks out.
- Clicking the archive button on a hovered epic row runs `herdr-epic archive <id> --json` and not `space.select`; on an archived epic it runs `unarchive`. A non-zero exit notifies a warning with the CLI's error.
- A `needs` task draws its second line in theme `ansi3`. A `working` task draws the 12px spinner in theme `working` at 900ms and the progress text. A `missing` task draws the dashed ring in theme `unknown`.
