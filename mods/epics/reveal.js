// Bringing a layout node into view, whatever the tree around it looks like. Herdr 0.1.13 rewrote
// layouts it changed itself and folded a one-child row or column into its child, so a node such as
// Shepherdr's "panel" column can be gone while the view inside it is still there. No other mod
// imports: node's tests load this file on its own.

export const find = (node, id) => {
  if (!node || typeof node !== "object") return null;
  if (node.id === id) return node;
  for (const c of node.children ?? []) { const hit = find(c, id); if (hit) return hit; }
  return null;
};

// The commands that show node `id` of `tree` (a layout(), with its clicks): on the way down from the
// root, every stack selects the child that leads there and every hidden node is shown. [] when the
// tree has no such node.
export function revealSteps(tree, id) {
  const path = (node) => {
    if (!node || typeof node !== "object") return null;
    if (node.id === id) return [node];
    for (const c of node.children ?? []) { const p = path(c); if (p) return [node, ...p]; }
    return null;
  };
  return (path(tree) ?? []).flatMap((node, i, nodes) => [
    ...(i > 0 && nodes[i - 1].type === "stack" ? [["layout.select", { stack: nodes[i - 1].id, child: node.id }]] : []),
    ...(i > 0 && node.hidden ? [["layout.show", { id: node.id }]] : []),
  ]);
}
