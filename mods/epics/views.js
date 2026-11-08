// What the sidebar draws: the stack header's three-way switch, the by-epic view
// and the by-repo view (README.md, "The sidebar"). Pure descriptions: every action comes in through `ui`
// ({ isOpen(key, byDefault), toggle(key, open), openTask(row), openEpic(epic, cx), openRoot(root, cx),
// hovered, hover(id, on), setArchived(epic, archive, cx) }).

import { div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";

// Herdr's own icons (shipped with every layout), tinted with the text color.
const ICON = (name) => `mods/workspace-chrome/${name}.svg`;
// Lucide's archive-restore (ISC), which Herdr doesn't ship: this mod bundles it.
const RESTORE = "mods/epics/archive-restore.svg";
const MONO = "JetBrains Mono";

export const VIEWS = ["epics-by-epic", "epics-by-repo", "epics-spaces"];

// The stack header: By epic / By repo / Spaces.
export function switchBar(t, w, active, pick) {
  const labels = [w.byEpic, w.byRepo, w.spaces];
  return h_flex().size_full().px(10).items_center().bg(t.chrome)
    .child(h_flex().w_full().h(28).p(2).gap(2).rounded(7).bg(t.raised).children(VIEWS.map((id, i) => {
      const on = id === active;
      return h_flex().id(`switch-${id}`).flex_1().h_full().justify_center().items_center().rounded(5)
        .whitespace_nowrap().text_size(12)
        .when(on, (el) => el.bg(t.background).text_color(t.foreground).font_medium())
        .when(!on, (el) => el.text_color(t.muted).cursor_pointer().hover((s) => s.text_color(t.foreground))
          .on_click((_e, cx) => { pick(id); cx.notify(); }))
        .child(labels[i]);
    })));
}

// A task's status mark, in a 14px box so nothing moves when it changes.
function dot(t, status) {
  const box = div().size(14).flex_none().flex().items_center().justify_center();
  if (status === "working" || status === "merging")
    return box.child(svg(ICON("spinner")).size(12).flex_none().text_color(status === "working" ? t.working : t.done).spin(status === "working" ? 900 : 1400));
  const fill = { needs: t.ansi3, ready: t.done, done: t.dim }[status];
  if (fill) return box.child(div().size(8).rounded(4).bg(fill));
  return box.child(div().size(9).rounded(5).border(1.5).border_color(status === "missing" ? t.unknown : t.dim)
    .when(status === "missing", (el) => el.border_dashed()));
}

const chevron = (t, open) => svg(ICON(open ? "chevron-down" : "chevron-right")).size(11).flex_none().text_color(t.dim);

function progressText(row) {
  const { done, left } = row.progress ?? {};
  return [done == null ? null : `${done}%`, left].filter(Boolean).join(" ");
}

// One task, two lines: the dot, the repo (by-epic view), the ref and the title, with
// progress while it works; under it what it waits for (the theme's yellow, ansi3) or what it is doing
// (dim). The by-repo view puts the epic's name at the end instead of the repo.
function taskRow(t, w, row, where, ui, { repo = false, epic = false } = {}) {
  const progress = row.status === "working" ? progressText(row) : "";
  const line2 = row.line2 || w.status[row.status] || row.status;
  return v_flex().id(`task-${where}-${row.repo}-${row.ref}`).flex_none().px(8).py(4).gap(1).rounded(7)
    .cursor_pointer().hover((s) => s.bg(t.hover))
    .on_click(() => ui.openTask(row))
    .child(h_flex().h(18).gap(6).items_center()
      .child(dot(t, row.status))
      .when(repo, (el) => el.child(div().flex_none().max_w(88).truncate().text_size(11.5).text_color(t.muted).child(row.repo)))
      .child(div().flex_none().font_family(MONO).text_size(10.5).text_color(t.dim).child(row.ref))
      .child(div().flex_1().min_w(0).truncate().text_size(12.5).text_color(row.status === "done" ? t.muted : t.foreground).child(row.title))
      .when(Boolean(progress), (el) => el.child(div().flex_none().text_size(11).text_color(t.working).child(progress))))
    .child(h_flex().h(16).pl(20).gap(6).items_center()
      .child(div().flex_1().min_w(0).truncate().text_size(11.5).text_color(row.needs ? t.ansi3 : t.dim).child(line2))
      .when(epic && Boolean(row.epic), (el) => el.child(div().flex_none().max_w(110).truncate().px(5).h(15).rounded(4)
        .bg(t.raised).text_size(10.5).text_color(t.muted).child(row.epic.title))));
}

// A section's header (Unassigned, Done, Archived): it folds the section; folded, it says how many.
function sectionHead(t, key, label, count, open, ui) {
  return h_flex().id(`sec-${key}`).flex_none().h(28).px(8).pt(6).gap(6).items_center().cursor_pointer()
    .text_size(11).font_medium().text_color(t.dim).hover((s) => s.text_color(t.foreground))
    .on_click((_e, cx) => { ui.toggle(key, open); cx.notify(); })
    .child(chevron(t, open))
    .child(div().child(label))
    .child(div().text_color(t.dim).child(String(count)));
}

// An epic: one row (fold chevron, name, summary; the theme's yellow (ansi3) while a task needs the person,
// folded or not; on hover, archive or unarchive at the end), then its tasks when open.
function epicBlock(t, w, epic, ui) {
  const key = `epic:${epic.id}`;
  const open = ui.isOpen(key, epic.state !== "done" && !epic.archived);
  const { agents = 0, working = 0, needs = 0 } = epic.counts ?? {};
  const summary = [w.agents(agents), working ? w.working(working) : null].filter(Boolean).join(" · ");
  const head = h_flex().id(`epic-${epic.id}`).flex_none().h(30).pl(4).pr(8).gap(4).items_center().rounded(7)
    .when(needs > 0, (el) => el.bg(t.ansi3 + "1f"))
    .hover((s) => s.bg(needs > 0 ? t.ansi3 + "33" : t.hover))
    .on_hover((on, cx) => { ui.hover(epic.id, on); cx.notify(); })
    .child(div().id(`fold-${epic.id}`).flex_none().size(20).flex().items_center().justify_center().rounded(5).cursor_pointer()
      .hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => { cx.stop_propagation(); ui.toggle(key, open); cx.notify(); })
      .child(chevron(t, open)))
    .child(h_flex().id(`epic-open-${epic.id}`).flex_1().min_w(0).h_full().gap(6).items_center().cursor_pointer()
      .tooltip(w.epicTip)
      .on_click((_e, cx) => ui.openEpic(epic, cx))
      .child(div().flex_1().min_w(0).truncate().text_size(13).font_medium()
        .text_color(needs > 0 ? t.ansi3 : epic.state === "done" ? t.muted : t.foreground).child(epic.title))
      .child(div().flex_none().text_size(11).text_color(t.dim).child(summary))
      .when(needs > 0, (el) => el.child(div().flex_none().px(5).h(16).rounded(4).bg(t.ansi3 + "33")
        .flex().items_center().text_size(10.5).font_semibold().text_color(t.ansi3).tooltip(w.needs(needs)).child(`! ${needs}`))))
    .when(ui.hovered === epic.id, (el) => el.child(div().id(`archive-${epic.id}`).flex_none().size(20).flex().items_center()
      .justify_center().rounded(5).cursor_pointer().hover((s) => s.bg(t.raised))
      .tooltip(epic.archived ? w.unarchiveTip : w.archiveTip)
      .on_click((_e, cx) => { cx.stop_propagation(); ui.setArchived(epic, !epic.archived, cx); })
      .child(svg(epic.archived ? RESTORE : ICON("archive")).size(13).flex_none().text_color(t.muted))));
  const tasks = epic.tasks ?? [];
  return [head, ...(open ? tasks.length ? tasks.map((r) => taskRow(t, w, r, epic.id, ui, { repo: true })) : [empty(t, w.noTasks, 28)] : [])];
}

const empty = (t, text, indent = 12) => div().flex_none().pl(indent).pr(12).py(6).text_size(12).text_color(t.dim).child(text);

function list(t, id, rows) {
  return v_flex().id(id).size_full().min_h(0).bg(t.chrome).text_size(13)
    .child(v_flex().id(`${id}-list`).flex_1().min_h(0).px(8).pt(6).pb(10).overflow_y_scrollbar().children(rows));
}

// A warning line over a view: the board came back, but part of it could not be read (board.error).
export function warning(t, text) {
  return div().flex_none().mx(8).mt(6).px(8).py(5).rounded(7).bg(t.raised).text_size(11.5).text_color(t.ansi3).child(text);
}

// Status line when there's no board yet: loading, or why it can't be read.
export function message(t, w, state) {
  return v_flex().size_full().bg(t.chrome).p(14).gap(4).text_size(12)
    .child(div().text_color(state.error ? t.foreground : t.dim).child(state.error ? w.failed : w.loading))
    .when(Boolean(state.error), (el) => el.child(div().text_color(t.dim).child(state.error)));
}

// By epic: the root assistants on top, the open epics, Unassigned by repo, Done folded, Archived folded.
// An archived epic shows only under Archived, whatever its state.
export function epicView(t, w, board, ui) {
  const roots = (board.roots ?? []).map((r) => h_flex().id(`root-${r.name}`).flex_none().h(30).px(8).gap(8).items_center()
    .rounded(7).cursor_pointer().hover((s) => s.bg(t.hover)).tooltip(w.rootTip)
    .on_click((_e, cx) => ui.openRoot(r, cx))
    .child(svg(ICON("folder")).size(14).flex_none().text_color(t.muted))
    .child(div().flex_none().text_size(13).font_medium().text_color(t.foreground).child(r.name))
    .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(r.path)));
  const epics = board.epics ?? [];
  const open = epics.filter((e) => !e.archived && e.state !== "done");
  const done = epics.filter((e) => !e.archived && e.state === "done");
  const archived = epics.filter((e) => e.archived);
  const unassigned = (board.unassigned ?? []).filter((g) => g.tasks?.length);
  const openU = ui.isOpen("unassigned", true);
  const openD = ui.isOpen("done", false);
  const openA = ui.isOpen("archived", false);
  return list(t, "by-epic", [
    ...roots,
    ...(roots.length ? [div().flex_none().h(1).mx(8).my(6).bg(t.border)] : []),
    ...(open.length ? open.flatMap((e) => epicBlock(t, w, e, ui)) : [empty(t, w.noEpics)]),
    ...(unassigned.length ? [
      sectionHead(t, "unassigned", w.unassigned, unassigned.reduce((n, g) => n + g.tasks.length, 0), openU, ui),
      ...(openU ? unassigned.flatMap((g) => [
        div().flex_none().pl(28).pt(4).pb(1).text_size(11).text_color(t.muted).child(g.repo),
        ...g.tasks.map((r) => taskRow(t, w, r, "unassigned", ui)),
      ]) : []),
    ] : []),
    ...(done.length ? [
      sectionHead(t, "done", w.done, done.length, openD, ui),
      ...(openD ? done.flatMap((e) => epicBlock(t, w, e, ui)) : []),
    ] : []),
    ...(archived.length ? [
      sectionHead(t, "archived", w.archived, archived.length, openA, ui),
      ...(openA ? archived.flatMap((e) => epicBlock(t, w, e, ui)) : []),
    ] : []),
  ]);
}

// By repo: repo → branch and how far behind → its tasks, each tagged with its epic. Tasks of an
// archived epic are left out, as the epic is in By epic.
export function repoView(t, w, board, ui) {
  const repos = board.repos ?? [];
  const archived = new Set((board.epics ?? []).filter((e) => e.archived).map((e) => e.id));
  return list(t, "by-repo", repos.length ? repos.flatMap((repo) => {
    const key = `repo:${repo.name}`;
    const open = ui.isOpen(key, true);
    const tasks = (repo.tasks ?? []).filter((r) => !archived.has(r.epic?.id));
    const head = h_flex().id(`repo-${repo.name}`).flex_none().h(30).pl(4).pr(8).gap(6).items_center().rounded(7)
      .cursor_pointer().hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => { ui.toggle(key, open); cx.notify(); })
      .child(h_flex().size(20).flex_none().items_center().justify_center().child(chevron(t, open)))
      .child(div().flex_1().min_w(0).truncate().text_size(13).font_medium().text_color(t.foreground).child(repo.name))
      .when(Boolean(repo.branch), (el) => el.child(h_flex().flex_none().max_w(110).gap(3).items_center()
        .child(svg(ICON("git-branch")).size(12).flex_none().text_color(t.dim))
        .child(div().min_w(0).truncate().text_size(11).text_color(t.muted).child(repo.branch))))
      .when(repo.behind > 0, (el) => el.child(div().flex_none().text_size(11).text_color(t.dim).tooltip(w.behind(repo.behind)).child(`↓${repo.behind}`)))
      .when(!open && tasks.length > 0, (el) => el.child(div().flex_none().text_size(11).text_color(t.dim).child(String(tasks.length))));
    return [head, ...(open ? tasks.length ? tasks.map((r) => taskRow(t, w, r, repo.name, ui, { epic: true })) : [empty(t, w.noTasks, 28)] : [])];
  }) : [empty(t, w.noRepos)]);
}
