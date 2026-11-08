import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_epic import Fail, config, orchestrator, store
from tests.helpers import StubEnv, make_repo, run_cli, temp_home

SPACE = "sp-epic"


def term(address, state="live", agent="claude-code", template=None):
    entry = {"address": address, "state": state, "agent": agent, "cwd": "/x"}
    if template:
        entry["template"] = template
    return entry


def stub_terminals(stub, entries, labels):
    """entries: ls rows. labels: address -> labels dict."""
    stub.respond("hc", ["ls"], json.dumps({"terminals": entries}))
    for address, lab in labels.items():
        stub.respond("hc", ["get", address], json.dumps({"address": address, "labels": lab}))


def coordinator_labels(space=SPACE, **extra):
    return {"herdr.role": "coordinator", "herdr.coordinator.space": space, **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "epics"
        self.root.mkdir()
        self.epic = store.create(self.root, "Gift cards", [])
        store.update(Path(self.epic["folder"]), lambda e: e.update(space=SPACE))
        self.epic = store.load(Path(self.epic["folder"]))
        self.app_home = Path(self.tmp.name) / "app"
        self.app_home.mkdir()
        old = os.environ.get("HERDR_APP_HOME")
        os.environ["HERDR_APP_HOME"] = str(self.app_home)
        self.addCleanup(lambda: os.environ.pop("HERDR_APP_HOME", None) if old is None
                        else os.environ.__setitem__("HERDR_APP_HOME", old))

    def spaces(self, stub, *ids):
        stub.respond("herdr-app", ["ls"], json.dumps({"spaces": [{"id": i} for i in ids]}))


class FindTest(Base):
    def test_find_matches_coordinator_space_label(self):
        with StubEnv() as stub:
            stub_terminals(stub, [term("t1"), term("t2")], {
                "t1": {"herdr.role": "coordinator", "herdr.coordinator.space": "other"},
                "t2": coordinator_labels(),
            })
            self.assertEqual(orchestrator.find(self.epic), "t2")

    def test_find_matches_herdr_space_label_and_null_template(self):
        with StubEnv() as stub:
            stub_terminals(stub, [term("t3", template=None)], {
                "t3": {"herdr.role": "coordinator", "herdr.space": SPACE}})
            self.assertEqual(orchestrator.find(self.epic), "t3")

    def test_find_ignores_remote_and_dead(self):
        with StubEnv() as stub:
            stub_terminals(stub, [term("m/t1"), term("t2", state="finished")], {
                "m/t1": coordinator_labels(), "t2": coordinator_labels()})
            self.assertIsNone(orchestrator.find(self.epic))

    def test_find_ignores_shell_left_after_agent_died(self):
        with StubEnv() as stub:
            stub_terminals(stub, [term("t2", agent="zsh")], {
                "t2": coordinator_labels(**{"herdr.resume": "x"})})
            self.assertIsNone(orchestrator.find(self.epic))
            self.assertEqual(orchestrator.reviving(self.epic), "t2")

    def test_every_shepherdr_shell_counts_as_a_shell(self):
        for shell in ("ksh", "tcsh", "csh", "nu", "xonsh", "pwsh", "powershell", "elvish", "bash"):
            with self.subTest(shell), StubEnv() as stub:
                stub_terminals(stub, [term("t2", agent=shell)], {"t2": coordinator_labels(**{"herdr.resume": "x"})})
                self.assertIsNone(orchestrator.find(self.epic))
                self.assertEqual(orchestrator.reviving(self.epic), "t2")

    def test_reviving_needs_resume_label(self):
        with StubEnv() as stub:
            stub_terminals(stub, [term("t2", agent="zsh")], {"t2": coordinator_labels()})
            self.assertIsNone(orchestrator.reviving(self.epic))


class EnsureTest(Base):
    def test_ensure_starts_when_absent(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            stub.respond("hc", ["run"], json.dumps(
                {"address": "t9", "first_message": {"sent": True, "started": True, "turn": 0}}))
            r = orchestrator.ensure(self.epic, "hello")
            self.assertEqual(r, {"address": "t9", "started": True, "sent": True, "error": None})
            run = [c for c in stub.calls("hc") if c[0] == "run"][0]
            self.assertIn(f"herdr.coordinator.space={SPACE}", run)
            self.assertIn("--no-parent", run)
            self.assertIn("--keep", run)
            self.assertEqual(run[run.index("--cwd") + 1], self.epic["folder"])
            self.assertEqual(run[-2], "assistant")
            self.assertTrue(run[-1].endswith("\n\nhello"), run[-1])
            self.assertNotIn("herdr.chat.slot", " ".join(run))

    def test_ensure_adopts_shepherdr_slot(self):
        (self.app_home / "shared.json").write_text(json.dumps({"coordinator.slots": {
            "coord-a": {"space": "other", "tab": "tbX", "pane": "pX"},
            "coord-b": {"space": SPACE, "tab": "tb1", "pane": "p1", "folder": "/f"},
        }}))
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            stub.respond("hc", ["run"], json.dumps({"address": "t9"}))
            orchestrator.ensure(self.epic, "hello")
            run = [c for c in stub.calls("hc") if c[0] == "run"][0]
            for label in ("herdr.chat=p1", "herdr.chat.slot=coord-b",
                          "herdr.chat.origin=slot:coord-b", "herdr.coordinator.tab=tb1"):
                self.assertIn(label, run)

    def test_ensure_reports_missing_address(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            stub.respond("hc", ["run"], json.dumps({"first_message": {}}))
            r = orchestrator.ensure(self.epic, "hello")
            self.assertIsNone(r["address"])
            self.assertFalse(r["started"])
            self.assertTrue(r["error"])

    def test_ensure_reports_first_message_not_sent(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            for reply in ({"address": "t9", "first_message": {"sent": False, "started": False, "turn": 0}},
                          {"address": "t9"}):
                stub.respond("hc", ["run"], json.dumps(reply))
                r = orchestrator.ensure(self.epic, "hello")
                self.assertEqual(r, {"address": "t9", "started": True, "sent": False, "error": None}, reply)

    def test_ensure_returns_existing_without_run(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [term("t2")], {"t2": coordinator_labels()})
            r = orchestrator.ensure(self.epic, "hello")
            self.assertEqual(r, {"address": "t2", "started": False, "sent": False, "error": None})
            self.assertEqual([c for c in stub.calls("hc") if c[0] == "run"], [])

    def test_ensure_waits_for_reviving(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            with mock.patch.object(orchestrator, "find", side_effect=[None, None, "t2"]), \
                 mock.patch.object(orchestrator, "reviving", return_value="t2"), \
                 mock.patch("time.sleep"):
                r = orchestrator.ensure(self.epic, "hello")
            self.assertEqual(r, {"address": "t2", "started": False, "sent": False, "error": None})
            self.assertEqual([c for c in stub.calls("hc") if c[0] == "run"], [])

    def test_ensure_reviving_timeout_is_error_without_second_start(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            with mock.patch.object(orchestrator, "find", return_value=None), \
                 mock.patch.object(orchestrator, "reviving", return_value="t2"), \
                 mock.patch("time.sleep"):
                r = orchestrator.ensure(self.epic, "hello", wait_s=3)
            self.assertIsNone(r["address"])
            self.assertTrue(r["error"])
            self.assertEqual([c for c in stub.calls("hc") if c[0] == "run"], [])

    def test_ensure_reports_error_not_raise(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            stub.respond("hc", ["run"], json.dumps({"error": {"message": "no pty"}}), code=3)
            r = orchestrator.ensure(self.epic, "hello")
            self.assertEqual(r["address"], None)
            self.assertFalse(r["started"])
            self.assertIn("no pty", r["error"])

    def test_ensure_space_recreates_missing_space(self):
        with StubEnv() as stub:
            self.spaces(stub, "sp-else")
            stub.respond("herdr-app", ["do", "space.new"],
                         json.dumps({"space": "s-new", "tab": "tb", "terminal": "t1"}))
            epic = orchestrator.ensure_space(self.epic)
            self.assertEqual(epic["space"], "s-new")
            self.assertEqual(store.load(Path(self.epic["folder"]))["space"], "s-new")
            call = stub.calls("herdr-app")[-1]
            self.assertEqual(call[call.index("--name") + 1], "Gift cards")
            self.assertEqual(call[call.index("--folder") + 1], self.epic["folder"])

    def test_ensure_space_refuses_closed_epic(self):
        store.update(Path(self.epic["folder"]), lambda e: e.update(state="done"))
        with StubEnv() as stub:
            self.spaces(stub)
            with self.assertRaises(Fail) as err:
                orchestrator.ensure_space(store.load(Path(self.epic["folder"])))
            self.assertIn("this epic is closed", str(err.exception))
            self.assertEqual([c for c in stub.calls("herdr-app") if c[0] == "do"], [])
            self.assertEqual(store.load(Path(self.epic["folder"]))["space"], SPACE)

    def test_ensure_space_keeps_existing(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            orchestrator.ensure_space(self.epic)
            self.assertEqual([c for c in stub.calls("herdr-app") if c[0] == "do"], [])


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def cfg(self, home, *names):
        roots = []
        for n in names:
            (self.dir / n).mkdir(exist_ok=True)
            roots.append({"name": n, "path": str(self.dir / n)})
        config.save({"roots": roots, "language": "en"})

    def test_new_rejects_non_git_repo(self):
        with temp_home() as home, StubEnv():
            self.cfg(home, "a")
            plain = self.dir / "shop-api"
            plain.mkdir()
            code, out = run_cli(["new", "Gift cards", "--repo", f"shop-api={plain}", "--json"])
            self.assertEqual(code, 1)
            self.assertIn("shop-api", json.loads(out)["error"])
            self.assertEqual(list((self.dir / "a").iterdir()), [])

    def test_new_requires_root_when_several(self):
        with temp_home() as home, StubEnv():
            self.cfg(home, "a", "b")
            code, out = run_cli(["new", "Gift cards", "--json"])
            self.assertEqual(code, 1)
            err = json.loads(out)["error"]
            self.assertIn("a", err)
            self.assertIn("b", err)

    def test_new_creates_epic_and_starts_orchestrator(self):
        with temp_home() as home, StubEnv() as stub:
            self.cfg(home, "a")
            repo = make_repo(self.dir, "shop-api")
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"],
                         json.dumps({"space": "s1", "tab": "tb", "terminal": "t1"}))
            stub.respond("hc", ["ls"], json.dumps({"terminals": []}))
            stub.respond("hc", ["run"], json.dumps({"address": "t5", "first_message": {"sent": True}}))
            code, out = run_cli(["new", "Gift cards", "--repo", f"shop-api={repo}@main",
                                 "--message", "go", "--json"])
            self.assertEqual(code, 0, out)
            data = json.loads(out)
            self.assertEqual(data["epic"]["space"], "s1")
            self.assertEqual(data["epic"]["repos"], [
                {"name": "shop-api", "path": str(repo.resolve()), "base": "main"}])
            self.assertEqual(data["orchestrator"]["address"], "t5")
            run = [c for c in stub.calls("hc") if c[0] == "run"][0]
            self.assertEqual(run[-2], "assistant")
            self.assertTrue(run[-1].endswith("\n\ngo"), run[-1])

    def test_new_exits_1_with_epic_when_orchestrator_fails(self):
        with temp_home() as home, StubEnv() as stub:
            self.cfg(home, "a")
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"],
                         json.dumps({"space": "s1", "tab": "tb", "terminal": "t1"}))
            stub.respond("hc", ["ls"], json.dumps({"terminals": []}))
            stub.respond("hc", ["run"], json.dumps({"error": "boom"}), code=1)
            code, out = run_cli(["new", "Gift cards", "--json"])
            self.assertEqual(code, 1)
            data = json.loads(out)
            self.assertIn("boom", data["error"])
            self.assertEqual(data["epic"]["space"], "s1")
            self.assertTrue((self.dir / "a" / "Gift cards" / "epic.json").is_file())

    def test_new_started_but_not_sent_queues_the_opening_message(self):
        with temp_home() as home, StubEnv() as stub, mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "t2"}):
            self.cfg(home, "a")
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"], json.dumps({"space": "s1", "tab": "tb", "terminal": "t1"}))
            stub.respond("hc", ["ls"], json.dumps({"terminals": []}))
            stub.respond("hc", ["get", "t2"], json.dumps({"address": "t2", "labels": {}}))
            stub.respond("hc", ["run"], json.dumps({"address": "t5", "first_message": {"sent": False}}))
            code, out = run_cli(["new", "Gift cards", "--message", "go", "--json"])
            self.assertEqual(code, 1, out)
            data = json.loads(out)
            self.assertEqual((data["request"], data["saved"]), ("r-1", True))
            self.assertIn("was saved", data["error"])
            self.assertIn("herdr-epic pending", data["error"])
            req = store.load(self.dir / "a" / "Gift cards")["requests"]
            self.assertEqual(len(req), 1)
            self.assertEqual({k: req[0][k] for k in ("id", "repo", "context", "from_terminal", "state", "orchestrator")},
                             {"id": "r-1", "repo": None, "context": "go", "from_terminal": "t2", "state": "queued",
                              "orchestrator": "t5"})
            code, out = run_cli(["pending", "--epic", "Gift cards", "--json"])
            self.assertEqual((code, [r["id"] for r in json.loads(out)["requests"]]), (0, ["r-1"]))
            code, text = run_cli(["pending", "--epic", "Gift cards"])
            self.assertIn("repo=- ", text)

    def test_new_waits_for_slot_written_after_space_new(self):
        with temp_home() as home, StubEnv() as stub:
            self.cfg(home, "a")
            app_home = self.dir / "app"
            app_home.mkdir()
            old = os.environ.get("HERDR_APP_HOME")
            os.environ["HERDR_APP_HOME"] = str(app_home)
            self.addCleanup(lambda: os.environ.pop("HERDR_APP_HOME", None) if old is None
                            else os.environ.__setitem__("HERDR_APP_HOME", old))
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"],
                         json.dumps({"space": "s1", "tab": "tb", "terminal": "t1"}))
            stub.respond("hc", ["ls"], json.dumps({"terminals": []}))
            stub.respond("hc", ["run"], json.dumps({"address": "t5", "first_message": {"sent": True}}))

            def late_slot(_seconds):
                (app_home / "shared.json").write_text(json.dumps({"coordinator.slots": {
                    "coord-1": {"space": "s1", "tab": "tb9", "pane": "p9"}}}))

            with mock.patch("time.sleep", late_slot):
                code, out = run_cli(["new", "Gift cards", "--json"])
            self.assertEqual(code, 0, out)
            run = [c for c in stub.calls("hc") if c[0] == "run"][0]
            for label in ("herdr.chat=p9", "herdr.chat.slot=coord-1",
                          "herdr.chat.origin=slot:coord-1", "herdr.coordinator.tab=tb9"):
                self.assertIn(label, run)

    def test_root_add_writes_agents_and_claude(self):
        with temp_home() as home, StubEnv() as stub:
            repo = make_repo(self.dir, "shop-api")
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"],
                         json.dumps({"space": "s-root", "tab": "tb", "terminal": "t1"}))
            root = self.dir / "work" / "cards"
            code, out = run_cli(["root", "add", str(root), "--name", "cards",
                                 "--repo", f"shop-api={repo}@main", "--json"])
            self.assertEqual(code, 0, out)
            # One source of rules: CLAUDE.md is a link to AGENTS.md, not an @import (an import is
            # "external" for every orchestrator working one folder down and Claude Code stops to ask).
            self.assertTrue((root / "CLAUDE.md").is_symlink())
            self.assertEqual(os.readlink(root / "CLAUDE.md"), "AGENTS.md")
            self.assertTrue((root / "AGENTS.md").read_text())
            self.assertEqual((root / "CLAUDE.md").read_text(), (root / "AGENTS.md").read_text())
            meta = json.loads((root / "root.json").read_text())
            self.assertEqual(meta["name"], "cards")
            self.assertEqual(meta["space"], "s-root")
            self.assertEqual(meta["repos"], [
                {"name": "shop-api", "path": str(repo.resolve()), "base": "main"}])
            self.assertIn({"name": "cards", "path": str(root.resolve())}, config.load()["roots"])
            do = [c for c in stub.calls("herdr-app") if c[0] == "do"][0]
            self.assertEqual(do[do.index("--name") + 1], "cards")
            self.assertEqual([c for c in stub.calls("hc") if c[0] == "run"], [])

    def test_root_add_replaces_legacy_import_and_keeps_own_claude_md(self):
        with temp_home() as home, StubEnv() as stub:
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": [{"id": "s-root"}]}))
            for name, text in (("legacy", "@AGENTS.md\n"), ("own", "my own notes\n")):
                root = self.dir / name
                root.mkdir()
                (root / "CLAUDE.md").write_text(text, encoding="utf-8")
                (root / "root.json").write_text(json.dumps({"name": name, "space": "s-root", "repos": []}))
                code, out = run_cli(["root", "add", str(root), "--json"])
                self.assertEqual(code, 0, out)
            legacy, own = self.dir / "legacy", self.dir / "own"
            self.assertTrue((legacy / "CLAUDE.md").is_symlink())
            self.assertEqual(os.readlink(legacy / "CLAUDE.md"), "AGENTS.md")
            self.assertFalse((own / "CLAUDE.md").is_symlink())
            self.assertEqual((own / "CLAUDE.md").read_text(encoding="utf-8"), "my own notes\n")
            self.assertIn("CLAUDE.md", json.dumps(json.loads(out)))

    def test_root_add_keeps_differing_agents_md(self):
        with temp_home() as home, StubEnv() as stub:
            root = self.dir / "cards"
            root.mkdir()
            (root / "AGENTS.md").write_text("mine\n")
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"],
                         json.dumps({"space": "s-root", "tab": "tb", "terminal": "t1"}))
            code, out = run_cli(["root", "add", str(root), "--json"])
            self.assertEqual(code, 0, out)
            self.assertEqual((root / "AGENTS.md").read_text(), "mine\n")
            self.assertTrue((root / "AGENTS.md.new").is_file())
            self.assertIn("AGENTS.md.new", json.dumps(json.loads(out)))


    def test_root_add_upgrades_agents_md_only_while_unchanged(self):
        import hashlib
        template = (Path(orchestrator.__file__).parent / "templates" / "root-AGENTS.md").read_text(encoding="utf-8")
        old = "the template of an older herdr-epic\n"
        with temp_home() as home, StubEnv() as stub:
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": [{"id": "s-root"}]}))
            for name, text in (("same", old), ("edited", "the person's own rules\n")):
                root = self.dir / name
                root.mkdir()
                (root / "AGENTS.md").write_text(text, encoding="utf-8")
                (root / "root.json").write_text(json.dumps({
                    "name": name, "space": "s-root", "repos": [],
                    "agents_sha256": hashlib.sha256(old.encode("utf-8")).hexdigest()}), encoding="utf-8")
                code, out = run_cli(["root", "add", str(root), "--json"])
                self.assertEqual(code, 0, out)
            same, edited = self.dir / "same", self.dir / "edited"
            self.assertEqual((same / "AGENTS.md").read_text(encoding="utf-8"), template)
            self.assertFalse((same / "AGENTS.md.new").exists())
            self.assertEqual(json.loads((same / "root.json").read_text())["agents_sha256"],
                             hashlib.sha256(template.encode("utf-8")).hexdigest())
            self.assertEqual((edited / "AGENTS.md").read_text(encoding="utf-8"), "the person's own rules\n")
            self.assertEqual((edited / "AGENTS.md.new").read_text(encoding="utf-8"), template)

    def test_root_add_records_the_template_hash(self):
        import hashlib
        template = (Path(orchestrator.__file__).parent / "templates" / "root-AGENTS.md").read_bytes()
        with temp_home() as home, StubEnv() as stub:
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"], json.dumps({"space": "s-root"}))
            root = self.dir / "cards"
            self.assertEqual(run_cli(["root", "add", str(root), "--json"])[0], 0)
            self.assertEqual(json.loads((root / "root.json").read_text())["agents_sha256"],
                             hashlib.sha256(template).hexdigest())

if __name__ == "__main__":
    unittest.main()


ASSISTANT_MD = "---\nname: assistant\ndescription: Coordinates a space.\ncommand: claude\n---\nYou are the coordinator of one space.\n"


class AssistantSettingsTest(Base):
    """Orchestrators start the way Shepherdr's window starts an Assistant: with the person's Assistant settings."""

    def setUp(self):
        super().setUp()
        self.templates = Path(os.environ["HERDR_TASK_TEMPLATES"])
        self.templates.mkdir(parents=True, exist_ok=True)
        (self.templates / "assistant.md").write_text(ASSISTANT_MD, encoding="utf-8")
        self.settings = Path(os.environ["HERDR_TASK_SETTINGS"])
        self.addCleanup(lambda: self.settings.unlink(missing_ok=True))

    def write_settings(self, data):
        self.settings.write_text(json.dumps(data), encoding="utf-8")

    def test_no_settings_keeps_the_assistant_template(self):
        self.assertIsNone(orchestrator.coordinator_pick(SPACE))
        self.assertIsNone(orchestrator.one_off_template(None))

    def test_space_setting_wins_over_machine_and_everywhere(self):
        self.write_settings({"everywhere": {"coordinator": {"agent": "claude", "perm": "manual"}},
                             "machines": {"local": {"coordinator": {"agent": "claude", "perm": "plan"}}},
                             "spaces": {SPACE: {"coordinator": {"agent": "claude", "perm": "auto"}}}})
        self.assertEqual(orchestrator.coordinator_pick(SPACE)["perm"], "auto")
        self.assertEqual(orchestrator.coordinator_pick("sp-other")["perm"], "plan")

    def test_agent_argv_matches_shepherdr(self):
        self.assertEqual(orchestrator.agent_argv({"agent": "claude", "perm": "auto", "model": "m1", "effort": "high"}),
                         ["claude", "--model", "m1", "--effort", "high", "--settings", '{"fastMode": false}',
                          "--permission-mode", "auto"])
        self.assertEqual(orchestrator.agent_argv({"agent": "claude", "perm": "manual"})[-2:],
                         ["--permission-mode", "default"])
        self.assertIn("--dangerously-skip-permissions", orchestrator.agent_argv({"agent": "claude", "perm": "bypassPermissions"}))
        self.assertEqual(orchestrator.agent_argv({"agent": "codex", "perm": "auto"}),
                         ["codex", "--no-daemon", "-s", "workspace-write", "-a", "on-request"])
        self.assertEqual(orchestrator.agent_argv({"argv": ["my-claude", "--x"]}), ["my-claude", "--x"])

    def test_one_off_template_carries_the_settings_and_the_assistant_body(self):
        path = orchestrator.one_off_template({"agent": "claude", "perm": "auto", "model": "m1"})
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.name.startswith("herdr-epic-assistant-"))
        self.assertEqual(path.parent, self.templates)
        self.assertIn(f"name: {path.stem}\n", text)
        self.assertIn("command: claude --model m1 --settings '{\"fastMode\": false}' --permission-mode auto\n", text)
        self.assertTrue(text.endswith("---\nYou are the coordinator of one space.\n"))

    def test_ensure_starts_with_a_one_off_template_and_removes_it(self):
        self.write_settings({"everywhere": {"coordinator": {"agent": "claude", "perm": "auto"}}})
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            stub.respond("hc", ["run"], json.dumps({"address": "t9", "first_message": {"sent": True}}))
            r = orchestrator.ensure(self.epic, "hello")
            self.assertTrue(r["sent"], r)
            run = [c for c in stub.calls("hc") if c[0] == "run"][0]
            name = run[-2]
            self.assertTrue(name.startswith("herdr-epic-assistant-"), run)
            self.assertEqual(sorted(p.name for p in self.templates.iterdir()), ["assistant.md"])


class ForegroundTest(Base):
    def test_orchestrator_whose_agent_has_not_reported_yet_is_found_by_its_foreground(self):
        # Claude Code still on a start screen: hc ls says "sh", the foreground program is claude.
        with StubEnv() as stub:
            stub.respond("hc", ["ls"], json.dumps({"terminals": [term("t12", agent="sh", template="assistant")]}))
            stub.respond("hc", ["get", "t12"], json.dumps({"address": "t12", "labels": coordinator_labels(),
                                                           "foreground": {"argv": ["claude"]}}))
            self.assertEqual(orchestrator.find(self.epic), "t12")
            self.assertIsNone(orchestrator.reviving(self.epic))

    def test_bare_shell_with_resume_label_is_still_reviving(self):
        with StubEnv() as stub:
            stub.respond("hc", ["ls"], json.dumps({"terminals": [term("t3", agent="zsh")]}))
            stub.respond("hc", ["get", "t3"], json.dumps({"address": "t3",
                                                          "labels": coordinator_labels(**{"herdr.resume": "{}"}),
                                                          "foreground": {"argv": ["-zsh"]}}))
            self.assertIsNone(orchestrator.find(self.epic))
            self.assertEqual(orchestrator.reviving(self.epic), "t3")


class RestartTest(Base):
    def test_restart_replaces_the_orchestrator_and_asks_it_to_read_pending(self):
        cfg_path = Path(os.environ["HERDR_EPIC_CONFIG"])
        config.save({"roots": [{"name": "epics", "path": str(self.root)}], "language": "en"}, cfg_path)
        self.addCleanup(lambda: cfg_path.unlink(missing_ok=True))
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [term("t5")], {"t5": coordinator_labels()})
            stub.respond("hc", ["kill"], json.dumps({"ok": True}))
            stub.respond("hc", ["rm"], json.dumps({"ok": True}))
            stub.respond("hc", ["run"], json.dumps({"address": "t9", "first_message": {"sent": True}}))
            code, out = run_cli(["restart", self.epic["id"], "--json"])
            self.assertEqual(code, 0, out)
            data = json.loads(out)
            self.assertEqual(data["killed"], ["t5"])
            self.assertEqual(data["orchestrator"]["address"], "t9")
            calls = stub.calls("hc")
            kinds = [c[0] for c in calls]
            self.assertLess(kinds.index("kill"), kinds.index("run"))
            run = [c for c in calls if c[0] == "run"][0]
            self.assertIn("herdr-epic pending", run[-1])

    def save_cfg(self):
        cfg_path = Path(os.environ["HERDR_EPIC_CONFIG"])
        config.save({"roots": [{"name": "epics", "path": str(self.root)}], "language": "en"}, cfg_path)
        self.addCleanup(lambda: cfg_path.unlink(missing_ok=True))

    def test_an_orchestrator_cannot_restart_itself(self):
        self.save_cfg()
        with StubEnv() as stub, mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "t5"}):
            stub_terminals(stub, [term("t5")], {"t5": coordinator_labels()})
            code, out = run_cli(["restart", self.epic["id"], "--json"])
            self.assertEqual(code, 1, out)
            self.assertEqual([c for c in stub.calls("hc") if c[0] in ("kill", "rm", "run")], [])

    def test_restart_kills_every_coordinator_terminal_of_the_space(self):
        # A duplicate from an earlier double start, and a stale shell (no herdr.resume) holding the chat slot.
        self.save_cfg()
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [term("t5"), term("t6"), term("t7", agent="zsh"), term("t8")],
                           {"t5": coordinator_labels(), "t6": coordinator_labels(), "t7": coordinator_labels(),
                            "t8": coordinator_labels(space="sp-other")})
            stub.respond("hc", ["kill"], json.dumps({"ok": True}))
            stub.respond("hc", ["rm"], json.dumps({"ok": True}))
            stub.respond("hc", ["run"], json.dumps({"address": "t9", "first_message": {"sent": True}}))
            code, out = run_cli(["restart", self.epic["id"], "--json"])
            self.assertEqual(code, 0, out)
            self.assertEqual(sorted(json.loads(out)["killed"]), ["t5", "t6", "t7"])
            self.assertEqual(json.loads(out)["orchestrator"]["address"], "t9")

    def test_a_plain_shell_in_the_epic_folder_may_restart(self):
        # Its folder makes it resolve as the orchestrator role, but it is not the orchestrator terminal.
        self.save_cfg()
        with StubEnv() as stub, mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "t20"}), \
                mock.patch("pathlib.Path.cwd", return_value=Path(self.epic["folder"])):
            self.spaces(stub, SPACE)
            stub_terminals(stub, [term("t5")], {"t5": coordinator_labels()})
            stub.respond("hc", ["get", "t20"], json.dumps({"address": "t20", "labels": {}}))
            stub.respond("hc", ["kill"], json.dumps({"ok": True}))
            stub.respond("hc", ["rm"], json.dumps({"ok": True}))
            stub.respond("hc", ["run"], json.dumps({"address": "t9", "first_message": {"sent": True}}))
            code, out = run_cli(["restart", self.epic["id"], "--json"])
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["killed"], ["t5"])

    def test_restart_refuses_a_closed_epic(self):
        cfg_path = Path(os.environ["HERDR_EPIC_CONFIG"])
        config.save({"roots": [{"name": "epics", "path": str(self.root)}], "language": "en"}, cfg_path)
        self.addCleanup(lambda: cfg_path.unlink(missing_ok=True))
        store.update(Path(self.epic["folder"]), lambda e: e.update(state="done"))
        with StubEnv() as stub:
            code, out = run_cli(["restart", self.epic["id"], "--json"])
            self.assertEqual(code, 1, out)
            self.assertIn("closed", json.loads(out)["error"])
            self.assertEqual([c for c in stub.calls("hc") if c[0] in ("kill", "rm", "run")], [])


class SpacelessTest(Base):
    def test_an_epic_without_a_space_matches_no_terminal(self):
        store.update(Path(self.epic["folder"]), lambda e: e.update(space=None))
        epic = store.load(Path(self.epic["folder"]))
        with StubEnv() as stub:
            stub.respond("hc", ["ls"], json.dumps({"terminals": [term("t4", template="assistant")]}))
            stub.respond("hc", ["get", "t4"], json.dumps({"address": "t4", "labels": {}}))
            self.assertIsNone(orchestrator.find(epic))
            self.assertEqual(orchestrator.space_terminals(epic), [])


class RulesHintTest(Base):
    def test_a_started_orchestrator_is_told_where_its_rules_are(self):
        with StubEnv() as stub:
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            stub.respond("hc", ["run"], json.dumps({"address": "t9", "first_message": {"sent": True}}))
            orchestrator.ensure(self.epic, "hello")
            first = [c for c in stub.calls("hc") if c[0] == "run"][0][-1]
            self.assertIn("../AGENTS.md", first)
            self.assertTrue(first.endswith("hello"))

    def test_an_unwritable_template_folder_is_reported_not_raised(self):
        Path(os.environ["HERDR_TASK_SETTINGS"]).write_text(json.dumps(
            {"everywhere": {"coordinator": {"agent": "claude", "perm": "auto"}}}), encoding="utf-8")
        self.addCleanup(lambda: Path(os.environ["HERDR_TASK_SETTINGS"]).unlink(missing_ok=True))
        with StubEnv() as stub, mock.patch.object(orchestrator, "one_off_template", side_effect=OSError("read-only")):
            self.spaces(stub, SPACE)
            stub_terminals(stub, [], {})
            r = orchestrator.ensure(self.epic, "hello")
            self.assertIn("read-only", r["error"])
            self.assertFalse(r["started"])


class LinkClaudeTest(unittest.TestCase):
    def test_non_utf8_or_directory_claude_md_is_kept_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CLAUDE.md").write_bytes(b"\xff\xfe not utf-8")
            self.assertEqual(orchestrator.link_claude(root), "kept")
            (root / "CLAUDE.md").unlink()
            (root / "CLAUDE.md").mkdir()
            self.assertEqual(orchestrator.link_claude(root), "kept")
