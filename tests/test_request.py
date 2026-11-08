import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_epic import config, store
from tests.helpers import StubEnv, make_repo, run_cli, temp_home

SPACE = "sp-epic"
BIN = Path(__file__).resolve().parents[1] / "bin" / "herdr-epic"
WORKER = {"herdr.task": "shop-api#1", "herdr.task.space": SPACE, "herdr.agent": "claude"}
ORCH = {"herdr.role": "coordinator", "herdr.coordinator.space": SPACE}


def calls_starting(stub, binary, *prefix):
    return [c for c in stub.calls(binary) if c[:len(prefix)] == list(prefix)]


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
        (base / "app").mkdir()
        env = mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "t1", "HERDR_APP_HOME": str(base / "app")})
        env.start()
        self.addCleanup(env.stop)

        self.root = base / "epics"
        self.root.mkdir()
        config.save({"roots": [{"name": "epics", "path": str(self.root)}], "language": "en"})
        api = make_repo(base, "shop-api")
        epic = store.create(self.root, "Gift cards", [{"name": "shop-api", "path": str(api), "base": None}])
        self.folder = Path(epic["folder"])
        self.epic = store.update(self.folder, lambda e: e.update(space=SPACE))
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": [{"id": SPACE}]}))
        self.stub.respond("herdr-task", ["list", "--all"], json.dumps([{"folder": str(api), "tasks": [
            {"key": "shop-api#1", "ref": "SA-1", "id": 1},
            {"key": "shop-api#2", "ref": "SA-2", "id": 2}]}]))

    def caller(self, labels, orchestrator=None):
        """t1 is the caller. `orchestrator` is the address of a live Assistant in the epic's space."""
        self.stub.respond("hc", ["get", "t1"], json.dumps(
            {"address": "t1", "state": "live", "status": {"app": "claude-code", "state": "working"}, "labels": labels}))
        rows = []
        if orchestrator:
            rows.append({"address": orchestrator, "state": "live", "agent": "claude-code", "template": "assistant"})
            self.stub.respond("hc", ["get", orchestrator], json.dumps({"address": orchestrator, "labels": ORCH}))
        self.stub.respond("hc", ["ls"], json.dumps({"terminals": rows}))

    def tell_ok(self):
        self.stub.respond("herdr-task", ["tell-assistant"], json.dumps(
            {"to": "t4", "space": "Gift cards", "can_reply": True, "from": "t1", "held": False}))

    def cli_json(self, argv, code=0):
        got, out = run_cli([*argv, "--json"])
        self.assertEqual(got, code, out)
        return json.loads(out)

    def requests(self):
        return store.load(self.folder)["requests"]


class RequestTest(Fixture):
    def test_request_from_worker_delivers_to_running_orchestrator(self):
        self.caller(WORKER, orchestrator="t4")
        self.tell_ok()
        out = self.cli_json(["request", "--repo", "shop-ios", "--agent", "claude", "--context", "Add a scan screen"])
        self.assertEqual(out, {"request": "r-1", "state": "delivered", "orchestrator": "t4", "saved": True})
        tell = calls_starting(self.stub, "herdr-task", "tell-assistant")
        self.assertEqual(len(tell), 1, tell)
        self.assertEqual(tell[0][1], SPACE)
        self.assertEqual(tell[0][2], '<herdr-epic-request id="r-1" from-task="SA-1" repo="shop-ios" agent="claude">'
                                     'Add a scan screen</herdr-epic-request>')
        self.assertEqual(calls_starting(self.stub, "hc", "run"), [])
        req = self.requests()[-1]
        self.assertEqual({k: req[k] for k in ("id", "from_terminal", "from_task", "repo", "agent", "context", "state")},
                         {"id": "r-1", "from_terminal": "t1", "from_task": "SA-1", "repo": "shop-ios",
                          "agent": "claude", "context": "Add a scan screen", "state": "delivered"})
        self.assertTrue(req["at"])
        self.assertEqual(req["orchestrator"], "t4")

    def test_request_starts_orchestrator_with_request_as_first_message(self):
        self.caller(WORKER)
        self.stub.respond("hc", ["run"], json.dumps(
            {"address": "t9", "first_message": {"sent": True, "started": True, "turn": 0}, "template": "assistant"}))
        out = self.cli_json(["request", "--repo", "shop-api", "--context", "Another endpoint"])
        self.assertEqual((out["request"], out["state"], out["orchestrator"]), ("r-1", "delivered", "t9"))
        run = calls_starting(self.stub, "hc", "run")
        self.assertEqual(len(run), 1)
        self.assertTrue(run[0][-1].endswith('\n\n<herdr-epic-request id="r-1" from-task="SA-1" repo="shop-api">'
                                            'Another endpoint</herdr-epic-request>'), run[0][-1])
        self.assertEqual(calls_starting(self.stub, "herdr-task", "tell-assistant"), [])
        self.assertEqual(self.requests()[-1]["state"], "delivered")
        self.assertIsNone(self.requests()[-1]["agent"])

    def test_request_started_orchestrator_without_first_message_stays_queued(self):
        self.caller(WORKER)
        self.stub.respond("hc", ["run"], json.dumps(
            {"address": "t9", "first_message": {"sent": False, "started": False, "turn": 0}, "template": "assistant"}))
        code, out = run_cli(["request", "--repo", "shop-ios", "--context", "x", "--json"])
        self.assertEqual(code, 1, out)
        data = json.loads(out)
        self.assertEqual((data["request"], data["state"], data["saved"], data["orchestrator"]),
                         ("r-1", "queued", True, "t9"))
        self.assertIn("was saved", data["error"])
        self.assertIn("did not receive the message yet", data["error"])
        self.assertIn("herdr-epic pending", data["error"])
        self.assertEqual(calls_starting(self.stub, "herdr-task", "tell-assistant"), [])
        req = self.requests()[-1]
        self.assertEqual(req["state"], "queued")
        self.assertIn("did not receive the message yet", req["error"])

    def test_request_failure_keeps_queued_and_exits_1(self):
        self.caller(WORKER)
        self.stub.respond("hc", ["run"], json.dumps({"error": "no pty"}), code=1)
        code, out = run_cli(["request", "--repo", "shop-ios", "--context", "x", "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual((data["request"], data["state"], data["saved"]), ("r-1", "queued", True))
        self.assertIn("no pty", data["error"])
        self.assertIn("r-1", data["error"])
        self.assertIn("saved", data["error"])
        req = store.load(self.folder)["requests"][-1]
        self.assertEqual(req["state"], "queued")
        self.assertIn("no pty", req["error"])

    def test_request_failure_text_says_saved(self):
        self.caller(WORKER, orchestrator="t4")
        self.stub.respond("herdr-task", ["tell-assistant"], json.dumps({"error": "couldn't deliver to t4"}), code=1)
        err = mock.patch("sys.stderr", new_callable=__import__("io").StringIO)
        stderr = err.start()
        self.addCleanup(err.stop)
        code, out = run_cli(["request", "--repo", "shop-ios", "--context", "x"])
        self.assertEqual(code, 1)
        self.assertIn("r-1", stderr.getvalue())
        self.assertIn("was saved", stderr.getvalue())
        self.assertIn("couldn't deliver", stderr.getvalue())
        self.assertEqual(self.requests()[-1]["state"], "queued")

    def test_request_on_closed_epic_is_refused_before_saving(self):
        store.update(self.folder, lambda e: e.update(state="done"))
        self.caller(WORKER)
        self.stub.respond("herdr-app", ["ls"], json.dumps({"spaces": []}))
        out = self.cli_json(["request", "--repo", "shop-ios", "--context", "x"], code=1)
        self.assertIs(out["saved"], False)
        self.assertIn("NOT saved", out["error"])
        self.assertIn("this epic is closed; ask the root assistant to start a new epic", out["error"])
        self.assertEqual(self.requests(), [])
        self.assertEqual(calls_starting(self.stub, "hc", "run"), [])
        self.assertEqual(calls_starting(self.stub, "herdr-app", "do"), [])
        self.assertEqual(calls_starting(self.stub, "herdr-task", "tell-assistant"), [])

    def test_request_without_epic_context_fails_with_hint(self):
        self.caller({})
        out = self.cli_json(["request", "--repo", "shop-ios", "--context", "x"], code=1)
        self.assertIn("herdr-epic list", out["error"])
        self.assertIn("--epic", out["error"])
        self.assertIn("NOT saved", out["error"])
        self.assertIs(out["saved"], False)
        self.assertEqual(self.requests(), [])

    def test_request_with_epic_flag_from_plain_terminal(self):
        self.caller({}, orchestrator="t4")
        self.tell_ok()
        out = self.cli_json(["request", "--epic", "Gift cards", "--repo", "shop-ios", "--context", "x"])
        self.assertEqual(out["state"], "delivered")
        self.assertIsNone(self.requests()[-1]["from_task"])
        self.assertNotIn("from-task", calls_starting(self.stub, "herdr-task", "tell-assistant")[0][2])

    def test_request_unknown_task_key_gives_null_from_task(self):
        self.caller({**WORKER, "herdr.task": "shop-api#99"}, orchestrator="t4")
        self.tell_ok()
        self.cli_json(["request", "--repo", "shop-ios", "--context", "x"])
        self.assertIsNone(self.requests()[-1]["from_task"])

    def test_request_needs_exactly_one_context(self):
        self.caller(WORKER, orchestrator="t4")
        out = self.cli_json(["request", "--repo", "shop-ios"], code=1)
        self.assertIn("--context", out["error"])
        self.assertIs(out["saved"], False)
        self.assertEqual(self.requests(), [])

    def test_request_context_file(self):
        self.caller(WORKER, orchestrator="t4")
        self.tell_ok()
        path = Path(self.tmp.name) / "ctx.md"
        path.write_text("Long context\nline two\n", encoding="utf-8")
        self.cli_json(["request", "--repo", "shop-ios", "--context-file", str(path)])
        self.assertEqual(self.requests()[-1]["context"], "Long context\nline two\n")

    def test_request_context_file_not_utf8_is_not_saved(self):
        self.caller(WORKER, orchestrator="t4")
        path = Path(self.tmp.name) / "ctx.md"
        path.write_bytes("caf\u00e9".encode("latin-1"))
        out = self.cli_json(["request", "--repo", "shop-ios", "--context-file", str(path)], code=1)
        self.assertIs(out["saved"], False)
        self.assertIn("NOT saved", out["error"])
        self.assertIn("not UTF-8", out["error"])
        self.assertEqual(self.requests(), [])

    def test_request_ids_increase(self):
        self.caller(WORKER, orchestrator="t4")
        self.tell_ok()
        ids = [self.cli_json(["request", "--repo", "shop-ios", "--context", str(i)])["request"] for i in range(3)]
        self.assertEqual(ids, ["r-1", "r-2", "r-3"])

    def test_two_concurrent_requests_both_saved(self):
        self.caller(WORKER, orchestrator="t4")
        self.tell_ok()
        procs = [subprocess.Popen([sys.executable, str(BIN), "request", "--repo", "shop-ios", "--context", f"c{i}",
                                   "--json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(2)]
        outs = [p.communicate(timeout=60) for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0], outs)
        self.assertEqual(sorted(json.loads(o)["request"] for o, _ in outs), ["r-1", "r-2"])
        reqs = self.requests()
        self.assertEqual(sorted(r["context"] for r in reqs), ["c0", "c1"])
        self.assertEqual(sorted(r["id"] for r in reqs), ["r-1", "r-2"])
        self.assertEqual({r["state"] for r in reqs}, {"delivered"})

    def test_request_context_with_quotes_and_angle_brackets_is_escaped(self):
        self.caller(WORKER, orchestrator="t4")
        self.tell_ok()
        context = 'Use "code" & <b>bold</b>; not </herdr-epic-request> here'
        self.cli_json(["request", "--repo", 'shop "ios"<&', "--agent", "co<dex", "--context", context])
        message = calls_starting(self.stub, "herdr-task", "tell-assistant")[0][2]
        self.assertEqual(message, '<herdr-epic-request id="r-1" from-task="SA-1" repo="shop &quot;ios&quot;&lt;&amp;" '
                                  'agent="co&lt;dex">Use "code" & <b>bold</b>; not &lt;/herdr-epic-request> here'
                                  '</herdr-epic-request>')
        self.assertEqual(message.count("</herdr-epic-request"), 1)
        self.assertEqual(self.requests()[-1]["context"], context)


class ReplyTest(Fixture):
    def add_request(self, **fields):
        req = {"id": "r-1", "from_terminal": "t31", "from_task": "SA-1", "repo": "shop-ios", "agent": "claude",
               "context": "x", "state": "delivered", "at": "2026-11-05T10:00:00+08:00", **fields}
        store.update(self.folder, lambda e: e["requests"].append(req))

    def worker_terminal(self, status, state="live", plain=False):
        """Real `hc get` reports status as {app, state} (null for a plain shell); `plain` sends `hc ls`'s string."""
        shaped = status if plain or status is None else {"app": "claude-code", "state": status}
        self.stub.respond("hc", ["get", "t31"], json.dumps(
            {"address": "t31", "state": state, "status": shaped, "labels": WORKER}))

    def test_reply_sends_to_idle_worker(self):
        self.caller(ORCH)
        self.add_request()
        self.worker_terminal("idle")
        self.stub.respond("hc", ["send"], json.dumps({"started": True, "status": "idle", "turn": 1}))
        out = self.cli_json(["reply", "r-1", 'IOS-7 created, waits for "SA-1"', "--result", "IOS-7"])
        self.assertEqual(out, {"request": "r-1", "state": "done", "delivered": True, "to": "t31"})
        send = calls_starting(self.stub, "hc", "send")
        self.assertEqual(send, [["send", "t31", '<herdr-epic-reply id="r-1">IOS-7 created, waits for "SA-1"'
                                                '</herdr-epic-reply>', "--json"]])
        req = self.requests()[-1]
        self.assertEqual((req["state"], req["result"], req["reply"]), ("done", "IOS-7", 'IOS-7 created, waits for "SA-1"'))

    def test_reply_to_working_and_done_worker_is_sent(self):
        self.caller(ORCH)
        self.add_request()
        for status in ("working", "done"):
            self.worker_terminal(status)
            self.stub.respond("hc", ["send"], json.dumps({"started": True, "status": status, "turn": 0}))
            self.assertTrue(self.cli_json(["reply", "r-1", "ok"])["delivered"])

    def test_reply_reads_status_object_and_plain_string(self):
        self.caller(ORCH)
        self.add_request()
        for plain in (False, True):
            self.worker_terminal("working", plain=plain)
            self.stub.respond("hc", ["send"], json.dumps({"started": True, "status": "working", "turn": 0}))
            out = self.cli_json(["reply", "r-1", "ok"])
            self.assertIs(out["delivered"], True, (plain, out))
            self.assertEqual(self.requests()[-1]["state"], "done")
        self.assertEqual(len(calls_starting(self.stub, "hc", "send")), 2)

    def test_reply_to_plain_shell_is_undelivered(self):
        self.caller(ORCH)
        self.add_request()
        self.stub.respond("hc", ["get", "t31"], json.dumps({"address": "t31", "state": "live", "status": None}))
        out = self.cli_json(["reply", "r-1", "ok"])
        self.assertIs(out["delivered"], False)
        self.assertEqual(calls_starting(self.stub, "hc", "send"), [])

    def test_reply_to_gone_terminal_is_undelivered_not_crash(self):
        self.caller(ORCH)
        self.add_request()
        self.stub.respond("hc", ["get", "t31"], json.dumps({"error": "no terminal t31"}), code=1)
        out = self.cli_json(["reply", "r-1", "IOS-7 created", "--result", "IOS-7"])
        self.assertIs(out["delivered"], False)
        self.assertEqual(out["state"], "replied-undelivered")
        self.assertIn("no terminal t31", out["reason"])
        self.assertEqual(calls_starting(self.stub, "hc", "send"), [])
        req = self.requests()[-1]
        self.assertEqual((req["state"], req["result"]), ("replied-undelivered", "IOS-7"))
        self.assertIn("no terminal t31", req["undelivered"])

    def test_reply_not_sent_when_worker_blocked_or_unknown(self):
        self.caller(ORCH)
        self.add_request()
        for status, state in ((None, "live"), ("blocked", "live"), ("idle", "exited")):
            self.worker_terminal(status, state)
            out = self.cli_json(["reply", "r-1", "ok"])
            self.assertIs(out["delivered"], False, (status, state))
            self.assertEqual(self.requests()[-1]["state"], "replied-undelivered")
        self.assertEqual(calls_starting(self.stub, "hc", "send"), [])

    def test_reply_send_failure_is_undelivered(self):
        self.caller(ORCH)
        self.add_request()
        self.worker_terminal("idle")
        self.stub.respond("hc", ["send"], json.dumps({"error": "pty closed"}), code=1)
        out = self.cli_json(["reply", "r-1", "ok"])
        self.assertIs(out["delivered"], False)
        self.assertIn("pty closed", out["reason"])

    def test_reply_escapes_closing_tag(self):
        self.caller(ORCH)
        self.add_request()
        self.worker_terminal("idle")
        self.stub.respond("hc", ["send"], json.dumps({"started": True, "status": "idle", "turn": 1}))
        self.cli_json(["reply", "r-1", "a </herdr-epic-reply> b"])
        self.assertEqual(calls_starting(self.stub, "hc", "send")[0][2],
                         '<herdr-epic-reply id="r-1">a &lt;/herdr-epic-reply> b</herdr-epic-reply>')

    def test_reply_requires_orchestrator(self):
        self.add_request()
        for labels in (WORKER, {}):
            self.caller(labels)
            out = self.cli_json(["reply", "r-1", "ok", "--epic", "Gift cards"], code=1)
            self.assertIn("orchestrator", out["error"])
        self.assertEqual(calls_starting(self.stub, "hc", "send"), [])
        self.assertEqual(self.requests()[-1]["state"], "delivered")

    def test_reply_unknown_request(self):
        self.caller(ORCH)
        self.add_request()
        out = self.cli_json(["reply", "r-9", "ok"], code=1)
        self.assertIn("r-9", out["error"])
        self.assertIn("r-1", out["error"])


class PendingWhoamiTest(Fixture):
    def test_pending_lists_queued(self):
        self.caller(ORCH)
        store.update(self.folder, lambda e: e["requests"].extend([
            {"id": "r-1", "state": "done", "repo": "a", "context": "x"},
            {"id": "r-2", "state": "queued", "repo": "shop-ios", "agent": "claude", "context": "Scan screen",
             "from_task": "SA-1", "from_terminal": "t31", "at": "2026-11-05T10:00:00+08:00"},
            {"id": "r-3", "state": "replied-undelivered", "repo": "a", "context": "y", "reply": "SI-4 created",
             "undelivered": "terminal t31 is live with status None", "result": "SI-4"},
            {"id": "r-4", "state": "delivered", "repo": "a", "context": "z"}]))
        out = self.cli_json(["pending"])
        self.assertEqual(out["epic"], self.epic["id"])
        self.assertEqual([(r["id"], r["state"]) for r in out["requests"]],
                         [("r-2", "queued"), ("r-3", "replied-undelivered")])
        self.assertEqual(out["requests"][0]["context"], "Scan screen")
        self.assertEqual((out["requests"][1]["reply"], out["requests"][1]["undelivered"]),
                         ("SI-4 created", "terminal t31 is live with status None"))
        code, text = run_cli(["pending"])
        self.assertEqual(code, 0)
        for part in ("r-2", "queued", "Scan screen", "r-3", "replied-undelivered", "SI-4 created",
                     "terminal t31 is live with status None"):
            self.assertIn(part, text)
        self.assertNotIn("r-1", text)
        self.assertNotIn("r-4", text)

    def test_pending_with_epic_flag_and_empty(self):
        self.caller({})
        out = self.cli_json(["pending", "--epic", self.epic["id"]])
        self.assertEqual(out["requests"], [])

    def test_whoami_worker(self):
        self.caller(WORKER)
        out = self.cli_json(["whoami"])
        self.assertEqual(out["role"], "worker")
        self.assertEqual(out["epic"], {"id": self.epic["id"], "title": "Gift cards", "folder": str(self.folder),
                                       "repos": self.epic["repos"]})
        self.assertEqual(out["read"], [str(self.folder / n) for n in ("epic.md", "decisions.md", "handoff.md")])
        self.assertIn("herdr-epic request --repo", out["request_example"])
        self.assertNotIn("--epic", out["request_example"])

    def test_whoami_none_exits_0_and_mentions_epic_flag(self):
        self.caller({})
        out = self.cli_json(["whoami"])
        self.assertEqual(out["role"], "none")
        self.assertIsNone(out["epic"])
        self.assertEqual(out["read"], [])
        self.assertIn("--epic", out["request_example"])
        self.assertIn("--epic", out["hint"])
        code, text = run_cli(["whoami"])
        self.assertEqual(code, 0)
        self.assertIn("--epic", text)


if __name__ == "__main__":
    unittest.main()
