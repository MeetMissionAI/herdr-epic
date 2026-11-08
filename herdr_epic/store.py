from __future__ import annotations

import fcntl
import json
import os
import secrets
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

from herdr_epic import Fail

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "epic.md"
_LOGS = ("decisions.md", "handoff.md")


def folder_name(title: str) -> str:
    """Turn a title into a folder name. Slashes, backslashes, colons, and controls become '-'."""
    chars = []
    for ch in title:
        if ch in "/\\:" or unicodedata.category(ch) == "Cc":
            chars.append("-")
        else:
            chars.append(ch)
    name = "".join(chars)
    while name and (name[0].isspace() or name[0] == "."):
        name = name[1:]
    while name and (name[-1].isspace() or name[-1] == "."):
        name = name[:-1]
    return name


def create(root: Path, title: str, repos: list[dict]) -> dict:
    """Create an epic folder and its files. The returned dict includes 'folder'; the file does not."""
    root = Path(root)
    if not root.is_dir():
        raise Fail(f"epic root does not exist: {root}")
    name = folder_name(title)
    if name == "":
        raise Fail("epic title is empty")
    stored = _repos(repos)
    folder = root / name
    try:
        folder.mkdir()
    except FileExistsError:
        raise Fail(f"epic folder already exists: {name}") from None
    epic = {
        "version": 1,
        "id": _new_id(root),
        "title": title,
        "space": None,
        "machine": None,
        "state": "open",
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "closed": None,
        "archived": None,
        "repos": stored,
        "tasks": [],
        "requests": [],
    }
    body = f"# {title}\n\n{_TEMPLATE.read_text(encoding='utf-8')}"
    if not body.endswith("\n"):
        body += "\n"
    (folder / "epic.md").write_text(body, encoding="utf-8")
    (folder / "decisions.md").write_text("", encoding="utf-8")
    (folder / "handoff.md").write_text("", encoding="utf-8")
    with _lock(folder):
        _atomic_write(folder / "epic.json", epic)
    epic["folder"] = str(folder.resolve())
    return epic


def load(folder: Path) -> dict:
    """Read epic.json and attach the absolute folder path."""
    folder = Path(folder)
    path = folder / "epic.json"
    if not path.is_file():
        raise Fail(f"no epic at {folder}")
    epic = _read_json(path)
    epic["folder"] = str(folder.resolve())
    return epic


def update(folder: Path, fn: Callable[[dict], None]) -> dict:
    """Lock epic.json, let fn mutate it in place, and write it back atomically."""
    folder = Path(folder)
    path = folder / "epic.json"
    if not path.is_file():
        raise Fail(f"no epic at {folder}")
    with _lock(folder):
        epic = _read_json(path)
        fn(epic)
        epic.pop("folder", None)
        epic.pop("root", None)
        _atomic_write(path, epic)
    epic["folder"] = str(folder.resolve())
    return epic


def all_epics(cfg: dict) -> list[dict]:
    """Every readable direct child that has epic.json, tagged with the root's name.

    A corrupt epic.json is skipped. load(folder) still raises Fail for that folder.
    """
    found = []
    for root in cfg["roots"]:
        base = Path(root["path"]).expanduser()
        if not base.is_dir():
            continue
        children = [child for child in base.iterdir() if (child / "epic.json").is_file()]
        for child in sorted(children, key=lambda item: item.name):
            epic = _readable(child)
            if epic is None:
                continue
            epic["root"] = root["name"]
            found.append(epic)
    return found


def find(cfg: dict, key: str) -> dict:
    """Match one epic by id, title, folder name, or absolute folder path."""
    candidate = _absolute_key(key)
    matches = []
    for epic in all_epics(cfg):
        folder = epic["folder"]
        same_path = candidate is not None and candidate == str(Path(folder).resolve())
        if key in (epic.get("id"), epic.get("title"), Path(folder).name, folder) or same_path:
            matches.append(epic)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise Fail(f"epic not found: {key}")
    lines = [f"{epic['id']} {epic['title']} ({epic['folder']})" for epic in matches]
    raise Fail("ambiguous epic %r:\n%s" % (key, "\n".join(lines)))


def by_space(cfg: dict, space_id: str) -> dict | None:
    """Return the epic bound to space_id (its space now, or one it had before the space was made again),
    or None when no epic is bound to it."""
    matches = [epic for epic in all_epics(cfg)
               if space_id == epic.get("space") or space_id in (epic.get("former_spaces") or [])]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    lines = [f"{epic['id']} {epic['title']} ({epic['folder']})" for epic in matches]
    raise Fail(f"ambiguous space {space_id}:\n" + "\n".join(lines))


def add_request(folder: Path, request: dict) -> dict:
    """Append `request` to epic.json under the next id (r-1, r-2, ...), which is also set on `request`."""
    def append(epic):
        numbers = [int(r["id"][2:]) for r in epic["requests"]
                   if str(r.get("id", "")).startswith("r-") and r["id"][2:].isdigit()]
        request["id"] = f"r-{max(numbers, default=0) + 1}"
        epic["requests"].append(request)

    return update(folder, append)


def append_log(folder: Path, file: str, text: str) -> None:
    """Append a dated entry to decisions.md or handoff.md."""
    if file not in _LOGS:
        raise Fail(f"not an epic log: {file}")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with (Path(folder) / file).open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {stamp}\n{text}\n")


def _repos(repos: list[dict]) -> list[dict]:
    if not isinstance(repos, list):
        raise Fail("repos must be a list")
    stored = []
    for item in repos:
        if not isinstance(item, dict):
            raise Fail("repo must be an object")
        name = item.get("name")
        path = item.get("path")
        base = item.get("base")
        if not isinstance(name, str) or name == "" or not isinstance(path, str) or path == "":
            raise Fail("repo needs a name and a path")
        if base is not None and not isinstance(base, str):
            raise Fail("repo base must be a string or null")
        stored.append({"name": name, "path": path, "base": base})
    return stored


def _readable(folder: Path) -> dict | None:
    """load(folder), or None when that epic.json cannot be read (corrupt, not UTF-8, no permission)."""
    try:
        return load(folder)
    except (Fail, OSError, UnicodeDecodeError):
        return None


def _absolute_key(key: str) -> str | None:
    """Resolve key as a folder path only when it is absolute or a home path.

    "~scratch" and a title like "~user notes" are not paths. Path.expanduser
    would raise RuntimeError for an unknown ~user.
    """
    if not (os.path.isabs(key) or key == "~" or key.startswith("~/")):
        return None
    return str(Path(key).expanduser().resolve())


def _new_id(root: Path) -> str:
    used = set()
    for child in root.iterdir():
        if not (child / "epic.json").is_file():
            continue
        epic = _readable(child)
        if epic is None:
            continue
        eid = epic.get("id")
        if isinstance(eid, str):
            used.add(eid)
    for _ in range(64):
        eid = "e-" + secrets.token_hex(2)
        if eid not in used:
            return eid
    raise Fail("could not allocate an epic id")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise Fail(f"invalid epic.json: {err}") from None
    if not isinstance(data, dict):
        raise Fail(f"invalid epic.json: {path}")
    return data


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _lock(folder: Path):
    fd = os.open(str(folder / "epic.json.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
