"""Shared body for the hc, herdr-task, and herdr-app test doubles."""

import json
import os
import sys
from pathlib import Path

_FILE_FLAGS = {"--prompt-file", "--context-file"}


def main() -> int:
    binary = Path(sys.argv[0]).name
    argv = sys.argv[1:]
    stub_dir = os.environ.get("STUB_DIR")
    if not stub_dir:
        print("STUB_DIR is not set", file=sys.stderr)
        return 99
    root = Path(stub_dir)
    logged = _archive(root, binary, argv)
    with (root / f"{binary}.calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(logged, ensure_ascii=False) + "\n")
    table_path = root / f"{binary}.json"
    if not table_path.exists():
        print(json.dumps(argv, ensure_ascii=False), file=sys.stderr)
        return 99
    best = None
    best_len = -1
    for entry in json.loads(table_path.read_text(encoding="utf-8")):
        prefix = entry["args"]
        if argv[:len(prefix)] == prefix and len(prefix) >= best_len:
            best = entry
            best_len = len(prefix)
    if best is None:
        print(json.dumps(argv, ensure_ascii=False), file=sys.stderr)
        return 99
    sys.stdout.write(best.get("stdout", ""))
    return int(best.get("code", 0))


def _archive(root: Path, binary: str, argv: list) -> list:
    """Copy --prompt-file and --context-file targets next to the call log.

    The logged argv points at the copy so a test can read it after the caller
    deletes the original temporary file.
    """
    calls = root / f"{binary}.calls.jsonl"
    index = 0
    if calls.exists():
        index = sum(1 for line in calls.read_text(encoding="utf-8").splitlines() if line)
    out = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _FILE_FLAGS and i + 1 < len(argv):
            src = Path(argv[i + 1])
            try:
                data = src.read_bytes()
            except OSError:
                out.extend((arg, argv[i + 1]))
            else:
                dest = root / f"{binary}.files" / f"{index:04d}-{arg[2:]}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                out.extend((arg, str(dest)))
            i += 2
            continue
        out.append(arg)
        i += 1
    return out
