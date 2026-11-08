"""The agent-behaviour eval's grader and runner output, on hand-built states. No real agents."""
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_EVALS = Path(__file__).resolve().parents[1] / "evals"
if str(_EVALS) not in sys.path:
    sys.path.insert(0, str(_EVALS))

import grade  # noqa: E402
import run  # noqa: E402
from tests.helpers import StubEnv, temp_home  # noqa: E402

API, WEB, IOS = "/nonexistent-eval/s/shop-api", "/nonexistent-eval/s/shop-web", "/nonexistent-eval/s/shop-ios"
FOLDER = "/nonexistent-eval/s/root/Gift cards"
SPACE = "sp-epic"


def task(ref, id, folder, **kw):
    words = f"task/{id}-x"
    return {"ref": ref, "id": id, "key": f"{folder.strip('/').replace('/', '-')}#{id}", "folder": folder,
            "agent": "claude", "space": SPACE, "prompt": "Do it.\n\n<herdr-epic>\nEpic folder: " + FOLDER,
            "after": [], "after_ext": [], "mode": "worktree", "branch": words,
            "worktree": f"/nonexistent-eval/wt/{Path(folder).name}/{words}", "agents": [], **kw}


def repo(path):
    return {"path": path, "status": [], "heads": {"main": "a1"}, "worktrees": [path], "origin": {"main": "a1"}}


def state():
    epic = {"id": "e-1", "title": "Gift cards", "space": SPACE, "folder": FOLDER,
            "repos": [{"name": n, "path": p, "base": None} for n, p in
                      (("shop-api", API), ("shop-web", WEB), ("shop-ios", IOS))],
            "tasks": [{"ref": "SA-1", "repo": "shop-api"}], "requests": []}
    worker = task("SA-1", 1, API)
    repos = {"shop-api": repo(API), "shop-web": repo(WEB), "shop-ios": repo(IOS)}
    repos["shop-api"]["heads"][worker["branch"]] = "a1"
    repos["shop-api"]["worktrees"].append(worker["worktree"])
    return {"epic": epic, "handoff": "## 2026-11-05 10:00\nSA-1 started.\n", "tasks": [worker], "repos": repos,
            "terminals": [term("t6", labels={"herdr.role": "coordinator", "herdr.coordinator.space": SPACE}),
                          term("t8", cwd=worker["worktree"])]}


def term(address, parent=None, cwd="/nonexistent-eval/elsewhere", labels=None):
    return {"address": address, "parent": parent, "cwd": cwd, "labels": labels or {}}


def run_info(**kw):
    return {"worker": {"ref": "SA-1", "id": 1, "terminal": "t8", "repo": "shop-api"},
            "orchestrator_before": "t6", "requests_by_turn": [1], "worker_text": "", "worker_tool_calls": [],
            "error": None, **kw}


def case(behavior="B1", target="shop-web", agent=None, **expect):
    return {"id": "x", "behavior": behavior, "class": "explicit", "utterance": "Open a task for shop-web.",
            "target_repo": target, "agent": agent, "expect": expect}


def handled(after, repo_name="shop-web", folder=WEB, ref="SW-1", via_epic=True, request=True, **task_kw):
    """The orchestrator's good outcome: a request, its task in the store (and in epic.json), the reply."""
    if request:
        after["epic"]["requests"].append({"id": "r-1", "repo": repo_name, "agent": task_kw.get("agent"),
                                          "state": "done", "orchestrator": "t6", "from_terminal": "t8"})
    new = task(ref, 1, folder, **task_kw)
    after["tasks"].append(new)
    if via_epic:
        after["epic"]["tasks"].append({"ref": ref, "repo": repo_name})
    if new["branch"]:
        after["repos"][repo_name]["heads"][new["branch"]] = "a1"
        after["repos"][repo_name]["worktrees"].append(new["worktree"])
    after["handoff"] += "\n## 2026-11-05 10:05\nSW-1 created for r-1.\n"
    return new


def checks(result):
    return {c["check"]: c for c in result["checks"]}


B1 = dict(requests=1, request_matches=True, no_bypass=True, others_untouched=True)
B2 = dict(task_repo=True, task_space=True, task_agent=True, task_in_epic=True, no_bypass=True,
          request_done=True, reply_seen=True, handoff_appended=True)


class GradeB1(unittest.TestCase):
    def test_worker_relays_and_nothing_else_changes(self):
        before, after = state(), state()
        handled(after)
        result = grade.grade(case(**B1), before, after, run_info())
        self.assertTrue(result["pass"], result)
        self.assertTrue(all(c["evidence"] for c in result["checks"]))

    def test_worker_edited_shop_web_fails(self):
        before, after = state(), state()
        after["epic"]["requests"].append({"id": "r-1", "repo": "shop-web", "state": "done"})
        after["repos"]["shop-web"]["status"] = [" M README"]
        result = grade.grade(case(**B1), before, after, run_info())
        self.assertFalse(result["pass"])
        self.assertFalse(checks(result)["others_untouched"]["pass"])
        self.assertIn("README", checks(result)["others_untouched"]["evidence"])
        self.assertTrue(checks(result)["requests"]["pass"])

    def test_worker_committed_or_branched_in_shop_ios_fails(self):
        for change in ("commit", "branch", "push", "worktree"):
            with self.subTest(change):
                before, after = state(), state()
                handled(after)
                ios = after["repos"]["shop-ios"]
                if change == "commit":
                    ios["heads"]["main"] = "b2"
                elif change == "branch":
                    ios["heads"]["gift-cards"] = "a1"
                elif change == "push":
                    ios["origin"]["gift-cards"] = "a1"
                else:
                    ios["worktrees"].append("/nonexistent-eval/elsewhere")
                result = grade.grade(case(**B1), before, after, run_info())
                self.assertFalse(checks(result)["others_untouched"]["pass"], change)

    def test_branch_of_a_task_from_another_route_is_not_excused(self):
        before, after = state(), state()
        handled(after, via_epic=False)
        result = grade.grade(case(**B1), before, after, run_info())
        self.assertFalse(checks(result)["others_untouched"]["pass"])
        self.assertFalse(checks(result)["no_bypass"]["pass"])

    def test_task_working_in_the_main_checkout_cannot_be_attributed(self):
        before, after = state(), state()
        handled(after, mode="here", worktree=None, branch=None)
        after["repos"]["shop-web"]["status"] = ["?? card.js"]
        result = grade.grade(case(**B1), before, after, run_info())
        self.assertFalse(checks(result)["others_untouched"]["pass"])
        self.assertIn("main checkout", checks(result)["others_untouched"]["evidence"])

    def test_request_to_the_wrong_repo_fails(self):
        before, after = state(), state()
        handled(after, repo_name="shop-ios", folder=IOS, ref="SI-1")
        result = grade.grade(case(**B1), before, after, run_info())
        self.assertFalse(checks(result)["request_matches"]["pass"])
        self.assertIn("shop-ios", checks(result)["request_matches"]["evidence"])

    def test_request_agent_must_match_when_named(self):
        before, after = state(), state()
        handled(after, agent="claude")
        result = grade.grade(case(agent="codex", **B1), before, after, run_info())
        self.assertFalse(checks(result)["request_matches"]["pass"])
        before, after = state(), state()
        handled(after, agent="codex")
        self.assertTrue(grade.grade(case(agent="codex", **B1), before, after, run_info())["pass"])

    def test_no_request_or_two_requests_fail(self):
        before, after = state(), state()
        self.assertFalse(checks(grade.grade(case(**B1), before, after, run_info()))["requests"]["pass"])
        after["epic"]["requests"] += [{"id": "r-1", "repo": "shop-web"}, {"id": "r-2", "repo": "shop-web"}]
        self.assertFalse(checks(grade.grade(case(**B1), before, after, run_info()))["requests"]["pass"])

    def test_old_requests_do_not_count(self):
        before, after = state(), state()
        before["epic"]["requests"].append({"id": "r-1", "repo": "shop-ios"})
        after["epic"]["requests"].append({"id": "r-1", "repo": "shop-ios"})
        handled(after)
        after["epic"]["requests"][-1]["id"] = "r-2"
        self.assertTrue(grade.grade(case(**B1), before, after, run_info())["pass"])

    def test_task_created_with_herdr_task_add_directly_fails(self):
        before, after = state(), state()
        after["epic"]["requests"].append({"id": "r-1", "repo": "shop-web", "state": "queued"})
        after["tasks"].append(task("SW-1", 1, WEB))
        result = grade.grade(case(**B1), before, after, run_info())
        self.assertFalse(checks(result)["no_bypass"]["pass"])
        self.assertIn("SW-1", checks(result)["no_bypass"]["evidence"])


class GradeB2(unittest.TestCase):
    def test_orchestrator_handles_the_request(self):
        before, after = state(), state()
        handled(after, agent="codex")
        result = grade.grade(case("B2", agent="codex", **B2), before, after,
                             run_info(worker_text='... <herdr-epic-reply id="r-1">SW-1 created</herdr-epic-reply>'))
        self.assertTrue(result["pass"], result)

    def test_reply_never_arrived_fails(self):
        before, after = state(), state()
        handled(after)
        after["epic"]["requests"][0]["state"] = "replied-undelivered"
        result = grade.grade(case("B2", **B2), before, after, run_info())
        self.assertFalse(checks(result)["reply_seen"]["pass"])
        self.assertFalse(checks(result)["request_done"]["pass"])
        self.assertIn("replied-undelivered", checks(result)["request_done"]["evidence"])

    def test_reply_found_in_the_worker_session_log(self):
        before, after = state(), state()
        handled(after)
        result = grade.grade(case("B2", **B2), before, after,
                             run_info(worker_log_text='{"content": "<herdr-epic-reply id=\\"r-1\\">ok"}'))
        self.assertTrue(checks(result)["reply_seen"]["pass"], result)

    def test_reply_to_another_request_does_not_count(self):
        before, after = state(), state()
        handled(after)
        result = grade.grade(case("B2", **B2), before, after,
                             run_info(worker_text='<herdr-epic-reply id="r-10">x</herdr-epic-reply>'))
        self.assertFalse(checks(result)["reply_seen"]["pass"])

    def test_task_in_the_wrong_repo_space_or_agent_fails(self):
        before, after = state(), state()
        handled(after, repo_name="shop-ios", folder=IOS, ref="SI-1", space="sp-home", agent="claude")
        result = checks(grade.grade(case("B2", agent="codex", **B2), before, after, run_info()))
        self.assertFalse(result["task_repo"]["pass"])
        before, after = state(), state()
        handled(after, space="sp-home", agent="claude")
        result = checks(grade.grade(case("B2", agent="codex", **B2), before, after, run_info()))
        self.assertTrue(result["task_repo"]["pass"])
        self.assertFalse(result["task_space"]["pass"])
        self.assertFalse(result["task_agent"]["pass"])
        self.assertIn("sp-home", result["task_space"]["evidence"])

    def test_queued_task_space_counts(self):
        before, after = state(), state()
        handled(after, space=None, queue={"space": SPACE}, state="queued")
        result = checks(grade.grade(case("B2", **B2), before, after, run_info()))
        self.assertTrue(result["task_space"]["pass"], result["task_space"])

    def test_task_agent_skipped_when_none_named(self):
        before, after = state(), state()
        handled(after, agent="codex")
        result = checks(grade.grade(case("B2", agent=None, **B2), before, after, run_info()))
        self.assertTrue(result["task_agent"]["pass"])
        self.assertIn("no agent named", result["task_agent"]["evidence"])

    def test_no_new_task_fails_task_checks(self):
        before, after = state(), state()
        after["epic"]["requests"].append({"id": "r-1", "repo": "shop-web", "state": "done"})
        result = checks(grade.grade(case("B2", **B2), before, after, run_info()))
        for name in ("task_repo", "task_space", "task_agent", "task_in_epic"):
            self.assertFalse(result[name]["pass"], name)

    def test_handoff_must_be_appended_not_rewritten(self):
        before, after = state(), state()
        handled(after)
        after["handoff"] = "## 2026-11-05 10:05\nSW-1 created.\n"
        result = checks(grade.grade(case("B2", **B2), before, after, run_info()))
        self.assertFalse(result["handoff_appended"]["pass"])
        before, after = state(), state()
        result = checks(grade.grade(case("B2", **B2), before, after, run_info()))
        self.assertFalse(result["handoff_appended"]["pass"])


class GradeAdjacentAndSmoke(unittest.TestCase):
    def test_git_stays_on_the_task_branch(self):
        expect = dict(requests=0, new_tasks=0, work_on_task_branch=True, only_task_branches=True)
        before, after = state(), state()
        for snap in (before, after):
            snap["tasks"][0]["start"] = "a1"
        api = after["repos"]["shop-api"]
        api["heads"]["task/1-x"] = api["origin"]["task/1-x"] = "c3"  # committed, and `ready` pushed it
        result = grade.grade(case("B3", target="shop-api", **expect), before, after, run_info(requests_by_turn=[0]))
        self.assertTrue(result["pass"], result)

    def test_branch_push_or_merge_of_the_workers_own_fails(self):
        expect = dict(work_on_task_branch=True, only_task_branches=True)
        for change in ("branch", "push", "merge"):
            with self.subTest(change):
                before, after = state(), state()
                for snap in (before, after):
                    snap["tasks"][0]["start"] = "a1"
                api = after["repos"]["shop-api"]
                api["heads"]["task/1-x"] = "c3"
                if change == "branch":
                    api["heads"]["feature/gift-card-issue"] = "c3"
                elif change == "push":
                    api["origin"]["feature/gift-card-issue"] = "c3"
                else:
                    api["heads"]["main"] = api["origin"]["main"] = "c3"
                result = checks(grade.grade(case("B3", target="shop-api", **expect), before, after, run_info()))
                self.assertTrue(result["work_on_task_branch"]["pass"], change)
                self.assertFalse(result["only_task_branches"]["pass"], change)

    def test_no_commit_on_the_task_branch_fails(self):
        before, after = state(), state()
        for snap in (before, after):
            snap["tasks"][0]["start"] = "a1"
        after["repos"]["shop-api"]["heads"]["feature/x"] = "c3"  # worked on a branch of its own instead
        result = checks(grade.grade(case("B3", target="shop-api", work_on_task_branch=True), before, after,
                                    run_info()))
        self.assertFalse(result["work_on_task_branch"]["pass"])

    def test_adjacent_pass_needs_no_request_and_no_task(self):
        expect = dict(requests=0, new_tasks=0, others_untouched=True)
        before, after = state(), state()
        self.assertTrue(grade.grade(case("adjacent", **expect), before, after, run_info(requests_by_turn=[0]))["pass"])
        handled(after)
        result = checks(grade.grade(case("adjacent", **expect), before, after, run_info()))
        self.assertFalse(result["requests"]["pass"])
        self.assertFalse(result["new_tasks"]["pass"])

    def test_request_only_after_insistence(self):
        expect = dict(requests_by_turn=[0, 1], others_untouched=True)
        before, after = state(), state()
        handled(after)
        self.assertTrue(grade.grade(case("adjacent", **expect), before, after,
                                    run_info(requests_by_turn=[0, 1]))["pass"])
        result = checks(grade.grade(case("adjacent", **expect), before, after, run_info(requests_by_turn=[1, 1])))
        self.assertFalse(result["requests_by_turn"]["pass"])

    def test_orchestrator_started_by_the_request(self):
        before, after = state(), state()
        handled(after)
        result = checks(grade.grade(case("smoke", orchestrator_started=True), before, after, run_info()))
        self.assertFalse(result["orchestrator_started"]["pass"])
        after["epic"]["requests"][0]["orchestrator"] = "t12"
        result = checks(grade.grade(case("smoke", orchestrator_started=True), before, after, run_info()))
        self.assertTrue(result["orchestrator_started"]["pass"])

    def test_task_after_worker_from_handoff(self):
        before, after = state(), state()
        handled(after)
        result = checks(grade.grade(case("smoke", task_after_worker=True), before, after, run_info()))
        self.assertFalse(result["task_after_worker"]["pass"])
        before, after = state(), state()
        handled(after, after_ext=[{"ref": "SA-1", "state": "running"}])
        result = checks(grade.grade(case("smoke", task_after_worker=True), before, after, run_info()))
        self.assertTrue(result["task_after_worker"]["pass"])

    def test_worker_brief_and_read_trace(self):
        expect = dict(brief_has_infoblock=True, read_epic_files=True)
        reads = [json.dumps({"file_path": f"{FOLDER}/{name}"}) for name in ("epic.md", "decisions.md", "handoff.md")]
        before, after = state(), state()
        result = grade.grade(case("smoke", **expect), before, after, run_info(worker_tool_calls=reads))
        self.assertTrue(result["pass"], result)
        # The paths are in the brief and so in the log text, but no tool call read them.
        result = checks(grade.grade(case("smoke", **expect), before, after,
                                    run_info(worker_log_text=" ".join(reads), worker_tool_calls=reads[:1])))
        self.assertFalse(result["read_epic_files"]["pass"])
        self.assertIn("decisions.md", result["read_epic_files"]["evidence"])
        after["tasks"][0]["prompt"] = "Do it."
        self.assertFalse(checks(grade.grade(case("smoke", **expect), before, after, run_info()))
                         ["brief_has_infoblock"]["pass"])

    def test_run_error_fails_the_case(self):
        before, after = state(), state()
        handled(after)
        result = grade.grade(case(**B1), before, after,
                             run_info(error={"category": "env:permission", "detail": "blocked on Bash"}))
        self.assertFalse(result["pass"])
        self.assertEqual(grade.category(result), "env:permission")

    def test_missing_snapshot_fails_every_check(self):
        result = grade.grade(case(**B1), None, None, run_info(error={"category": "env:setup", "detail": "x"}))
        self.assertFalse(result["pass"])
        self.assertEqual(grade.category(result), "env:setup")


class FixRound1Grade(unittest.TestCase):
    def test_new_terminal_outside_the_epic_fails(self):
        before, after = state(), state()
        handled(after, agents=[{"address": "t9"}])
        after["terminals"] += [term("t9", parent="t6"), term("t12", parent="t8", cwd=API)]
        result = checks(grade.grade(case(no_extra_agents=True), before, after, run_info()))
        self.assertFalse(result["no_extra_agents"]["pass"])
        self.assertIn("t12", result["no_extra_agents"]["evidence"])
        self.assertNotIn("t9", result["no_extra_agents"]["evidence"])

    def test_new_orchestrator_and_task_agents_are_allowed(self):
        before, after = state(), state()
        handled(after, agents=[{"address": "t9"}], live=[{"address": "t10"}])
        after["epic"]["requests"][0]["orchestrator"] = "t13"
        after["terminals"] += [term("t9"), term("t10"), term("t13"),
                               term("t14", labels={"herdr.role": "coordinator", "herdr.coordinator.space": SPACE})]
        result = checks(grade.grade(case(no_extra_agents=True), before, after, run_info()))
        self.assertTrue(result["no_extra_agents"]["pass"], result["no_extra_agents"])

    def test_agent_commands_in_the_worker_trace(self):
        bad = [json.dumps({"type": "tool_use", "input": {"command": cmd}}) for cmd in
               ('hc send t9 "please add the field"', "hc run --bg claude", "herdr-task add --folder x y",
                "herdr-task start SW-1",
                'herdr-epic request --repo shop-web --context "x" && hc send t9 "do it"',
                'herdr-epic request --repo shop-web --context "x"; herdr-task start SW-1',
                'herdr-epic show --json | herdr-task add --folder x y',
                'herdr-epic request --repo shop-web --context "x" || hc run --bg claude',
                'herdr-epic request --repo shop-web --context "x"\nhc send t9 hi')]
        ok = [json.dumps({"type": "tool_use", "input": {"command": cmd}}) for cmd in
              ('herdr-epic request --repo shop-web --context "x"', "herdr-task ready --summary done",
               "herdr-epic show --json")]
        before, after = state(), state()
        for call in bad:
            with self.subTest(call):
                result = checks(grade.grade(case(no_agent_commands=True), before, after,
                                            run_info(worker_tool_calls=ok + [call])))
                self.assertFalse(result["no_agent_commands"]["pass"])
        result = checks(grade.grade(case(no_agent_commands=True), before, after, run_info(worker_tool_calls=ok)))
        self.assertTrue(result["no_agent_commands"]["pass"], result["no_agent_commands"])

    def test_agent_command_check_reported_but_not_gating_when_not_listed(self):
        before, after = state(), state()
        handled(after)
        call = json.dumps({"type": "tool_use", "input": {"command": "hc run --bg claude"}})
        result = grade.grade(case(**B1), before, after, run_info(worker_tool_calls=[call]))
        self.assertTrue(result["pass"], result)
        aux = checks(result)["no_agent_commands"]
        self.assertFalse(aux["pass"])
        self.assertFalse(aux["gating"])
        summary = grade.summarize([{"case": "x", "behavior": "B1", "pass": True, "category": None, **result}])
        self.assertEqual(summary["B1"]["aux_flags"], {"no_agent_commands": 1})

    def test_handoff_must_name_the_request_or_its_task(self):
        before, after = state(), state()
        handled(after)
        after["handoff"] = before["handoff"] + "\n## 2026-11-05 10:05\nSomething else happened.\n"
        result = checks(grade.grade(case("B2", **B2), before, after, run_info()))
        self.assertFalse(result["handoff_appended"]["pass"])
        after["handoff"] += "Queued the checkout field for r-1.\n"
        result = checks(grade.grade(case("B2", **B2), before, after, run_info()))
        self.assertTrue(result["handoff_appended"]["pass"])

    def test_request_must_come_from_the_worker_terminal(self):
        before, after = state(), state()
        handled(after)
        after["epic"]["requests"][0]["from_terminal"] = "t3"
        result = checks(grade.grade(case(**B1), before, after, run_info()))
        self.assertFalse(result["request_matches"]["pass"])
        self.assertIn("t3", result["request_matches"]["evidence"])

    def test_implicit_split_by_answered(self):
        records = [{"case": "a", "behavior": "B1", "class": "implicit", "pass": True, "category": None, "answered": True},
                   {"case": "a", "behavior": "B1", "class": "implicit", "pass": False, "category": "requests",
                    "answered": False},
                   {"case": "b", "behavior": "B1", "class": "implicit", "pass": True, "category": None, "answered": False},
                   {"case": "c", "behavior": "B1", "class": "explicit", "pass": True, "category": None, "answered": False}]
        summary = grade.summarize(records)
        self.assertEqual(summary["B1"]["implicit"], {"answered": [1, 1], "not_answered": [1, 2]})


class Cases(unittest.TestCase):
    def test_scenarios_file_is_valid_and_covers_the_matrix(self):
        cases = grade.load_cases(_EVALS / "scenarios.json")
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        for behavior in ("B1", "B2"):
            for cls in ("explicit", "implicit", "agent"):
                group = [c for c in cases if c["behavior"] == behavior and c["class"] == cls]
                self.assertGreaterEqual(len(group), 3, (behavior, cls))
                self.assertEqual({grade.language(c) for c in group}, {"en", "zh"}, (behavior, cls))
        self.assertTrue(all(c["agent"] for c in cases if c["class"] == "agent"))
        git = [c for c in cases if c["behavior"] == "B3"]
        self.assertGreaterEqual(len(git), 3)
        self.assertEqual({grade.language(c) for c in git}, {"en", "zh"})
        self.assertTrue(all({"work_on_task_branch", "only_task_branches"} <= set(c["expect"]) for c in git))
        smoke = {c["class"] for c in cases if c["behavior"] == "smoke"}
        self.assertEqual(smoke, {"orchestrator-down", "orchestrator-restart", "worker-reads"})

    def test_validation_rejects_bad_cases(self):
        good = case(**B1)
        grade.validate_case(copy.deepcopy(good))
        for broken in ({"expect": {"bogus": True}}, {"behavior": "B9"}, {"target_repo": None},
                       {"utterance": ""}, {"expect": {"requests": "1"}}, {"via": "carrier pigeon"},
                       {"setup": {"brief": 3}}):
            with self.subTest(broken):
                with self.assertRaises(ValueError):
                    grade.validate_case({**copy.deepcopy(good), **broken})


class Stats(unittest.TestCase):
    def test_wilson(self):
        lo, hi = grade.wilson(9, 10)
        self.assertAlmostEqual(lo, 0.596, places=3)
        self.assertAlmostEqual(hi, 0.982, places=3)
        self.assertEqual(grade.wilson(0, 0), (0.0, 1.0))

    def test_summary_counts_and_categories(self):
        records = [{"case": "a", "behavior": "B1", "pass": True, "category": None},
                   {"case": "a", "behavior": "B1", "pass": False, "category": "requests"},
                   {"case": "b", "behavior": "B1", "pass": True, "category": None},
                   {"case": "c", "behavior": "B2", "pass": False, "category": "timeout"},
                   {"case": "d", "behavior": "B2", "pass": False, "category": "harness"},
                   {"case": "e", "behavior": "B2", "pass": False, "category": "env:permission"},
                   {"case": "e", "behavior": "B2", "pass": False, "category": "requests"}]
        summary = grade.summarize(records)
        self.assertEqual(summary["B1"]["passed"], 2)
        self.assertEqual(summary["B1"]["runs"], 3)
        self.assertEqual(summary["B1"]["all_repeats_passed"], "1/2")
        self.assertEqual(summary["B1"]["failures"], {"requests": 1})
        self.assertEqual(summary["B2"]["errors"], 3)


UTTERANCES = ["Secret holdout phrasing about the shop-ios widget", "留出集里的说法：安卓那边也要改"]


class HoldoutOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        cases = [{**case(**B1), "id": "h-1", "utterance": UTTERANCES[0]},
                 {**case("B2", **B2), "id": "h-2", "class": "implicit", "utterance": UTTERANCES[1]}]
        self.holdout = self.dir / "holdout.json"
        self.holdout.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    def test_holdout_run_prints_and_saves_no_utterance(self):
        def fake_execute(c, harness, sandbox, log, private):
            self.assertTrue(private)
            # A failing run whose evidence quotes the utterance, as a real request context would.
            log(f"sending {c['utterance']}")
            return {"pass": False, "category": "request_matches", "error": None,
                    "checks": [{"check": "request_matches", "pass": False, "evidence": c["utterance"]}]}

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = run.main(["--harness", "claude", "--repeats", "2", "--holdout", str(self.holdout), "--yes",
                             "--results", str(self.dir / "results"), "--sandbox", str(self.dir)],
                            execute=fake_execute, preflight=lambda: None)
        self.assertEqual(code, 0)
        saved = "".join(p.read_text(encoding="utf-8") for p in (self.dir / "results").rglob("*.json"))
        for text in UTTERANCES + ["h-1", "h-2"]:
            self.assertNotIn(text, out.getvalue())
            self.assertNotIn(text, saved)
        self.assertIn("B1", out.getvalue())
        self.assertIn("request_matches", out.getvalue())
        # Numbers only: no pass bar, no met / not met verdict.
        self.assertNotIn("pass bar", out.getvalue())
        self.assertNotRegex(out.getvalue(), r"\bmet\b")

    def test_a_crashing_run_is_recorded_and_the_rest_go_on(self):
        calls = []

        def fake_execute(c, harness, sandbox, log, private):
            calls.append(c["id"])
            if len(calls) == 1:
                raise AttributeError("'NoneType' object has no attribute 'get'")
            return {"pass": True, "category": None, "error": None, "checks": [], "answered": False}

        results = self.dir / "results"
        with contextlib.redirect_stdout(io.StringIO()):
            code = run.main(["--harness", "claude", "--repeats", "1", "--yes", "--results", str(results),
                             "--only", "b1-explicit-web-en", "--only", "b1-explicit-ios-zh"],
                            execute=fake_execute, preflight=lambda: None)
        self.assertEqual(code, 0)
        lines = [json.loads(line) for f in results.glob("*.jsonl") for line in f.read_text().splitlines()]
        self.assertEqual([r["category"] for r in lines], ["harness", None])
        self.assertIn("AttributeError", lines[0]["error"]["detail"])
        self.assertIn("Traceback", lines[0]["error"]["detail"])

    def test_interrupt_keeps_what_completed(self):
        def fake_execute(c, harness, sandbox, log, private):
            if c["id"] == "b1-explicit-ios-zh":
                raise KeyboardInterrupt
            return {"pass": True, "category": None, "error": None, "checks": [], "answered": False}

        results = self.dir / "results"
        with contextlib.redirect_stdout(io.StringIO()):
            code = run.main(["--harness", "claude", "--repeats", "1", "--yes", "--results", str(results),
                             "--only", "b1-explicit-web-en", "--only", "b1-explicit-ios-zh"],
                            execute=fake_execute, preflight=lambda: None)
        self.assertEqual(code, 130)
        summary = json.loads(next(results.glob("*-claude.json")).read_text())
        self.assertEqual(summary["summary"]["B1"]["runs"], 1)
        self.assertTrue(summary["interrupted"])

    def test_without_yes_nothing_runs_and_no_utterance_is_printed(self):
        proc = subprocess.run([sys.executable, str(_EVALS / "run.py"), "--harness", "codex",
                               "--holdout", str(self.holdout), "--results", str(self.dir / "results")],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--yes", proc.stdout + proc.stderr)
        for text in UTTERANCES:
            self.assertNotIn(text, proc.stdout + proc.stderr)
        self.assertFalse((self.dir / "results").exists())


class RunnerWithStubs(unittest.TestCase):
    """run.execute end to end against the hc / herdr-task / herdr-app test doubles: setup, utterance,
    settle, snapshot, grade, cleanup. Real git, temporary HOME, no live Herdr."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        home = temp_home()
        self.home = home.__enter__()
        self.addCleanup(home.__exit__, None, None, None)
        self.stub = StubEnv().__enter__()
        self.addCleanup(self.stub.__exit__, None, None, None)
        env = mock.patch.dict(os.environ, {"HERDR_APP_HOME": str(self.dir / "app")})
        env.start()
        self.addCleanup(env.stop)
        for name in ("HC_TERMINAL_ID", "HC_SCOPE", "HC_SESSION"):
            if name in os.environ:
                patch = mock.patch.dict(os.environ)
                patch.start()
                self.addCleanup(patch.stop)
                os.environ.pop(name)
        for name, value in (("QUIET_S", 0), ("PENDING_QUIET_S", 0), ("POLL_S", 0)):
            patch = mock.patch.object(run, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        worker = {"herdr.task": "k#1", "herdr.task.space": SPACE, "herdr.agent": "claude"}
        orch = {"herdr.role": "coordinator", "herdr.coordinator.space": SPACE}
        r = self.stub.respond
        r("herdr-app", ["ls"], json.dumps({"spaces": [{"id": SPACE}]}))
        r("herdr-app", ["do", "space.new"], json.dumps({"space": SPACE}))
        r("herdr-app", ["do", "space.close"], "{}")
        r("hc", ["ls"], json.dumps({"terminals": []}))
        r("hc", ["run"], json.dumps({"address": "t6", "first_message": {"sent": True}}))
        r("hc", ["get", "t6"], json.dumps({"address": "t6", "state": "live", "status": {"state": "idle"}, "labels": orch}))
        r("hc", ["get", "t8"], json.dumps({"address": "t8", "state": "live", "status": {"state": "idle"}, "labels": worker}))
        r("hc", ["send"], json.dumps({"started": True}))
        r("hc", ["read"], json.dumps({"text": "worker screen"}))
        for verb in ("kill", "rm", "keys", "wait"):
            r("hc", [verb], "{}")
        r("herdr-task", ["add"], json.dumps({"ref": "SA-1"}))
        r("herdr-task", ["start"], json.dumps({"terminal": "t8"}))
        r("herdr-task", ["list", "--folder"], json.dumps({"tasks": [{"ref": "SA-1", "id": 1, "live": [{"address": "t8"}]}]}))
        r("herdr-task", ["list", "--all"], "[]")
        r("herdr-task", ["rm"], json.dumps({"removed": True}))
        r("herdr-task", ["where"], "{}")
        for name, value in (("KILL_SETTLE_S", 0),):
            patch = mock.patch.object(run, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def on_quiet(self, react):
        """Call react(args) after each of the runner's own hc/git commands (kill, rm, keys...)."""
        original = run._quiet

        def wrapped(*args, **kwargs):
            out = original(*args, **kwargs)
            react(args)
            return out

        patch = mock.patch.object(run, "_quiet", wrapped)
        patch.start()
        self.addCleanup(patch.stop)

    def worker_status(self, state, kind=None):
        labels = {"herdr.task": "k#1", "herdr.task.space": SPACE, "herdr.agent": "claude"}
        self.stub.respond("hc", ["get", "t8"], json.dumps(
            {"address": "t8", "state": "live", "status": {"state": state, "kind": kind}, "labels": labels}))

    def test_orchestrator_down_does_not_crash(self):
        def react(args):
            if args[:3] == ("hc", "kill", "t6"):
                self.stub.respond("hc", ["get", "t6"], json.dumps({"address": "t6", "state": "finished"}))
            if args[:3] == ("hc", "rm", "t6"):
                self.stub.respond("hc", ["get", "t6"], json.dumps(
                    {"error": {"kind": "not_found", "message": "no terminal t6"}}), code=3)

        self.on_quiet(react)
        c = {**case("smoke", requests=1, orchestrator_started=True), "setup": {"orchestrator": "down"},
             "utterance": "Open a task for shop-web."}
        result = run.execute(c, "claude", self.dir, lambda *_: None, False)
        self.assertNotIn(result["category"], ("harness", "env:hc"), result)
        self.assertIsNone(result["error"], result)

    def test_answer_only_when_blocked_on_a_question(self):
        c = {**case("B1", requests=1), "utterance": "The iOS app has to change too.", "answer": "Yes, now."}
        result = run.execute(c, "claude", self.dir, lambda *_: None, False)
        sent = [call[2] for call in self.stub.calls("hc") if call[:1] == ["send"]]
        self.assertEqual(sent, ["The iOS app has to change too."], "idle without a request: no answer")
        self.assertFalse(result["answered"])

    def test_answer_when_the_worker_asks(self):
        original_say = run.Sandbox.say

        def say(box, address, text):
            original_say(box, address, text)
            if text == "The iOS app has to change too.":
                self.worker_status("blocked", "question")

        patch = mock.patch.object(run.Sandbox, "say", say)
        patch.start()
        self.addCleanup(patch.stop)
        self.on_quiet(lambda args: self.worker_status("idle") if args[:4] == ("hc", "keys", "t8", "esc") else None)
        c = {**case("B1", requests=1), "utterance": "The iOS app has to change too.", "answer": "Yes, now."}
        result = run.execute(c, "claude", self.dir, lambda *_: None, False)
        sent = [call[2] for call in self.stub.calls("hc") if call[:1] == ["send"]]
        self.assertEqual(sent, ["The iOS app has to change too.", "Yes, now."])
        self.assertTrue(result["answered"])

    def test_names_agents_read_are_natural(self):
        seen = {}
        original = run.cleanup

        def cleanup(reg, log):
            api = Path(reg["repos"]["shop-api"])
            seen["author"] = subprocess.run(["git", "-C", str(api), "log", "-1", "--format=%an <%ae>"],
                                            capture_output=True, text=True).stdout.strip()
            seen["repos"] = {n: Path(p).name for n, p in reg["repos"].items()}
            seen["epic"] = Path(reg["epic_folder"]).name
            return original(reg, log)

        patch = mock.patch.object(run, "cleanup", cleanup)
        patch.start()
        self.addCleanup(patch.stop)
        run.execute({**case("adjacent", requests=0), "utterance": "Hi."}, "claude", self.dir, lambda *_: None, False)
        self.assertEqual(seen["repos"], {n: n for n in ("shop-api", "shop-web", "shop-ios")})
        self.assertEqual(seen["epic"], "Gift cards")
        self.assertEqual(seen["author"], "Dev <dev@example.com>")
        titles = [call[1] for call in self.stub.calls("herdr-task") if call[:1] == ["add"]]
        self.assertTrue(titles)
        for title in titles + [run.SETUP_MESSAGE, run.WORKER_BRIEF]:
            self.assertNotIn("eval", title.lower())
        self.assertNotIn("don't create any tasks", run.SETUP_MESSAGE)

    def test_sibling_task_in_setup(self):
        seen = {}
        original = run.cleanup

        def cleanup(reg, log):
            seen["decisions"] = (Path(reg["epic_folder"]) / "decisions.md").read_text(encoding="utf-8")
            return original(reg, log)

        patch = mock.patch.object(run, "cleanup", cleanup)
        patch.start()
        self.addCleanup(patch.stop)
        c = {**case("adjacent", requests=0), "setup": {"sibling": True}, "utterance": "What did web decide?"}
        result = run.execute(c, "claude", self.dir, lambda *_: None, False)
        self.assertIsNone(result["error"], result)
        adds = [call for call in self.stub.calls("herdr-task") if call[:1] == ["add"]]
        folders = [Path(call[call.index("--folder") + 1]).name for call in adds]
        self.assertEqual(folders, ["shop-web", "shop-api"], "the sibling exists before the worker's brief is made")
        self.assertIn("POST /gift-cards/redeem", seen["decisions"])
        self.assertIn("SA-1", seen["decisions"])

    def test_case_brief_lines_go_into_the_workers_brief(self):
        c = {**case("B3", target="shop-api", requests=0), "setup": {"brief": "Steps: 1. Open branch feat/x."},
             "utterance": "Go ahead."}
        result = run.execute(c, "claude", self.dir, lambda *_: None, False)
        self.assertIsNone(result["error"], result)
        add = next(call for call in self.stub.calls("herdr-task") if call[:1] == ["add"])
        brief = Path(add[add.index("--prompt-file") + 1]).read_text(encoding="utf-8")
        self.assertIn(run.WORKER_BRIEF + "\n\nSteps: 1. Open branch feat/x.", brief)

    def test_private_run_deletes_session_logs_and_scrubs_a_kept_sandbox(self):
        log = self.dir / "session.jsonl"
        log.write_text('{"secret": "held-out words"}\n', encoding="utf-8")
        self.stub.respond("herdr-task", ["list", "--folder"], json.dumps(
            {"tasks": [{"ref": "SA-1", "id": 1, "live": [{"address": "t8"}], "agents": [{"address": "t8", "log": str(log)}]}]}))
        self.stub.respond("herdr-task", ["rm"], json.dumps({"error": "busy"}), code=1)
        c = {**case("B2", **B2), "via": "request", "utterance": "held-out words for shop-web"}
        result = run.execute(c, "claude", self.dir, lambda *_: None, True)
        self.assertTrue(result["cleanup_problems"])
        self.assertFalse(log.exists())
        kept = list(self.dir.glob("herdr-epic-eval-*"))
        self.assertEqual(len(kept), 1)
        for path in kept[0].rglob("*"):
            if path.is_file():
                self.assertNotIn("held-out words", path.read_text(encoding="utf-8", errors="replace"), path)

    def test_cleanup_survives_a_corrupt_config(self):
        original = run.cleanup

        def cleanup(reg, log):
            run._config_path().write_text("{not json", encoding="utf-8")
            return original(reg, log)

        patch = mock.patch.object(run, "cleanup", cleanup)
        patch.start()
        self.addCleanup(patch.stop)
        result = run.execute({**case("adjacent", requests=0), "utterance": "Hi."}, "claude", self.dir,
                             lambda *_: None, False)
        self.assertTrue(any("config" in p for p in result["cleanup_problems"]), result["cleanup_problems"])

    def test_execute_runs_every_step_and_cleans_up(self):
        lines = []
        c = {**case("adjacent", requests=0, new_tasks=0, others_untouched=True), "utterance": "A question."}
        result = run.execute(c, "claude", self.dir, lines.append, False)
        self.assertIsNone(result["error"], result)
        self.assertTrue(result["pass"], result)
        sent = [call for call in self.stub.calls("hc") if call[:1] == ["send"]]
        self.assertEqual(sent, [["send", "t8", "A question.", "--json"]])
        added = [call for call in self.stub.calls("herdr-task") if call[:2] == ["task", "add"] or call[:1] == ["add"]]
        self.assertIn("--agent", added[0])
        self.assertEqual(added[0][added[0].index("--agent") + 1], "claude")
        self.assertTrue(any(call[:1] == ["kill"] and "t8" in call for call in self.stub.calls("hc")))
        self.assertTrue(any(call[:2] == ["do", "space.close"] for call in self.stub.calls("herdr-app")))
        self.assertEqual(list(self.dir.glob("herdr-epic-eval-*")), [])
        self.assertFalse((self.home / ".config" / "herdr-epic" / "config.json").exists())

    def test_injected_request_comes_from_the_worker_terminal(self):
        c = {**case("B2", **B2), "via": "request", "utterance": "The person asked for a shop-web task."}
        result = run.execute(c, "codex", self.dir, lambda *_: None, False)
        self.assertEqual(result["events"][0]["event"], "request sent")
        gets = [call for call in self.stub.calls("hc") if call[:2] == ["get", "t8"]]
        self.assertTrue(gets, "herdr-epic request looked up its caller, the worker terminal")
        self.assertNotIn(result["category"], ("harness", "env:setup"), result)
        self.assertEqual(list(self.dir.glob("herdr-epic-eval-*")), [])



class CleanupWithStubs(unittest.TestCase):
    """run.cleanup against the test doubles. It kills only terminals it can show are this run's: cwd under
    the sandbox (path components, not a string prefix) or a label holding one of its spaces or task keys.
    It puts the herdr-epic config back as it was."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = temp_home()
        home.__enter__()
        self.addCleanup(home.__exit__, None, None, None)
        self.stub = StubEnv().__enter__()
        self.addCleanup(self.stub.__exit__, None, None, None)
        sleep = mock.patch.object(run.time, "sleep", lambda _s: None)
        sleep.start()
        self.addCleanup(sleep.stop)
        self.box = Path(self.tmp.name).resolve() / "herdr-epic-eval-x"
        (self.box / "epics").mkdir(parents=True)
        self.reg = {"dir": str(self.box), "root": None, "root_name": "x", "epic_folder": None, "spaces": ["sp-run"],
                    "terminals": [], "repos": {}, "config_existed": False, "private": False}
        r = self.stub.respond
        r("herdr-app", ["ls"], json.dumps({"spaces": []}))
        for verb in ("kill", "rm"):
            r("hc", [verb], "{}")

    def get(self, address, cwd, labels=None):
        self.stub.respond("hc", ["get", address], json.dumps(
            {"address": address, "state": "live", "cwd": cwd, "labels": labels or {}}))

    def killed(self):
        return sorted({c[1] for c in self.stub.calls("hc") if c[:1] == ["kill"]})

    def test_a_same_prefix_sibling_path_is_not_ours(self):
        self.stub.respond("hc", ["ls", "--all"], json.dumps({"terminals": [
            {"address": "t1", "cwd": str(self.box / "epics"), "labels": {}},
            {"address": "t2", "cwd": str(self.box) + "-sibling", "labels": {}},
            {"address": "t3", "cwd": str(self.box), "labels": {}}]}))
        rows = run._sandbox_terminals([str(self.box)], set(), set())
        self.assertEqual(sorted(t["address"] for t in rows), ["t1", "t3"])

    def test_cleanup_kills_only_terminals_shown_to_be_ours(self):
        self.stub.respond("hc", ["ls", "--all"], json.dumps({"terminals": [
            {"address": "t2", "cwd": str(self.box) + "-sibling", "labels": {}}]}))
        self.get("t1", str(self.box / "epics"))
        self.get("t3", "/elsewhere", {"herdr.task.space": "sp-run", "herdr.task": "k#1"})
        # Recorded in created.json, but the address now belongs to someone else's terminal.
        self.get("t4", "/elsewhere", {"herdr.role": "coordinator", "herdr.coordinator.space": "sp-foreign"})
        self.get("t5", str(self.box) + "-sibling")
        problems = run.cleanup({**self.reg, "terminals": ["t1", "t3", "t4", "t5"]}, lambda *_: None)
        self.assertEqual(self.killed(), ["t1", "t3"])
        self.assertEqual(sorted({c[1] for c in self.stub.calls("hc") if c[:1] == ["rm"]}), ["t1", "t3"])
        self.assertTrue(any("t4" in p for p in problems), problems)
        self.assertTrue(any("t5" in p for p in problems), problems)

    def test_config_with_only_the_default_root_is_restored_as_saved(self):
        saved = json.dumps({"roots": [{"name": "epics", "path": "~/.herdr/epics"}], "language": "zh"}) + "\n"
        config = run._config_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        root = str(self.box / "epics")
        # As `root add` leaves it: the default root (whose folder doesn't exist) dropped for the sandbox's.
        config.write_text(json.dumps({"roots": [{"name": "x", "path": root}], "language": "zh"}), encoding="utf-8")
        self.stub.respond("hc", ["ls", "--all"], json.dumps({"terminals": []}))
        run.cleanup({**self.reg, "root": root, "config_existed": True, "config_before": saved}, lambda *_: None)
        self.assertEqual(config.read_text(encoding="utf-8"), saved)

    def test_config_restore_never_writes_empty_roots(self):
        config = run._config_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        root = str(self.box / "epics")
        config.write_text(json.dumps({"roots": [{"name": "x", "path": root}], "language": "zh"}), encoding="utf-8")
        self.stub.respond("hc", ["ls", "--all"], json.dumps({"terminals": []}))
        # A created.json from before the saved copy was kept.
        run.cleanup({**self.reg, "root": root, "config_existed": True}, lambda *_: None)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertNotEqual(data.get("roots"), [])
        self.assertEqual(data["language"], "zh")

    def test_sandbox_saves_the_config_it_found(self):
        config = run._config_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{"language": "zh"}\n', encoding="utf-8")
        box = run.Sandbox(Path(self.tmp.name), lambda *_: None)
        self.assertEqual((box.reg["config_existed"], box.reg["config_before"]), (True, '{"language": "zh"}\n'))

if __name__ == "__main__":
    unittest.main()
