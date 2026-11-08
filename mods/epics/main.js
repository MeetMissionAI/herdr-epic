// The epics sidebar (README.md, "The sidebar"): one mod, three copies in the sidebar's stack.
//   props().view "switch": the stack's header, By epic / By repo / Spaces (layout.select).
//   props().view "epic" | "repo": the two views of `herdr-epic board --json`.
//   props().fixture: a board JSON file read instead of running herdr-epic (tests, screenshots).
// The board is read again whenever an epic root or Shepherdr's task store changes
// (watchFolder), and every 10 s as a fallback, as the Files panel does.

import { View } from "gpui-kit";
import { v_flex } from "gpui-base";
import { exec, home, layout, notify, props, readTextFile, run, setShared, shared, spaces, theme, watchFolder } from "herdr";
import { words } from "./i18n.js";
import { find, revealSteps } from "./reveal.js";
import { VIEWS, epicView, message, repoView, switchBar, warning } from "./views.js";

// The sidebar's stack (the layout's id; herdr-epic's generated layout uses the same).
const STACK = "epics-sidebar";

const expand = (path) => (path.startsWith("~/") ? `${home()}${path.slice(1)}` : path);

export default class Epics extends View {
  init(_props, cx) {
    this.view = props()?.view ?? "epic";
    if (this.view === "switch") {
      // The stack keeps its own pick; this one brings back the last pick after a layout reset.
      const saved = localStorage.getItem("view"), stack = find(layout(), STACK);
      if (stack && VIEWS.includes(saved) && stack.active !== saved) run("layout.select", { stack: STACK, child: saved });
      return;
    }
    this.board = null;
    this.error = null;
    this.open = JSON.parse(localStorage.getItem("open") ?? "{}");
    cx.timer.every(10000, (cx) => cx.spawn(async (cx) => { await this.reload(); cx.notify(); }));
    cx.spawn(async (cx) => {
      // Each folder's pending watch, kept across rounds so a change elsewhere doesn't drop it.
      const watches = new Map();
      for (;;) {
        await this.reload();
        cx.notify();
        if (props()?.fixture) return;
        const dirs = [...(this.board?.roots ?? []).map((r) => r.path), `${home()}/.herdr/tasks`];
        for (const d of dirs) if (!watches.has(d)) watches.set(d, watchFolder(d).then((paths) => ({ d, paths }), () => ({ d, paths: null })));
        for (const d of [...watches.keys()]) if (!dirs.includes(d)) watches.delete(d);
        // Wait for a change that isn't only dot-files (locks, temp files). A folder that can't be
        // watched (gone, too big) is left to the timer.
        for (;;) {
          if (!watches.size) return;
          const { d, paths } = await Promise.race(watches.values());
          if (!paths) { watches.delete(d); continue; }
          watches.set(d, watchFolder(d).then((paths) => ({ d, paths }), () => ({ d, paths: null })));
          if (paths.some((p) => !p.slice(d.length).split("/").some((part) => part.startsWith(".")))) break;
        }
      }
    });
  }

  // One read at a time: the timer and the watcher share it.
  reload() {
    this.reading ??= this.read().then((board) => {
      this.board = board;
      this.error = null;
      // The switch has no board of its own: it takes the language from here.
      if ((board.language ?? "en") !== (shared("epics.language") ?? "en")) setShared("epics.language", board.language ?? "en");
    }, (e) => { this.error = String(e?.message ?? e); }).finally(() => { this.reading = null; });
    return this.reading;
  }

  async read() {
    const fixture = props()?.fixture;
    if (fixture) {
      const text = await readTextFile(expand(fixture));
      if (text == null) throw new Error(`${fixture}: not found`);
      return JSON.parse(text);
    }
    const r = await exec("herdr-epic", ["board", "--json"]);
    if (r.code !== 0) throw new Error(r.stderr.trim() || `herdr-epic board: exit ${r.code}`);
    return JSON.parse(r.stdout);
  }

  isOpen(key, byDefault) {
    return this.open[key] ?? byDefault;
  }

  toggle(key, open) {
    this.open[key] = !open;
    localStorage.setItem("open", JSON.stringify(this.open));
  }

  // A space by id, else repaired by the CLI, which makes the space again and saves it
  // (`herdr-epic show <epic>`, `herdr-epic root show <root>`: ensure_space); then the board is read
  // again and `pick(output)` names the new space. The mod never makes spaces itself. A space the CLI
  // did not repair (a Done epic's stays closed) is not selected: the toast says it can't be opened.
  openSpace(space, args, pick, cx) {
    const alive = (id) => Boolean(id) && spaces().some((s) => s.id === id);
    if (alive(space)) return run("space.select", { id: space });
    cx.spawn(async (cx) => {
      const r = await exec("herdr-epic", args).catch(() => null);
      if (r?.code === 0) {
        await this.reload();
        cx.notify();
        const fixed = pick(JSON.parse(r.stdout));
        if (alive(fixed)) return run("space.select", { id: fixed });
      }
      notify({ title: "herdr-epic", body: words(this.board?.language).noSpace, level: "warning" });
    });
  }

  openEpic(epic, cx) {
    this.openSpace(epic.space, ["show", epic.id, "--json"], () => this.board?.epics?.find((e) => e.id === epic.id)?.space, cx);
  }

  // `herdr-epic archive|unarchive`, then the board again. The CLI decides: a refusal (a task still
  // running) comes back as a warning with its message, which names those tasks.
  setArchived(epic, archive, cx) {
    this.hovered = null;
    cx.spawn(async (cx) => {
      const r = await exec("herdr-epic", [archive ? "archive" : "unarchive", epic.id, "--json"]).catch((e) => ({ code: -1, stdout: "", stderr: String(e?.message ?? e) }));
      if (r.code !== 0) {
        const w = words(this.board?.language);
        const out = r.stdout.trim().startsWith("{") ? JSON.parse(r.stdout) : {};
        notify({ title: `${archive ? w.archiveFailed : w.unarchiveFailed} · ${epic.title}`, body: out.error || r.stderr.trim() || `herdr-epic: exit ${r.code}`, level: "warning" });
      }
      await this.reload();
      cx.notify();
    });
  }

  // A root's assistant space; `root show` returns the root with its (repaired) space.
  openRoot(root, cx) {
    this.openSpace(root.space, ["root", "show", root.name, "--json"], (out) => out.space, cx);
  }

  // Shepherdr's own task view (its Tasks panel drilled into the task, on its chat: shared
  // "tasks.thread"), where the layout has one; else a peek at the task's terminal. The view is
  // brought up through whatever holds it (reveal.js), not by fixed ids: Shepherdr's "panel" column
  // can be missing from a layout Herdr rewrote.
  openTask(row) {
    const w = words(this.board?.language);
    if (!row.terminal && !row.log) return notify({ title: `${row.ref} · ${row.title}`, body: w.notStarted, level: "info" });
    const l = layout();
    if (!find(l, "tasks-thread")) return row.terminal ? run("terminal.peek", { terminal: row.terminal }) : notify({ title: `${row.ref} · ${row.title}`, body: w.notStarted, level: "info" });
    // An ended task: its last agent's terminal with its log, as Shepherdr's Tasks list opens it (the
    // view waits on "Opening…" without a terminal).
    const log = row.log ? { path: row.log.path, kind: row.log.kind ?? null } : null;
    setShared("tasks.thread", { terminal: row.terminal ?? row.log?.address ?? null, task: row.ref, title: row.title, key: row.key ?? null, log, at: Date.now() });
    for (const [command, args] of revealSteps(l, find(l, "tasks-thread-chat") ? "tasks-thread-chat" : "tasks-thread")) run(command, args);
  }

  render() {
    const t = theme();
    if (this.view === "switch") {
      const w = words(shared("epics.language"));
      const active = find(layout(), STACK)?.active ?? localStorage.getItem("view") ?? VIEWS[0];
      return switchBar(t, w, active, (id) => {
        localStorage.setItem("view", id);
        run("layout.select", { stack: STACK, child: id });
      });
    }
    const w = words(this.board?.language);
    if (!this.board) return message(t, w, { error: this.error });
    const ui = {
      isOpen: (key, byDefault) => this.isOpen(key, byDefault),
      toggle: (key, open) => this.toggle(key, open),
      openTask: (row) => this.openTask(row),
      openEpic: (epic, cx) => this.openEpic(epic, cx),
      openRoot: (root, cx) => this.openRoot(root, cx),
      hovered: this.hovered,
      hover: (id, on) => { this.hovered = on ? id : this.hovered === id ? null : this.hovered; },
      setArchived: (epic, archive, cx) => this.setArchived(epic, archive, cx),
    };
    const body = this.view === "repo" ? repoView(t, w, this.board, ui) : epicView(t, w, this.board, ui);
    // `herdr-epic board` still answers when `herdr-task list` fails; it says so in board.error.
    if (!this.board.error) return body;
    return v_flex().size_full().bg(t.chrome).child(warning(t, w.stale(this.board.error))).child(v_flex().flex_1().min_h(0).child(body));
  }
}
