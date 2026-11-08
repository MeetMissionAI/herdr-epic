import re
import unittest
from pathlib import Path

from herdr_epic import cli

from tests.helpers import HOME_PATH, forbidden

TEMPLATES = Path(__file__).resolve().parents[1] / "herdr_epic" / "templates"
TEMPLATE = TEMPLATES / "root-AGENTS.md"


def template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


class AgentsTemplateTest(unittest.TestCase):
    def test_agents_template_mentions_only_real_subcommands(self):
        commands = set(cli._commands)
        used = {f"{a} {b}" if f"{a} {b}" in commands else a
                for a, b in re.findall(r"herdr-epic ([a-z]+)(?: ([a-z]+))?", template_text())}
        self.assertTrue(used, "the template names no herdr-epic command")
        self.assertEqual(used - commands, set())

    def test_agents_template_has_three_roles_and_no_user_strings(self):
        text = template_text()
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        for role in ("Root assistant", "Orchestrator", "Worker"):
            self.assertTrue(any(role in h for h in headings), role)
        self.assertIn("herdr-epic whoami", text)
        for role in ('"root"', '"orchestrator"', '"worker"'):
            self.assertIn(role, text)
        for path in sorted(TEMPLATES.iterdir()):
            body = path.read_text(encoding="utf-8")
            self.assertIsNone(HOME_PATH.search(body), path.name)
            for bad in forbidden():
                self.assertNotIn(bad, body.lower(), path.name)

    def test_agents_template_encodes_the_orchestrator_rules(self):
        text = template_text()
        for needle in (
            "herdr-epic pending", "handoff.md", "decisions.md", "herdr-epic task add", "never `herdr-task add`",
            "<herdr-epic-request", "<herdr-assistant", "herdr-epic reply", "replied-undelivered", "--after",
            "--set", "herdr-epic repo add", "details", "hc send", "Summary", "herdr-epic close",
            "herdr-epic new", "root.json", "herdr-epic list", "herdr-epic show", "git rev-parse",
            "person's language", "--root", "herdr-task auto add", "For AGENTS.md", "already started",
            "one or more git repositories", "Accept", "plain fields", "herdr-epic archive", "herdr-epic unarchive",
            "list --archived",
        ):
            self.assertIn(needle, text)
        for gone in ("epic.json `requests`", "which pull requests merged"):
            self.assertNotIn(gone, text)

    def test_agents_template_gives_git_to_shepherdr(self):
        orchestrator = template_text().split("## Orchestrator", 1)[1].split("## Worker", 1)[0]
        text = " ".join(orchestrator.split())
        for needle in (
            "**Git is Shepherdr's.**", "`herdr-task ready` pushes that branch and opens its pull request",
            "a brief never tells the worker to create, switch or push a branch, open a pull request or merge",
            "nor which branch to start from", "in whatever words", "the repository's `@BASE`",
            "`--base <branch>` on `task add`", "`--set merge=…`", "[--base <branch>]",
            "the worker updates its own task branch",
        ):
            self.assertIn(needle, text)

    def test_agents_template_stays_focused(self):
        self.assertLessEqual(len(template_text()), 8400)


if __name__ == "__main__":
    unittest.main()
