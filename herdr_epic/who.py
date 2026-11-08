from __future__ import annotations

from pathlib import Path

from herdr_epic import store


def resolve(cfg: dict, term: dict | None, cwd: Path) -> dict:
    """Say who is calling: worker, orchestrator, root, or none.

    A worker's herdr.task label is Shepherdr's task key (<slug>#<id>), returned as
    task_key and not treated as a human ref.
    """
    labels = (term or {}).get("labels") or {}
    task_key = labels.get("herdr.task")

    def result(role, epic=None, root=None):
        return {"role": role, "epic": epic, "root": root, "task_key": task_key}

    epic = store.by_space(cfg, labels["herdr.task.space"]) if labels.get("herdr.task.space") else None
    if epic:
        return result("worker", epic)
    if labels.get("herdr.role") == "coordinator":
        for space in (labels.get("herdr.coordinator.space"), labels.get("herdr.space")):
            epic = store.by_space(cfg, space) if space else None
            if epic:
                return result("orchestrator", epic)
    here = Path(cwd).resolve()
    for epic in store.all_epics(cfg):
        folder = Path(epic["folder"]).resolve()
        if here == folder or folder in here.parents:
            return result("orchestrator", epic)
    for root in cfg["roots"]:
        if here == Path(root["path"]).expanduser().resolve():
            return result("root", root=root)
    return result("none")
