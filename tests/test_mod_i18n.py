import re
import unittest
from pathlib import Path

from tests.helpers import _ROOT

MOD = Path(_ROOT) / "mods" / "epics"
# Chinese, Japanese and Korean letters and their punctuation (full-width forms included).
CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
# A JS string literal: "…" or '…' on one line (an apostrophe in a comment opens none), or `…`.
STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`')


def blank_strings(text):
    """The text with every string literal's insides turned to spaces: same length, so offsets still
    match, and no brace or colon inside a string is read as code."""
    return STRING.sub(lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1], text)


def table(code, name):
    """Start and end offsets of `name: { … }` in i18n.js (strings already blanked)."""
    start = re.search(rf"^\s*{name}:\s*{{", code, re.M).end() - 1
    depth = 0
    for i in range(start, len(code)):
        depth += {"{": 1, "}": -1}.get(code[i], 0)
        if depth == 0:
            return start, i + 1
    raise AssertionError(f"{name} table never closes")


def keys(code):
    """Every key in an object literal, with its path ("status.todo")."""
    path, found, last = [], set(), None
    for m in re.finditer(r"(\w+)\s*:|[{}]", code):
        if m.group(0) == "{":
            path.append(last)
        elif m.group(0) == "}":
            path.pop()
        else:
            last = m.group(1)
            found.add(".".join([p for p in path[1:] if p] + [last]))
    return found


class ModI18nTest(unittest.TestCase):
    """Anyone who installs the mod sees English: Chinese lives only in i18n.js's zh table."""

    def test_no_cjk_outside_the_zh_table(self):
        hits = []
        for path in sorted(MOD.glob("*.js")) + [MOD / "mod.json"]:
            text = path.read_text(encoding="utf-8")
            if path.name == "i18n.js":
                start, end = table(blank_strings(text), "zh")
                text = text[:start] + text[end:]
            hits += [f"{path.name}: {line.strip()}" for line in text.splitlines() if CJK.search(line)]
        self.assertEqual(hits, [], "Chinese outside i18n.js's zh table")

    def test_en_and_zh_have_the_same_keys(self):
        code = blank_strings((MOD / "i18n.js").read_text(encoding="utf-8"))
        en, zh = (keys(code[slice(*table(code, name))]) for name in ("en", "zh"))
        self.assertIn("noEpics", en)
        self.assertIn("status.todo", en)
        self.assertEqual(sorted(en - zh), [], "in en, missing from zh")
        self.assertEqual(sorted(zh - en), [], "in zh, missing from en")


if __name__ == "__main__":
    unittest.main()
