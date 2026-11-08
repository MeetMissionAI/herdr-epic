import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from herdr_epic import Fail
from herdr_epic import cli
from herdr_epic import config
from herdr_epic.i18n import t

from tests.helpers import StubEnv, make_repo, run_cli, temp_home


class CliTest(unittest.TestCase):
    def test_unknown_command_json_error(self):
        code, out = run_cli(["nope", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(out))

    def test_config_default_root(self):
        with temp_home() as home:
            cfg = config.load()
            self.assertEqual(cfg["roots"], [{"name": "epics", "path": str(home / ".herdr/epics")}])
            self.assertEqual(cfg["language"], "en")

    def test_stub_records_calls(self):
        with StubEnv() as stub:
            stub.respond("hc", ["ls"], '{"terminals": []}')
            self.assertEqual(json.loads(subprocess.check_output(["hc", "ls", "--json"])), {"terminals": []})
            self.assertEqual(stub.calls("hc"), [["ls", "--json"]])

    def test_config_save_round_trip(self):
        with temp_home() as home:
            cfg = {
                "roots": [{"name": "cards", "path": str(home / "cards")}],
                "language": "zh",
            }
            config.save(cfg)
            self.assertEqual(config.load(), cfg)
            self.assertTrue((home / ".config/herdr-epic/config.json").is_file())

    def test_config_env_override(self):
        with temp_home():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "elsewhere.json"
                path.write_text(json.dumps({
                    "roots": [{"name": "other", "path": str(Path(tmp) / "root")}],
                    "language": "zh",
                }))
                os.environ["HERDR_EPIC_CONFIG"] = str(path)
                cfg = config.load()
                self.assertEqual(cfg["language"], "zh")
                self.assertEqual(cfg["roots"][0]["name"], "other")

    def test_unknown_command_text_on_stderr(self):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["nope"])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("nope", err.getvalue())

    def test_fail_becomes_json_error(self):
        def boom(_argv, _as_json):
            raise Fail("blocked")

        def ok(_argv, _as_json):
            return 0

        saved = dict(cli._commands)
        real = cli.subcommand_names() - {"task", "task add", "root"}
        cli.add_command("task", ok)
        cli.add_command("task add", boom)
        cli.add_command("root repo add", ok)
        try:
            self.assertEqual(
                cli.subcommand_names() - real,
                {"task", "task add", "root", "root repo add"},
            )
            code, out = run_cli(["task", "add", "--json"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out), {"error": "blocked"})
            self.assertEqual(run_cli(["task", "--json"])[0], 0)
            self.assertEqual(run_cli(["root", "repo", "add"])[0], 0)
            code, out = run_cli(["root", "--json"])
            self.assertEqual(code, 1)
            self.assertIn("error", json.loads(out))
        finally:
            cli._commands.clear()
            cli._commands.update(saved)

    def test_i18n_en_and_zh(self):
        self.assertEqual(t("error.unknown_command", "en", name="nope"), "unknown command: nope")
        self.assertEqual(t("error.unknown_command", "zh", name="nope"), "未知命令：nope")
        self.assertEqual(t("missing.key", "en"), "missing.key")

    def test_stub_longest_prefix_miss_and_archive(self):
        with StubEnv() as stub:
            stub.respond("hc", ["ls"], "first")
            stub.respond("hc", ["ls"], "second")
            self.assertEqual(subprocess.check_output(["hc", "ls"], text=True), "second")

            stub.respond("herdr-task", ["add"], "short")
            stub.respond("herdr-task", ["add", "--json"], "long", code=0)
            self.assertEqual(
                subprocess.check_output(["herdr-task", "add", "--json"], text=True),
                "long",
            )
            missed = subprocess.run(["herdr-app", "missing"], capture_output=True, text=True)
            self.assertEqual(missed.returncode, 99)
            self.assertIn("missing", missed.stderr)

            with tempfile.TemporaryDirectory() as tmp:
                prompt = Path(tmp) / "prompt.txt"
                prompt.write_text("Do X\n")
                context = Path(tmp) / "context.txt"
                context.write_text("background\n")
                added = subprocess.run(
                    ["herdr-task", "add", "--prompt-file", str(prompt)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(added.stdout, "short")
                subprocess.run(
                    ["herdr-task", "add", "--context-file", str(context)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                prompt.unlink()
                context.unlink()
                logged = stub.calls("herdr-task")
                saved = Path(logged[-2][logged[-2].index("--prompt-file") + 1])
                saved_context = Path(logged[-1][logged[-1].index("--context-file") + 1])
                self.assertEqual(saved.read_text(), "Do X\n")
                self.assertEqual(saved_context.read_text(), "background\n")
                self.assertTrue(str(saved).startswith(str(stub.dir)))
                self.assertTrue(str(saved_context).startswith(str(stub.dir)))

    def test_launcher_json_error(self):
        bin_path = Path(__file__).resolve().parents[1] / "bin" / "herdr-epic"
        proc = subprocess.run([str(bin_path), "nope", "--json"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stderr, "")
        self.assertIn("error", json.loads(proc.stdout))

    def test_make_repo_one_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "shop-api", branch="main")
            self.assertTrue((repo / ".git").is_dir())
            branch = subprocess.check_output(
                ["git", "-C", str(repo), "branch", "--show-current"], text=True
            ).strip()
            count = subprocess.check_output(
                ["git", "-C", str(repo), "rev-list", "--count", "HEAD"], text=True
            ).strip()
            self.assertEqual(branch, "main")
            self.assertEqual(count, "1")


if __name__ == "__main__":
    unittest.main()
