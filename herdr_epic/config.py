from __future__ import annotations

import json
import os
from pathlib import Path

from herdr_epic import Fail


def load(path: Path | None = None) -> dict:
    """Return roots and language. A missing file yields the default root."""
    dest = _path(path)
    if not dest.exists():
        return _default()
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise Fail(f"invalid config: {err}") from None
    return _normalize(data)


def save(cfg: dict, path: Path | None = None) -> None:
    dest = _path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(_normalize(cfg), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _path(path: Path | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("HERDR_EPIC_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path("~/.config/herdr-epic/config.json").expanduser()


def _default() -> dict:
    return {
        "roots": [{"name": "epics", "path": str(Path("~/.herdr/epics").expanduser())}],
        "language": "en",
    }


def _normalize(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise Fail("config must be a JSON object")
    language = cfg.get("language", "en")
    if language not in ("en", "zh"):
        raise Fail(f"unsupported language: {language}")
    roots = cfg.get("roots")
    if roots is None:
        roots = _default()["roots"]
    if not isinstance(roots, list):
        raise Fail("config roots must be a list")
    normalized = []
    for item in roots:
        if not isinstance(item, dict):
            raise Fail("config root must be an object")
        name = item.get("name")
        root = item.get("path")
        if not isinstance(name, str) or not isinstance(root, str) or name == "" or root == "":
            raise Fail("config root needs a name and a path")
        normalized.append({"name": name, "path": str(Path(root).expanduser())})
    return {"roots": normalized, "language": language}
