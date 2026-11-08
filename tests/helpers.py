import io
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# An absolute home path (macOS or Linux): never part of a brief or a template.
HOME_PATH = re.compile(r"/(?:Users|home)/")


def forbidden() -> list:
    """Lowercased words that must never reach a brief or a template, kept out of the tracked tree:
    HERDR_EPIC_FORBIDDEN (comma-separated), else the untracked tests/.forbidden (commas or lines)."""
    text = os.environ.get("HERDR_EPIC_FORBIDDEN")
    if text is None:
        path = Path(__file__).resolve().parent / ".forbidden"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return [word.strip().lower() for word in text.replace("\n", ",").split(",") if word.strip()]


class StubEnv:
    """Point PATH at tests/stubs and record calls under a temporary STUB_DIR."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._path = os.environ.get("PATH")
        self._stub = os.environ.get("STUB_DIR")
        stubs = Path(__file__).resolve().parent / "stubs"
        os.environ["PATH"] = str(stubs) + os.pathsep + (self._path or "")
        os.environ["STUB_DIR"] = str(self.dir)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self._path
        if self._stub is None:
            os.environ.pop("STUB_DIR", None)
        else:
            os.environ["STUB_DIR"] = self._stub
        self._tmp.cleanup()
        return False

    def respond(self, binary, args_prefix, stdout, code=0):
        path = self.dir / f"{binary}.json"
        table = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        table.append({"args": list(args_prefix), "stdout": stdout, "code": code})
        path.write_text(json.dumps(table), encoding="utf-8")

    def calls(self, binary):
        path = self.dir / f"{binary}.calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@contextmanager
def temp_home():
    """Use a temporary HOME and hide HERDR_EPIC_CONFIG for the duration."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        old_home = os.environ.get("HOME")
        old_cfg = os.environ.get("HERDR_EPIC_CONFIG")
        os.environ["HOME"] = str(home)
        os.environ.pop("HERDR_EPIC_CONFIG", None)
        try:
            yield home
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_cfg is None:
                os.environ.pop("HERDR_EPIC_CONFIG", None)
            else:
                os.environ["HERDR_EPIC_CONFIG"] = old_cfg


def run_cli(argv):
    from herdr_epic.cli import main

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(list(argv))
    return code, buf.getvalue()


def make_repo(dir, name, branch="main"):
    """Create a git repo at `dir`/`name` with one commit on `branch`."""
    path = Path(dir) / name
    path.mkdir(parents=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "HEAD", f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
    )
    (path / "README").write_text(name + "\n", encoding="utf-8")
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Dev"
    env["GIT_AUTHOR_EMAIL"] = "dev@example.com"
    env["GIT_COMMITTER_NAME"] = "Dev"
    env["GIT_COMMITTER_EMAIL"] = "dev@example.com"
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
        env=env,
    )
    return path
