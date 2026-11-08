import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_epic import infoblock, store
from tests.helpers import HOME_PATH, StubEnv, forbidden, make_repo, run_cli, temp_home

SPACE = "sp-epic"


def flag(call, name):
    return call[call.index(name) + 1]


def call_starting(stub, binary, *prefix):
    calls = [c for c in stub.calls(binary) if c[:len(prefix)] == list(prefix)]
    assert len(calls) == 1, calls
    return calls[0]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        home = temp_home()
        home.__enter__()
        self.addCleanup(home.__exit__, None, None, None)
        self.stub = StubEnv().__enter__()
        self.addCleanup(self.stub.__exit__, None, None, None)
        env = mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "t1"})
        env.start()
        self.addCleanup(env.stop)

        self.root = base / "epics"
        self.root.mkdir()
        from herdr_epic import config
        config.save({"roots": [{"name": "epics", "path": str(self.root)}], "language": "en"})
        self.api = make_repo(base, "shop-api")
        self.ios = make_repo(base, "shop-ios")
        make_repo(base, "shop-web", branch="develop")
        self.web = base / "shop-web"
        epic = store.create(self.root, "Gift cards", [
            {"name": "shop-api", "path": str(self.api), "base": None},
            {"name": "shop-ios", "path": str(self.ios), "base": None},
            {"name": "shop-web", "path": str(self.web), "base": "develop"},
        ])
        self.folder = Path(epic["folder"])
        self.epic = store.update(self.folder, lambda e: e.update(space=SPACE))
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": [{"id": SPACE}]}))

    def caller(self, labels, live=("t1",)):
        """t1 is the caller; `live` terminals are listed as coordinators of the epic's space."""
        stub = self.stub
        stub.respond("hc", ["get", "t1"], json.dumps({"address": "t1", "labels": labels}))
        rows = [{"address": a, "state": "live", "agent": "claude-code", "template": "assistant"} for a in live]
        stub.respond("hc", ["ls"], json.dumps({"terminals": rows}))
        for a in live:
            if a != "t1":
                stub.respond("hc", ["get", a], json.dumps({"address": a, "labels": self.orch_labels()}))

    def orch_labels(self):
        return {"herdr.role": "coordinator", "herdr.coordinator.space": SPACE}

    def herdr_task(self, ref="SI-1", start=None):
        self.stub.respond("herdr-task", ["add"], json.dumps({"ref": ref, "id": 1, "state": "todo"}))
        self.stub.respond("herdr-task", ["start"], json.dumps(
            start or {"ref": ref, "terminal": "t7", "space": SPACE, "parent": "t1"}))

    def cli_json(self, argv, code=0):
        got, out = run_cli([*argv, "--json"])
        self.assertEqual(got, code, out)
        return json.loads(out)


class TaskAddTest(Fixture):
    def test_prompt_gets_infoblock_and_flags(self):
        self.caller(self.orch_labels())
        self.herdr_task()
        out = self.cli_json(["task", "add", "--epic", self.epic["id"], "--repo", "shop-ios",
                             "--title", "Scan to join", "--prompt", "Do X", "--agent", "claude"])
        add = call_starting(self.stub, "herdr-task", "add")
        self.assertEqual(add[1], "Scan to join")
        self.assertEqual(flag(add, "--folder"), str(self.ios))
        self.assertEqual(flag(add, "--space"), SPACE)
        self.assertNotIn("--base", add)
        self.assertEqual(flag(add, "--agent"), "claude")
        prompt = Path(flag(add, "--prompt-file")).read_text(encoding="utf-8")
        self.assertTrue(prompt.startswith("Do X\n\n<herdr-epic>"), prompt[:40])
        self.assertIn("herdr-epic request --repo", prompt)
        start = call_starting(self.stub, "herdr-task", "start")
        self.assertEqual(start[1], "SI-1")
        self.assertEqual(flag(start, "--agent"), "claude")
        self.assertEqual(flag(start, "--folder"), str(self.ios))
        self.assertEqual(flag(start, "--space"), SPACE)
        self.assertEqual(flag(start, "--parent"), "t1")
        self.assertEqual(out, {"ref": "SI-1", "repo": "shop-ios", "folder": str(self.ios), "terminal": "t7"})
        task = store.load(self.folder)["tasks"][-1]
        self.assertEqual((task["ref"], task["repo"], task["title"], task["requested_by"]),
                         ("SI-1", "shop-ios", "Scan to join", None))
        self.assertTrue(task["added"])

    def test_task_add_on_closed_epic_is_refused(self):
        store.update(self.folder, lambda e: e.update(state="done"))
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
        self.caller(self.orch_labels())
        self.herdr_task()
        out = self.cli_json(["task", "add", "--epic", self.epic["id"], "--repo", "shop-api", "--title", "X",
                             "--prompt", "Do X"], code=1)
        self.assertIn("this epic is closed", out["error"])
        self.assertEqual([c for c in self.stub.calls("herdr-task") if c[:1] in (["add"], ["start"])], [])
        self.assertEqual([c for c in self.stub.calls("herdr-app") if c[:1] == ["do"]], [])
        self.assertEqual(store.load(self.folder)["tasks"], [])

    def test_prompt_file_not_utf8_is_a_clear_failure(self):
        self.caller(self.orch_labels())
        self.herdr_task()
        brief = Path(self.tmp.name) / "brief.md"
        brief.write_bytes(b"\xff\xfe brief")
        out = self.cli_json(["task", "add", "--repo", "shop-api", "--title", "X", "--prompt-file", str(brief)], code=1)
        self.assertIn("not UTF-8", out["error"])
        self.assertEqual([c for c in self.stub.calls("herdr-task") if c[:1] == ["add"]], [])

    def test_epic_from_orchestrator_terminal_without_flag(self):
        self.caller(self.orch_labels())
        self.herdr_task()
        out = self.cli_json(["task", "add", "--repo", "shop-api", "--title", "Balance API", "--prompt", "Do Y"])
        self.assertEqual(out["ref"], "SI-1")
        self.assertEqual(store.load(self.folder)["tasks"][-1]["repo"], "shop-api")

    def test_prompt_file_and_pass_through_flags(self):
        self.caller(self.orch_labels())
        self.herdr_task()
        brief = Path(self.tmp.name) / "brief.md"
        brief.write_text("Long brief\n", encoding="utf-8")
        self.cli_json(["task", "add", "--repo", "shop-api", "--title", "Balance API", "--prompt-file", str(brief),
                       "--after", "SI-1,WEB-2", "--set", "review=on", "--set", "merge=ask me first",
                       "--requested-by", "SA-4"])
        add = call_starting(self.stub, "herdr-task", "add")
        self.assertTrue(Path(flag(add, "--prompt-file")).read_text(encoding="utf-8").startswith("Long brief\n\n<herdr-epic>"))
        self.assertEqual(flag(add, "--after"), "SI-1,WEB-2")
        sets = [add[i + 1] for i, a in enumerate(add) if a == "--set"]
        self.assertEqual(sets, ["review=on", "merge=ask me first"])
        self.assertNotIn("--agent", call_starting(self.stub, "herdr-task", "start"))
        self.assertEqual(store.load(self.folder)["tasks"][-1]["requested_by"], "SA-4")

    def test_needs_exactly_one_prompt(self):
        self.caller(self.orch_labels())
        out = self.cli_json(["task", "add", "--repo", "shop-api", "--title", "T"], code=1)
        self.assertIn("--prompt", out["error"])
        self.assertEqual(self.stub.calls("herdr-task"), [])

    def test_unknown_repo_lists_choices(self):
        self.caller(self.orch_labels())
        out = self.cli_json(["task", "add", "--repo", "shop-android", "--title", "T", "--prompt", "p"], code=1)
        for name in ("shop-android", "shop-api", "shop-ios", "shop-web", "herdr-epic repo add"):
            self.assertIn(name, out["error"])
        self.assertEqual(self.stub.calls("herdr-task"), [])

    def test_worker_cannot_task_add(self):
        self.caller({"herdr.task": "shop-api#1", "herdr.task.space": SPACE}, live=())
        out = self.cli_json(["task", "add", "--epic", self.epic["id"], "--repo", "shop-ios",
                             "--title", "T", "--prompt", "p"], code=1)
        self.assertIn("herdr-epic request", out["error"])
        self.assertEqual(self.stub.calls("herdr-task"), [])

    def test_caller_outside_any_epic_or_root_is_refused(self):
        self.caller({}, live=())
        out = self.cli_json(["task", "add", "--epic", self.epic["id"], "--repo", "shop-ios",
                             "--title", "T", "--prompt", "p"], code=1)
        self.assertIn("orchestrator", out["error"])
        self.assertEqual(self.stub.calls("herdr-task"), [])

    def test_root_cannot_task_add_and_is_pointed_to_request(self):
        self.caller({}, live=())
        with mock.patch("pathlib.Path.cwd", return_value=self.root):
            for argv in (["task", "add", "--repo", "shop-ios", "--title", "T", "--prompt", "p"],
                         ["task", "add", "--epic", "Gift cards", "--repo", "shop-ios", "--title", "T", "--prompt", "p"]):
                out = self.cli_json(argv, code=1)
                self.assertIn("herdr-epic request --epic", out["error"])
        self.assertEqual(self.stub.calls("herdr-task"), [])

    def test_orchestrator_by_folder_with_no_live_orchestrator_parents_to_caller(self):
        self.caller({}, live=())
        self.herdr_task()
        with mock.patch("pathlib.Path.cwd", return_value=self.folder):
            self.cli_json(["task", "add", "--repo", "shop-ios", "--title", "T", "--prompt", "p"])
        self.assertEqual(flag(call_starting(self.stub, "herdr-task", "start"), "--parent"), "t1")

    def test_parent_is_live_orchestrator_not_caller(self):
        self.caller({}, live=("t4",))
        self.herdr_task()
        with mock.patch("pathlib.Path.cwd", return_value=self.folder):
            self.cli_json(["task", "add", "--repo", "shop-ios", "--title", "T", "--prompt", "p"])
        self.assertEqual(flag(call_starting(self.stub, "herdr-task", "start"), "--parent"), "t4")

    def test_repo_base_passed_as_base(self):
        self.caller(self.orch_labels())
        self.herdr_task()
        self.cli_json(["task", "add", "--repo", "shop-web", "--title", "T", "--prompt", "p"])
        add = call_starting(self.stub, "herdr-task", "add")
        self.assertEqual(flag(add, "--base"), "develop")
        self.assertEqual(flag(add, "--folder"), str(self.web))

    def test_task_base_wins_over_repo_base(self):
        self.caller(self.orch_labels())
        self.herdr_task()
        self.cli_json(["task", "add", "--repo", "shop-web", "--title", "T", "--prompt", "p",
                       "--base", "release"])
        add = call_starting(self.stub, "herdr-task", "add")
        self.assertEqual(add.count("--base"), 1)
        self.assertEqual(flag(add, "--base"), "release")

    def test_task_base_without_repo_base(self):
        self.caller(self.orch_labels())
        self.herdr_task()
        self.cli_json(["task", "add", "--repo", "shop-ios", "--title", "T", "--prompt", "p", "--base", "hotfix"])
        self.assertEqual(flag(call_starting(self.stub, "herdr-task", "add"), "--base"), "hotfix")

    def test_queued_start_is_reported(self):
        self.caller(self.orch_labels())
        self.herdr_task(start={"ref": "SI-1", "queued": True, "reason": "CPU is busy"})
        out = self.cli_json(["task", "add", "--repo", "shop-ios", "--title", "T", "--prompt", "p"])
        self.assertEqual(out, {"ref": "SI-1", "repo": "shop-ios", "folder": str(self.ios),
                               "queued": True, "reason": "CPU is busy"})

    def test_start_failure_keeps_task_and_names_ref(self):
        self.caller(self.orch_labels())
        self.stub.respond("herdr-task", ["add"], json.dumps({"ref": "SI-1"}))
        self.stub.respond("herdr-task", ["start"], json.dumps({"error": "no agent named klaude"}), code=1)
        out = self.cli_json(["task", "add", "--repo", "shop-ios", "--title", "T", "--prompt", "p", "--agent", "klaude"],
                            code=1)
        self.assertIn("no agent named klaude", out["error"])
        self.assertIn("SI-1", out["error"])
        self.assertEqual(out["ref"], "SI-1")
        self.assertEqual(store.load(self.folder)["tasks"][-1]["ref"], "SI-1")

    def test_later_task_lists_earlier_one_as_sibling(self):
        self.caller(self.orch_labels())
        self.herdr_task("SA-1")
        self.cli_json(["task", "add", "--repo", "shop-api", "--title", "Balance API", "--prompt", "p"])
        self.herdr_task("SI-1")
        self.cli_json(["task", "add", "--repo", "shop-ios", "--title", "Redeem screen", "--prompt", "p"])
        adds = [c for c in self.stub.calls("herdr-task") if c[0] == "add"]
        first = Path(flag(adds[0], "--prompt-file")).read_text(encoding="utf-8")
        second = Path(flag(adds[1], "--prompt-file")).read_text(encoding="utf-8")
        self.assertNotIn("Balance API", first)
        self.assertIn("SA-1", second)
        self.assertIn("Balance API", second)


class RepoAddTest(Fixture):
    def test_repo_add_appends_repo(self):
        self.caller(self.orch_labels())
        android = make_repo(self.tmp.name, "shop-android")
        out = self.cli_json(["repo", "add", f"shop-android={android}@main"])
        self.assertEqual(out["repo"], {"name": "shop-android", "path": str(android.resolve()), "base": "main"})
        self.assertEqual(store.load(self.folder)["repos"][-1]["name"], "shop-android")

    def test_repo_add_rejects_existing_name_and_non_git(self):
        self.caller(self.orch_labels())
        out = self.cli_json(["repo", "add", f"shop-api={self.api}"], code=1)
        self.assertIn("shop-api", out["error"])
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        out = self.cli_json(["repo", "add", f"plain={plain}"], code=1)
        self.assertIn("git", out["error"])
        self.assertEqual(len(store.load(self.folder)["repos"]), 3)

    def test_root_cannot_repo_add(self):
        self.caller({}, live=())
        android = make_repo(self.tmp.name, "shop-android")
        with mock.patch("pathlib.Path.cwd", return_value=self.root):
            out = self.cli_json(["repo", "add", f"shop-android={android}", "--epic", "Gift cards"], code=1)
        self.assertIn("herdr-epic request --epic", out["error"])
        self.assertEqual(len(store.load(self.folder)["repos"]), 3)

    def test_worker_cannot_repo_add(self):
        self.caller({"herdr.task": "shop-api#1", "herdr.task.space": SPACE}, live=())
        android = make_repo(self.tmp.name, "shop-android")
        out = self.cli_json(["repo", "add", f"shop-android={android}"], code=1)
        self.assertIn("herdr-epic request", out["error"])


class InfoBlockTest(unittest.TestCase):
    def epic(self):
        return {
            "id": "e-7f3a", "title": "Gift cards", "folder": "/srv/epics/Gift cards", "space": SPACE,
            "repos": [{"name": "shop-api", "path": "/srv/code/shop-api", "base": None},
                      {"name": "shop-ios", "path": "/srv/code/shop-ios", "base": "develop"}],
            "tasks": [],
        }

    def test_infoblock_lists_siblings_and_no_user_specific_strings(self):
        siblings = [{"ref": "SA-1", "repo": "shop-api", "title": "Balance API"},
                    {"ref": "SI-2", "repo": "shop-ios", "title": "Redeem screen"}]
        block = infoblock.render(self.epic(), siblings, "en")
        self.assertTrue(block.startswith("<herdr-epic>\n"))
        self.assertTrue(block.endswith("\n</herdr-epic>"))
        for text in ("Gift cards", "/srv/epics/Gift cards",
                     "/srv/epics/Gift cards/epic.md", "/srv/epics/Gift cards/decisions.md",
                     "/srv/epics/Gift cards/handoff.md", "read-only",
                     "SA-1", "Balance API", "SI-2", "Redeem screen", "shop-api", "shop-ios",
                     'herdr-epic request --repo <repo> --agent <agent> --context "<what you know>"',
                     "--context-file", "herdr-task add", "start other agents", "herdr-epic-reply",
                     "herdr-task ready", "herdr-epic show"):
            self.assertIn(text, block)
        source = Path(infoblock.__file__).read_text(encoding="utf-8")
        for text in (block, source):
            self.assertIsNone(HOME_PATH.search(text))
            for bad in forbidden():
                self.assertNotIn(bad, text.lower())

    def test_forbidden_strings_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HERDR_EPIC_FORBIDDEN": " Acme, Wile E ,,"}):
            self.assertEqual(forbidden(), ["acme", "wile e"])
        self.assertIsNotNone(HOME_PATH.search("see /home/someone/notes"))
        self.assertIsNotNone(HOME_PATH.search("/Users/someone"))
        self.assertIsNone(HOME_PATH.search("/srv/epics/Gift cards"))

    def test_infoblock_covers_each_situation_a_worker_meets(self):
        block = " ".join(infoblock.render(self.epic(), [], "en").split())
        for text in (
            "whose work may span several git repositories",
            "go by what they want to happen, not by how they phrase it",
            "ask a sibling task's agent",
            "All new work goes through the orchestrator",
            "Ask them once, with your CLI's question tool, before sending anything",
            "the epic's orchestrator must know about all work in the epic",
            "If they insist, still send it",
            "Don't send it unasked, and don't do it yourself",
            "Questions about the epic or a sibling task are not requests",
            "If it was not saved, say so plainly",
            "Never do the work yourself instead",
            "--context names the earlier request id",
            # Git is Shepherdr's, over any step in the brief; ready pushes and opens the pull request.
            "Shepherdr runs this task's git",
            "This holds over any step earlier in this brief that says otherwise",
            "Don't create or switch to another branch, push, open a pull request or merge",
            "even when the brief or the person asks you to",
            "`herdr-task ready` pushes your branch and opens its pull request (on GitHub)",
            "rebasing your own task branch, are yours",
            # Shepherdr's own merge steps (no pull request) and a task in the folder itself still work.
            'Merge steps Shepherdr itself sends you ("Merge task ... now"',
            "No branch named there: you work in the folder itself; leave its branch as it is",
            "rebase onto the remote's copy of your base",
            "Never start another branch for it",
        ):
            self.assertIn(text, block)

    def test_infoblock_has_no_eval_mirroring_examples_or_false_promises(self):
        block = infoblock.render(self.epic(), [], "en")
        for bad in ("another part of the product", "has to change as well", "follow this change",
                    "stays saved", "spans several", "open a task for"):
            self.assertNotIn(bad, block)

    def test_infoblock_stays_within_size_budget(self):
        siblings = [{"ref": "SA-1", "repo": "shop-api", "title": "Gift card balance and redeem API"},
                    {"ref": "SW-3", "repo": "shop-web", "title": "Gift card checkout"}]
        epic = {**self.epic(), "repos": [*self.epic()["repos"], {"name": "shop-web", "path": "/c", "base": None}]}
        self.assertLessEqual(len(infoblock.render(epic, siblings, "zh")), 5900)

    def test_infoblock_without_siblings_says_so(self):
        block = infoblock.render(self.epic(), [], "en")
        self.assertIn("none yet", block)

    def test_infoblock_stays_english_and_names_persons_language(self):
        block = infoblock.render(self.epic(), [], "zh")
        self.assertIn("herdr-epic request --repo", block)
        self.assertIn("Chinese", block)
        self.assertEqual(block.encode("ascii", "ignore").decode(), block)


if __name__ == "__main__":
    unittest.main()
