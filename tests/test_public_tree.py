import re
import subprocess
import unittest
from pathlib import Path

from tests.helpers import forbidden

ROOT = Path(__file__).resolve().parents[1]
# A real home folder; the examples use the placeholders /home/you/ and /Users/someone.
REAL_HOME = re.compile(r"/(?:Users|home)/(?!(?:you|someone)\b)[^/\s]*")


class PublicTreeTest(unittest.TestCase):
    """Every tracked file is fit to publish: no one's home folder, none of the forbidden words."""

    def test_tracked_files_name_no_person_or_private_place(self):
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True)
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        words = forbidden()
        hits = [f"{name}: {bad}"
                for name in filter(None, listed.stdout.split("\0"))
                if (ROOT / name).is_file()
                for text in [(ROOT / name).read_text(encoding="utf-8", errors="ignore")]
                for bad in [m.group(0) for m in REAL_HOME.finditer(text)] + [w for w in words if w in text.lower()]]
        self.assertEqual(hits, [])

    def test_real_home_spots_a_person_but_not_the_placeholders(self):
        # Built from parts, so this file doesn't trip its own check.
        self.assertIsNotNone(REAL_HOME.search("/".join(["", "Users", "ada", "notes"])))
        self.assertIsNotNone(REAL_HOME.search("/".join(["", "home", "younger", "x"])))
        self.assertIsNone(REAL_HOME.search("/home/you/work/shop and /Users/someone"))


if __name__ == "__main__":
    unittest.main()
