import re
import unittest
from pathlib import Path

from herdr_epic import config
from tests import ISOLATED_CONFIG


class IsolationTest(unittest.TestCase):
    """No test reads the developer's own ~/.config/herdr-epic/config.json, however it is run."""

    def test_config_is_the_tests_own(self):
        self.assertEqual(config._path(None), Path(ISOLATED_CONFIG))

    def test_every_test_module_imports_the_tests_package(self):
        # tests/__init__.py sets HERDR_EPIC_CONFIG; a module run alone that skips it reads the real one.
        here = Path(__file__).resolve().parent
        missing = [p.name for p in sorted(here.glob("test_*.py"))
                   if not re.search(r"^(from tests\b|import tests\b)", p.read_text(encoding="utf-8"), re.M)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
