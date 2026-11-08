import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_epic import Fail, board, config, store
from tests.helpers import StubEnv, make_repo, run_cli

KEYS = {"ref", "repo", "title", "status", "needs", "line2", "progress", "epic", "terminal", "key", "log"}


def item(ref, folder, status="working", **extra):
    base = {"id": int(ref.split("-")[1]), "title": f"title {ref}", "status": status, "ref": ref,
            "folder": folder, "key": f"slug#{ref.split('-')[1]}", "live": [], "agents": []}
    return {**base, **extra}


def listing(*groups):
    return json.dumps([{"folder": f, "name": os.path.basename(f), "kind": "repo", "archived": 0, "tasks": ts}
                       for f, ts in groups])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name).resolve()
        self.root = tmp / "epics"
        self.root.mkdir()
        self.api = make_repo(tmp, "shop-api")
        self.web = make_repo(tmp, "shop-web")
        self.cfg = {"roots": [{"name": "shop", "path": str(self.root)}], "language": "en"}
        self.epic = store.create(self.root, "Gift cards", [
            {"name": "shop-api", "path": str(self.api), "base": None},
            {"name": "shop-web", "path": str(self.web), "base": None}])
        self.add_task("API-1", "shop-api")
        self.add_task("WEB-1", "shop-web")
        self.stub = StubEnv()
        self.addCleanup(self.stub.__exit__, None, None, None)
        self.stub.__enter__()

    def add_task(self, ref, repo):
        store.update(Path(self.epic["folder"]),
                     lambda e: e["tasks"].append({"ref": ref, "repo": repo, "title": f"t {ref}"}))

    def listing(self, *groups, archived=None):
        self.stub.respond("herdr-task", ["list", "--all", "--json"], listing(*groups))
        if archived is not None:
            self.stub.respond("herdr-task", ["list", "--all", "--archived", "--json"], listing(*archived))

    def epic_row(self, board_, ref):
        return next(r for r in board_["epics"][0]["tasks"] if r["ref"] == ref)


class BoardTest(Base):
    def test_rows_grouped_by_epic_and_repo(self):
        self.listing((str(self.api), [item("API-1", str(self.api))]), (str(self.web), [item("WEB-1", str(self.web))]))
        out = board.build(self.cfg)
        self.assertEqual([r["ref"] for r in out["epics"][0]["tasks"]], ["API-1", "WEB-1"])
        self.assertEqual({r["name"]: [x["ref"] for x in r["tasks"]] for r in out["repos"]},
                         {"shop-api": ["API-1"], "shop-web": ["WEB-1"]})
        self.assertEqual(out["epics"][0]["tasks"][0]["epic"], {"id": self.epic["id"], "title": "Gift cards"})
        self.assertEqual(out["unassigned"], [])
        self.assertEqual(out["roots"], [{"name": "shop", "path": str(self.root), "space": None}])

    def test_line2_is_why_when_needs_else_current_step(self):
        self.listing((str(self.api), [
            item("API-1", str(self.api), "needs", why="Reuse the form?", report={"text": "step"}),
            item("WEB-1", str(self.web), "working", report={"text": "Running tests", "done": 60, "left": 2520000})]))
        out = board.build(self.cfg)
        api = self.epic_row(out, "API-1")
        self.assertEqual((api["line2"], api["needs"], api["status"]), ("Reuse the form?", True, "needs"))
        web = self.epic_row(out, "WEB-1")
        self.assertEqual((web["line2"], web["progress"]), ("Running tests", {"done": 60, "left": "42m"}))

    def test_unassigned_tasks_grouped_by_repo(self):
        self.listing((str(self.api), [item("API-1", str(self.api)), item("API-9", str(self.api))]),
                     (str(self.web), [item("WEB-1", str(self.web)), item("WEB-9", str(self.web))]))
        out = board.build(self.cfg)
        self.assertEqual({g["repo"]: [r["ref"] for r in g["tasks"]] for g in out["unassigned"]},
                         {"shop-api": ["API-9"], "shop-web": ["WEB-9"]})
        self.assertIsNone(out["unassigned"][0]["tasks"][0]["epic"])
        self.assertEqual([r["ref"] for r in next(r for r in out["repos"] if r["name"] == "shop-api")["tasks"]],
                         ["API-1", "API-9"])

    def test_archived_task_found_via_archived_list(self):
        self.listing((str(self.web), [item("WEB-1", str(self.web))]),
                     archived=[(str(self.api), [item("API-1", str(self.api), "done")])])
        row = self.epic_row(board.build(self.cfg), "API-1")
        self.assertEqual(row["status"], "done")

    def test_missing_task_marked_missing(self):
        self.listing((str(self.web), [item("WEB-1", str(self.web))]), archived=[])
        row = self.epic_row(board.build(self.cfg), "API-1")
        self.assertEqual((row["status"], row["terminal"], row["title"]), ("missing", None, "t API-1"))

    def test_counts(self):
        live = [{"address": "t9", "status": "working", "role": "worker"}]
        self.listing((str(self.api), [item("API-1", str(self.api), "needs", why="?", live=live)]),
                     (str(self.web), [item("WEB-1", str(self.web), "working", live=live)]))
        self.assertEqual(board.build(self.cfg)["epics"][0]["counts"], {"agents": 2, "working": 1, "needs": 1})

    def test_row_carries_key_log_and_terminal(self):
        agents = [{"address": "t1", "agent": "claude", "log": "/l/a.jsonl"}]
        self.listing((str(self.api), [item("API-1", str(self.api), key="slug#1", agents=agents),
                                      ]), (str(self.web), [item("WEB-1", str(self.web), live=[
                                          {"address": "t5", "status": "done", "role": "worker"}])]))
        out = board.build(self.cfg)
        api, web = self.epic_row(out, "API-1"), self.epic_row(out, "WEB-1")
        self.assertEqual(api["key"], {"machine": None, "folder": str(self.api), "slug": "slug", "id": 1})
        self.assertEqual(api["log"], {"path": "/l/a.jsonl", "kind": "claude"})
        self.assertEqual((web["terminal"], web["log"]), ("t5", None))
        for row in out["epics"][0]["tasks"]:
            self.assertEqual(set(row), KEYS)

    def test_git_branch_and_behind(self):
        self.listing()
        repo = next(r for r in board.build(self.cfg)["repos"] if r["name"] == "shop-api")
        self.assertEqual((repo["branch"], repo["behind"]), ("main", None))  # no upstream
        # A clone with a real upstream, registered in the epic: one commit behind.
        clone = Path(self.tmp.name).resolve() / "api-clone"
        subprocess.run(["git", "clone", str(self.api), str(clone)], check=True, capture_output=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "d", "GIT_AUTHOR_EMAIL": "d@x", "GIT_COMMITTER_NAME": "d",
               "GIT_COMMITTER_EMAIL": "d@x"}
        (self.api / "f").write_text("x")
        subprocess.run(["git", "-C", str(self.api), "add", "f"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.api), "commit", "-m", "two"], check=True, capture_output=True, env=env)
        subprocess.run(["git", "-C", str(clone), "fetch"], check=True, capture_output=True)
        store.update(Path(self.epic["folder"]), lambda e: e["repos"].append(
            {"name": "api-clone", "path": str(clone), "base": None}))
        repo = next(r for r in board.build(self.cfg)["repos"] if r["name"] == "api-clone")
        self.assertEqual((repo["branch"], repo["behind"]), ("main", 1))

    def test_unassigned_lists_only_tasks_not_done(self):
        self.listing((str(self.api), [item("API-1", str(self.api)), item("API-8", str(self.api), "done"),
                                      item("API-9", str(self.api), "working")]))
        out = board.build(self.cfg)
        self.assertEqual([[r["ref"] for r in g["tasks"]] for g in out["unassigned"]], [["API-9"]])

    def test_survives_non_git_path_corrupt_epic_and_missing_herdr_task(self):
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        store.update(Path(self.epic["folder"]), lambda e: e["repos"].append(
            {"name": "plain", "path": str(plain), "base": None}))
        bad = self.root / "Broken"
        bad.mkdir()
        (bad / "epic.json").write_text("{nope", encoding="utf-8")
        out = board.build(self.cfg)  # stub has no herdr-task table: the call fails
        self.assertIn("error", out)
        self.assertEqual([e["title"] for e in out["epics"]], ["Gift cards"])
        # The task store could not be read: the tasks are unknown, not gone.
        rows = out["epics"][0]["tasks"]
        self.assertEqual([(r["ref"], r["status"], r["line2"]) for r in rows], [("API-1", "unknown", ""), ("WEB-1", "unknown", "")])
        plain_repo = next(r for r in out["repos"] if r["name"] == "plain")
        self.assertEqual((plain_repo["branch"], plain_repo["behind"]), (None, None))

    def test_matches_fixture_structure(self):
        self.listing((str(self.api), [item("API-1", str(self.api))]))
        out = board.build(self.cfg)
        fixture = json.loads((Path(__file__).parent / "fixtures" / "board.json").read_text(encoding="utf-8"))
        self.assertEqual(set(out) - {"error"}, set(fixture))
        self.assertEqual(set(out["epics"][0]), set(fixture["epics"][0]))
        self.assertEqual(set(out["repos"][0]), set(fixture["repos"][0]))
        self.assertEqual(set(out["roots"][0]), set(fixture["roots"][0]))
        self.assertEqual(set(out["epics"][0]["tasks"][0]), set(fixture["epics"][0]["tasks"][0]))
        self.assertEqual(set(out["epics"][0]["tasks"][0]), KEYS)
        self.assertEqual(set(out["repos"][0]["tasks"][0]), set(fixture["repos"][0]["tasks"][0]))
        self.assertEqual(set(out["unassigned"][0] if out["unassigned"] else {"repo": 0, "tasks": 0}),
                         set(fixture["unassigned"][0]))


class CommandTest(Base):
    def setUp(self):
        super().setUp()
        cfg_path = Path(self.tmp.name) / "config.json"
        config.save(self.cfg, cfg_path)
        patcher = mock.patch.dict(os.environ, {"HERDR_EPIC_CONFIG": str(cfg_path)})
        patcher.start()
        os.environ.pop("HC_TERMINAL_ID", None)
        self.addCleanup(patcher.stop)
        self.listing((str(self.api), [item("API-1", str(self.api))]), (str(self.web), [item("WEB-1", str(self.web))]))

    def test_list_json(self):
        code, out = run_cli(["list", "--json"])
        data = json.loads(out)["epics"][0]
        self.assertEqual((code, data["title"], data["state"], data["tasks"]), (0, "Gift cards", "open", 2))
        self.assertIn("counts", data)

    def test_board_json_is_one_object(self):
        code, out = run_cli(["board", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["epics"][0]["id"], self.epic["id"])

    def test_show_repairs_closed_space_then_lists_rows(self):
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
        self.stub.respond("herdr-app", ["do", "space.new"], json.dumps({"space": "sp-new"}))
        code, out = run_cli(["show", self.epic["id"], "--json"])
        data = json.loads(out)
        self.assertEqual((code, data["space"], len(data["tasks"])), (0, "sp-new", 2))
        self.assertEqual([r["name"] for r in data["repos"]], ["shop-api", "shop-web"])
        self.assertEqual(store.load(Path(self.epic["folder"]))["space"], "sp-new")

    def test_show_leaves_closed_epic_without_space(self):
        store.update(Path(self.epic["folder"]), lambda e: e.update(state="done"))
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
        code, out = run_cli(["show", self.epic["id"], "--json"])
        self.assertEqual((code, json.loads(out)["state"]), (0, "done"))
        self.assertEqual([c for c in self.stub.calls("herdr-app") if c[0] == "do"], [])
        self.assertEqual(store.load(Path(self.epic["folder"]))["space"], self.epic["space"])

    def test_close_requires_orchestrator_or_root(self):
        code, out = run_cli(["close", self.epic["id"], "--json"])
        self.assertEqual(code, 1)
        self.assertIn("runs in an epic's orchestrator", json.loads(out)["error"])
        self.assertEqual(store.load(Path(self.epic["folder"]))["state"], "open")
        old = os.getcwd()
        os.chdir(self.root)  # an epic root: allowed
        self.addCleanup(os.chdir, old)
        code, out = run_cli(["close", self.epic["id"], "--json"])
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["state"], "done")
        epic = store.load(Path(self.epic["folder"]))
        self.assertEqual(epic["state"], "done")
        self.assertTrue(epic["closed"])

    def test_root_show_creates_space_and_saves_it(self):
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
        self.stub.respond("herdr-app", ["do", "space.new"], json.dumps({"space": "sp-root"}))
        code, out = run_cli(["root", "show", "shop", "--json"])
        self.assertEqual((code, json.loads(out)["space"]), (0, "sp-root"))
        self.assertEqual(json.loads((self.root / "root.json").read_text())["space"], "sp-root")
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": [{"id": "sp-root"}]}))
        run_cli(["root", "show", "shop", "--json"])
        self.assertEqual(len([c for c in self.stub.calls("herdr-app") if c[:2] == ["do", "space.new"]]), 1)
        out = board.build(config.load())
        self.assertEqual(out["roots"][0]["space"], "sp-root")


if __name__ == "__main__":
    unittest.main()
