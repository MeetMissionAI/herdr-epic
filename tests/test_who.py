import json
import tempfile
import unittest
from pathlib import Path

from herdr_epic import Fail
from herdr_epic import herdr, store, who
from tests.helpers import StubEnv, make_repo


class WhoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "epics"
        self.root.mkdir()
        self.cfg = {"roots": [{"name": "epics", "path": str(self.root)}], "language": "en"}
        repo = make_repo(base, "shop-api")
        self.epic = store.create(self.root, "Gift cards", [{"name": "shop-api", "path": str(repo)}])
        store.update(Path(self.epic["folder"]), lambda e: e.update(space="sp1"))
        self.epic = store.load(Path(self.epic["folder"]))

    def tearDown(self):
        self._tmp.cleanup()

    def test_worker_by_task_space_label(self):
        term = {"labels": {"herdr.task.space": "sp1", "herdr.task": "shop-api#5"}}
        r = who.resolve(self.cfg, term, Path("/elsewhere"))
        self.assertEqual((r["role"], r["epic"]["id"]), ("worker", self.epic["id"]))
        self.assertEqual(r["task_key"], "shop-api#5")

    def test_worker_of_a_former_space_still_resolves_after_the_space_is_recreated(self):
        from herdr_epic import orchestrator
        with StubEnv() as stub:
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"], json.dumps({"space": "sp2"}))
            orchestrator.ensure_space(self.epic)
            stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
            stub.respond("herdr-app", ["do", "space.new"], json.dumps({"space": "sp3"}))
            orchestrator.ensure_space(store.load(Path(self.epic["folder"])))
        saved = store.load(Path(self.epic["folder"]))
        self.assertEqual((saved["space"], saved["former_spaces"]), ("sp3", ["sp1", "sp2"]))
        for space in ("sp1", "sp2", "sp3"):
            term = {"labels": {"herdr.task.space": space, "herdr.task": "shop-api#5"}}
            r = who.resolve(self.cfg, term, Path("/elsewhere"))
            self.assertEqual((r["role"], r["epic"]["id"]), ("worker", self.epic["id"]), space)
        self.assertEqual(store.by_space(self.cfg, "sp1")["id"], self.epic["id"])
        self.assertIsNone(store.by_space(self.cfg, "sp-other"))

    def test_orchestrator_by_coordinator_space(self):
        term = {"labels": {"herdr.role": "coordinator", "herdr.coordinator.space": "sp1"}}
        r = who.resolve(self.cfg, term, Path("/elsewhere"))
        self.assertEqual((r["role"], r["epic"]["id"]), ("orchestrator", self.epic["id"]))
        self.assertIsNone(r["task_key"])

    def test_orchestrator_by_herdr_space_in_terminal_mode(self):
        term = {"labels": {"herdr.role": "coordinator", "herdr.space": "sp1"}}
        self.assertEqual(who.resolve(self.cfg, term, Path("/elsewhere"))["role"], "orchestrator")

    def test_coordinator_of_unknown_space_is_not_orchestrator(self):
        term = {"labels": {"herdr.role": "coordinator", "herdr.coordinator.space": "other"}}
        self.assertEqual(who.resolve(self.cfg, term, Path("/elsewhere"))["role"], "none")

    def test_orchestrator_by_cwd_without_labels(self):
        sub = Path(self.epic["folder"]) / "notes"
        for term in (None, {"labels": {}}, {"labels": None}):
            r = who.resolve(self.cfg, term, sub)
            self.assertEqual((r["role"], r["epic"]["id"]), ("orchestrator", self.epic["id"]))

    def test_worker_label_wins_over_cwd(self):
        term = {"labels": {"herdr.task.space": "sp1"}}
        r = who.resolve(self.cfg, term, Path(self.epic["folder"]))
        self.assertEqual(r["role"], "worker")

    def test_root_by_cwd(self):
        r = who.resolve(self.cfg, None, self.root)
        self.assertEqual((r["role"], r["root"]["name"], r["epic"]), ("root", "epics", None))

    def test_none_outside(self):
        r = who.resolve(self.cfg, None, Path("/tmp"))
        self.assertEqual((r["role"], r["epic"], r["root"]), ("none", None, None))


class HerdrTest(unittest.TestCase):
    def test_app_do_passes_json_values(self):
        with StubEnv() as stub:
            stub.respond("herdr-app", ["do", "space.new"], '{"space": "s9"}')
            out = herdr.app_do("space.new", name="礼品卡", folder="/x y", argv=["pi"], opts={"a": 1})
            self.assertEqual(out, {"space": "s9"})
            self.assertEqual(
                stub.calls("herdr-app")[0],
                ["do", "space.new", "--name", "礼品卡", "--folder", "/x y", "--argv", '["pi"]', "--opts", '{"a": 1}'],
            )

    def test_app_do_error_object_fails(self):
        with StubEnv() as stub:
            stub.respond("herdr-app", ["do"], '{"error": "no such space"}', code=1)
            with self.assertRaises(Fail) as ctx:
                herdr.app_do("space.rm", id="x")
            self.assertIn("no such space", str(ctx.exception))

    def test_app_do_error_object_with_zero_exit_fails(self):
        with StubEnv() as stub:
            stub.respond("herdr-app", ["do"], '{"error": "bad"}')
            with self.assertRaises(Fail):
                herdr.app_do("x")

    def test_app_ls_parses_json_without_flag(self):
        with StubEnv() as stub:
            stub.respond("herdr-app", ["ls"], '{"spaces": []}')
            self.assertEqual(herdr.app("ls"), {"spaces": []})
            self.assertEqual(stub.calls("herdr-app")[0], ["ls"])

    def test_hc_appends_json_and_parses(self):
        with StubEnv() as stub:
            stub.respond("hc", ["ls"], '[{"address": "t1"}]')
            self.assertEqual(herdr.hc("ls", "--all"), [{"address": "t1"}])
            self.assertEqual(stub.calls("hc")[0], ["ls", "--all", "--json"])

    def test_task_nonzero_exit_uses_stderr(self):
        with StubEnv() as stub:
            stub.respond("herdr-task", ["list"], "", code=2)
            with self.assertRaises(Fail):
                herdr.task("list", "--all")
            self.assertEqual(stub.calls("herdr-task")[0], ["list", "--all", "--json"])

    def test_invalid_json_fails(self):
        with StubEnv() as stub:
            stub.respond("hc", ["get"], "not json")
            with self.assertRaises(Fail):
                herdr.terminal("t1")

    def test_terminal_and_caller(self):
        import os
        with StubEnv() as stub:
            stub.respond("hc", ["get", "t7"], '{"address": "t7", "labels": {}}')
            self.assertEqual(herdr.terminal("t7")["address"], "t7")
            old = os.environ.pop("HC_TERMINAL_ID", None)
            try:
                self.assertIsNone(herdr.caller())
                os.environ["HC_TERMINAL_ID"] = "t7"
                self.assertEqual(herdr.caller()["address"], "t7")
            finally:
                os.environ.pop("HC_TERMINAL_ID", None)
                if old is not None:
                    os.environ["HC_TERMINAL_ID"] = old

    def test_missing_binary_fails(self):
        import os
        old = os.environ["PATH"]
        os.environ["PATH"] = "/nonexistent"
        try:
            with self.assertRaises(Fail):
                herdr.hc("ls")
        finally:
            os.environ["PATH"] = old


if __name__ == "__main__":
    unittest.main()
