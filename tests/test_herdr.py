import subprocess
import unittest
from unittest import mock

from herdr_epic import herdr
from tests.helpers import StubEnv


class AppDoTest(unittest.TestCase):
    def test_booleans_go_as_json_and_none_is_skipped(self):
        with StubEnv() as stub:
            stub.respond("herdr-app", ["do"], "{}")
            herdr.app_do("tab.new", focus=True, quiet=False, name="x", skipped=None)
            self.assertEqual(
                stub.calls("herdr-app"),
                [["do", "tab.new", "--focus", "true", "--quiet", "false", "--name", "x"]],
            )

    def test_every_subprocess_gets_devnull_stdin(self):
        with StubEnv() as stub:
            stub.respond("hc", ["ls"], "{}")
            real = subprocess.run
            seen = []

            def spy(*args, **kwargs):
                seen.append(kwargs.get("stdin"))
                return real(*args, **kwargs)

            with mock.patch("subprocess.run", spy):
                herdr.hc("ls")
            self.assertEqual(seen, [subprocess.DEVNULL])


if __name__ == "__main__":
    unittest.main()
