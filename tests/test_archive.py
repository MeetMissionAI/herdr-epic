"""`herdr-epic archive` and `unarchive`: the mark, who may set it, what blocks it, and what it hides."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from herdr_epic import board, config, store
from tests.helpers import run_cli
from tests.test_board import Base, item

SPACE = "sp-gift"


class ArchiveTest(Base):
    def setUp(self):
        super().setUp()
        cfg_path = Path(self.tmp.name) / "config.json"
        config.save(self.cfg, cfg_path)
        patcher = mock.patch.dict(os.environ, {"HERDR_EPIC_CONFIG": str(cfg_path)})
        patcher.start()
        os.environ.pop("HC_TERMINAL_ID", None)
        self.addCleanup(patcher.stop)
        self.folder = Path(self.epic["folder"])
        store.update(self.folder, lambda e: e.update(space=SPACE))
        self.statuses("todo", "done")

    def statuses(self, api, web):
        self.listing((str(self.api), [item("API-1", str(self.api), api)]),
                     (str(self.web), [item("WEB-1", str(self.web), web)]))

    def cli_json(self, argv, code=0):
        got, out = run_cli([*argv, "--json"])
        self.assertEqual(got, code, out)
        return json.loads(out)

    def saved(self):
        return store.load(self.folder)

    def test_round_trip_keeps_state_and_tasks(self):
        before = self.saved()
        out = self.cli_json(["archive", self.epic["id"]])
        self.assertEqual((out["id"], out["title"], out["changed"]), (self.epic["id"], "Gift cards", True))
        self.assertTrue(out["archived"])
        self.assertEqual(self.saved()["archived"], out["archived"])
        self.assertEqual((self.saved()["state"], self.saved()["tasks"], self.saved()["space"]),
                         (before["state"], before["tasks"], before["space"]))
        out = self.cli_json(["unarchive", self.epic["id"]])
        self.assertEqual((out["archived"], out["changed"]), (None, True))
        self.assertEqual({**self.saved(), "archived": None}, {**before, "archived": None})
        self.assertIsNone(self.saved()["archived"])
        # Archiving never touches tasks or spaces.
        self.assertEqual([c for c in self.stub.calls("herdr-task") if c[0] != "list"], [])
        self.assertEqual([c for c in self.stub.calls("herdr-app") if c[0] == "do"], [])

    def test_done_epic_can_be_archived_and_stays_done(self):
        store.update(self.folder, lambda e: e.update(state="done"))
        self.cli_json(["archive", self.epic["id"]])
        self.cli_json(["unarchive", self.epic["id"]])
        self.assertEqual(self.saved()["state"], "done")

    def test_again_changes_nothing_and_exits_0(self):
        stamp = self.cli_json(["archive", self.epic["id"]])["archived"]
        out = self.cli_json(["archive", self.epic["id"]])
        self.assertEqual((out["archived"], out["changed"]), (stamp, False))
        code, text = run_cli(["archive", self.epic["id"]])
        self.assertEqual(code, 0)
        self.assertIn("already archived", text)
        self.cli_json(["unarchive", self.epic["id"]])
        out = self.cli_json(["unarchive", self.epic["id"]])
        self.assertEqual((out["archived"], out["changed"]), (None, False))

    def test_running_tasks_block_and_are_named(self):
        for status in ("queued", "working", "needs", "ready", "merging"):
            with self.subTest(status=status):
                self.statuses(status, "todo")
                out = self.cli_json(["archive", self.epic["id"]], code=1)
                self.assertEqual(out["blocking"], [{"ref": "API-1", "title": "title API-1", "status": status}])
                self.assertIn("API-1", out["error"])
                self.assertIn(status, out["error"])
                self.assertIn("Finish, accept or stop them first", out["error"])
                self.assertIsNone(self.saved().get("archived"))
        self.assertEqual([c for c in self.stub.calls("herdr-task") if c[0] != "list"], [])

    def test_unreadable_task_store_blocks(self):
        self.stub.respond("herdr-task", ["list", "--all", "--json"], "boom", code=1)
        out = self.cli_json(["archive", self.epic["id"]], code=1)
        self.assertEqual({b["status"] for b in out["blocking"]}, {"unknown"})
        self.assertEqual(len(out["blocking"]), 2)

    def test_todo_done_and_missing_tasks_do_not_block(self):
        self.listing((str(self.api), [item("API-1", str(self.api), "todo")]), (str(self.web), []), archived=[])
        self.assertEqual(self.epic_row(board.build(self.cfg), "WEB-1")["status"], "missing")
        self.cli_json(["archive", self.epic["id"]])

    def test_text_refusal_lists_tasks_on_stderr(self):
        self.statuses("working", "needs")
        with mock.patch("sys.stderr") as err:
            code, out = run_cli(["archive", self.epic["id"]])
        written = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("API-1  working  title API-1", written)
        self.assertIn("WEB-1  needs  title WEB-1", written)

    def test_list_hides_archived_unless_asked(self):
        other = store.create(self.root, "Coupons", [])
        self.cli_json(["archive", self.epic["id"]])
        self.assertEqual([e["id"] for e in self.cli_json(["list"])["epics"]], [other["id"]])
        shown = self.cli_json(["list", "--archived"])["epics"]
        self.assertEqual({e["id"]: bool(e["archived"]) for e in shown}, {self.epic["id"]: True, other["id"]: False})
        code, text = run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertNotIn("Gift cards", text)
        self.assertIn("1 archived epic(s) not shown", text)
        code, text = run_cli(["list", "--archived"])
        line = next(line for line in text.splitlines() if "Gift cards" in line)
        self.assertIn("archived", line)
        self.assertNotIn("archived", next(line for line in text.splitlines() if "Coupons" in line))

    def test_board_keeps_archived_epics_and_marks_them(self):
        self.assertIsNone(self.cli_json(["board"])["epics"][0]["archived"])
        stamp = self.cli_json(["archive", self.epic["id"]])["archived"]
        epics = self.cli_json(["board"])["epics"]
        self.assertEqual([(e["id"], e["archived"]) for e in epics], [(self.epic["id"], stamp)])

    def test_old_epic_json_without_the_field_reads_as_not_archived(self):
        store.update(self.folder, lambda e: e.pop("archived", None))
        self.assertIsNone(self.cli_json(["board"])["epics"][0]["archived"])
        self.assertEqual(len(self.cli_json(["list"])["epics"]), 1)

    def test_show_still_works(self):
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": [{"id": SPACE}]}))
        stamp = self.cli_json(["archive", self.epic["id"]])["archived"]
        out = self.cli_json(["show", self.epic["id"]])
        self.assertEqual((out["archived"], len(out["tasks"])), (stamp, 2))

    def test_request_and_task_add_refused(self):
        self.cli_json(["archive", self.epic["id"]])
        out = self.cli_json(["request", "--epic", self.epic["id"], "--repo", "shop-api", "--context", "x"], code=1)
        self.assertIn("archived", out["error"])
        self.assertIn(f"herdr-epic unarchive {self.epic['id']}", out["error"])
        self.assertEqual(self.saved()["requests"], [])
        with mock.patch("pathlib.Path.cwd", return_value=self.folder):  # the epic's orchestrator
            out = self.cli_json(["task", "add", "--repo", "shop-api", "--title", "T", "--prompt", "p"], code=1)
        self.assertIn(f"herdr-epic unarchive {self.epic['id']}", out["error"])
        self.assertEqual([c for c in self.stub.calls("herdr-task") if c[0] != "list"], [])

    def test_root_and_orchestrator_may_archive(self):
        with mock.patch("pathlib.Path.cwd", return_value=self.root):
            self.cli_json(["archive", self.epic["id"]])
        with mock.patch("pathlib.Path.cwd", return_value=self.folder):
            self.cli_json(["unarchive"])
        self.assertIsNone(self.saved()["archived"])

    def test_worker_refused(self):
        self.stub.respond("hc", ["get", "t1"], json.dumps(
            {"address": "t1", "labels": {"herdr.task": "shop-api#1", "herdr.task.space": SPACE}}))
        with mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "t1"}):
            for command in ("archive", "unarchive"):
                out = self.cli_json([command, self.epic["id"]], code=1)
                self.assertIn("herdr-epic request", out["error"])
        self.assertIsNone(self.saved().get("archived"))


if __name__ == "__main__":
    unittest.main()
