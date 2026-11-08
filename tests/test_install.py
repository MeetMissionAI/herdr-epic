import json
import os
import unittest
from pathlib import Path
from unittest import mock

from herdr_epic import Fail
from herdr_epic import install

from tests.helpers import StubEnv, run_cli, temp_home

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "shepherdr-layout.json"
_REPO = Path(__file__).resolve().parents[1]


def find(node, node_id):
    if node.get("id") == node_id:
        return node
    for child in node.get("children", []):
        hit = find(child, node_id)
        if hit:
            return hit
    return None


def seed_shepherdr(home):
    app = home / ".config" / "herdr-app"
    (app / "layouts").mkdir(parents=True)
    (app / "layouts" / "shepherdr.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    py = app / "mods" / "tasks" / "bin" / "herdr_task"
    py.mkdir(parents=True)
    (py / "launch.py").write_text("herdr.task.space herdr.coordinator.space herdr.role", encoding="utf-8")
    (py / "cli.py").write_text("tell-assistant", encoding="utf-8")
    (app / "lib").mkdir()
    (app / "lib" / "coordinator.js").write_text("herdr.chat.slot coordinator.slots", encoding="utf-8")
    (app / "mods" / "tasks" / "inbox.js").write_text('setShared("tasks.thread", x)', encoding="utf-8")
    return app


def on_path(*dirs):
    """PATH with `dirs` first (the test doubles stay on it)."""
    return mock.patch.dict(os.environ, {"PATH": os.pathsep.join([*map(str, dirs), os.environ.get("PATH", "")])})


def snapshot(home):
    return sorted(str(p.relative_to(home)) for p in home.rglob("*"))


class InstallTest(unittest.TestCase):
    def test_generate_layout_wraps_spaces_chat_in_stack(self):
        src = json.loads(FIXTURE.read_text(encoding="utf-8"))
        before = json.dumps(src, sort_keys=True)
        out = install.generate_layout(src)
        self.assertEqual(json.dumps(src, sort_keys=True), before)
        stack = find(out, "epics-sidebar")
        self.assertEqual(stack["type"], "stack")
        self.assertEqual(stack["active"], "epics-by-epic")
        self.assertEqual(stack["header"], {"content": {"mod": "epics", "props": {"view": "switch"}}, "height": 44})
        self.assertEqual(stack["size"], {"weight": 1.0193114280700684})
        self.assertEqual(stack["min"], 90)
        self.assertEqual([c["id"] for c in stack["children"]], ["epics-by-epic", "epics-by-repo", "epics-spaces"])
        self.assertEqual(stack["children"][0]["content"], {"mod": "epics", "props": {"view": "epic"}})
        self.assertEqual(stack["children"][1]["content"], {"mod": "epics", "props": {"view": "repo"}})
        spaces = stack["children"][2]
        self.assertEqual(spaces["content"], {"mod": "spaces-chat"})
        self.assertNotIn("size", spaces)
        self.assertNotIn("min", spaces)
        self.assertEqual(len(out["cloned_from"]["sha256"]), 64)
        self.assertIsNone(find(out, "spaces"))

    def test_generate_layout_fails_without_spaces_chat(self):
        with self.assertRaises(Fail):
            install.generate_layout({"id": "root", "type": "row", "children": []})

    def test_install_dry_run_writes_nothing(self):
        with temp_home() as home:
            seed_shepherdr(home)
            before = snapshot(home)
            actions = install.install(home, dry_run=True)
            self.assertEqual(snapshot(home), before)
            self.assertTrue(any("shepherdr-epics.json" in a for a in actions))
            self.assertTrue(any("mods/epics/main.js" in a for a in actions))

    def test_install_writes_files_state_and_link_without_herdr_files(self):
        with temp_home() as home:
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            self.assertTrue((app / "mods/epics/main.js").exists())
            self.assertTrue((app / "layouts/shepherdr-epics.json").exists())
            self.assertTrue((home / ".local/bin/herdr-epic").is_symlink())
            state = json.loads((home / ".config/herdr-epic/installed.json").read_text(encoding="utf-8"))
            self.assertIn(".config/herdr-app/mods/epics/main.js", state["files"])
            # Idempotent
            self.assertTrue(all(a.startswith("record") for a in install.install(home, dry_run=False)))

    def test_install_keeps_user_edited_file_as_new(self):
        with temp_home() as home:
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            main = app / "mods/epics/main.js"
            main.write_text("// mine\n", encoding="utf-8")
            install.install(home, dry_run=False)
            self.assertEqual(main.read_text(encoding="utf-8"), "// mine\n")
            self.assertTrue((app / "mods/epics/main.js.new").exists())

    def test_install_does_not_overwrite_foreign_file(self):
        with temp_home() as home:
            app = seed_shepherdr(home)
            (app / "mods/epics").mkdir(parents=True)
            (app / "mods/epics/mod.json").write_text("{}", encoding="utf-8")
            install.install(home, dry_run=False)
            self.assertEqual((app / "mods/epics/mod.json").read_text(encoding="utf-8"), "{}")
            self.assertTrue((app / "mods/epics/mod.json.new").exists())

    def test_install_backs_up_own_previous_version(self):
        with temp_home() as home:
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            layout = app / "layouts/shepherdr-epics.json"
            old = layout.read_bytes()
            src = app / "layouts/shepherdr.json"
            data = json.loads(src.read_text(encoding="utf-8"))
            data["extra"] = 1
            src.write_text(json.dumps(data), encoding="utf-8")
            install.install(home, dry_run=False)
            self.assertNotEqual(layout.read_bytes(), old)
            backups = list((home / ".config/herdr-epic/backup").rglob("shepherdr-epics.json"))
            self.assertEqual([b.read_bytes() for b in backups], [old])

    def test_uninstall_removes_unedited_keeps_edited_and_roots(self):
        with temp_home() as home:
            app = seed_shepherdr(home)
            shepherdr_before = (app / "layouts/shepherdr.json").read_bytes()
            root = home / ".herdr/epics/gifts"
            root.mkdir(parents=True)
            (home / ".config/herdr-epic").mkdir(parents=True)
            (home / ".config/herdr-epic/config.json").write_text('{"roots": []}', encoding="utf-8")
            install.install(home, dry_run=False)
            (app / "mods/epics/main.js").write_text("// mine\n", encoding="utf-8")
            actions = install.uninstall(home, dry_run=True)
            self.assertTrue((app / "layouts/shepherdr-epics.json").exists())
            self.assertTrue(any(a.startswith("keep") and "main.js" in a for a in actions))
            install.uninstall(home, dry_run=False)
            self.assertFalse((app / "layouts/shepherdr-epics.json").exists())
            self.assertFalse((home / ".local/bin/herdr-epic").exists())
            self.assertFalse((app / "mods/epics/mod.json").exists())
            self.assertEqual((app / "mods/epics/main.js").read_text(encoding="utf-8"), "// mine\n")
            self.assertEqual((app / "layouts/shepherdr.json").read_bytes(), shepherdr_before)
            self.assertTrue(root.is_dir())
            self.assertTrue((home / ".config/herdr-epic/config.json").exists())

    def test_install_refreshes_root_agents_md_left_unchanged(self):
        import hashlib
        template = (_REPO / "herdr_epic" / "templates" / "root-AGENTS.md").read_text(encoding="utf-8")
        old = "the template of an older herdr-epic\n"
        with temp_home() as home:
            seed_shepherdr(home)
            roots = []
            for name, text in (("same", old), ("edited", "the person's own rules\n")):
                root = home / "work" / name
                root.mkdir(parents=True)
                (root / "AGENTS.md").write_text(text, encoding="utf-8")
                (root / "root.json").write_text(json.dumps({
                    "name": name, "space": "s", "repos": [],
                    "agents_sha256": hashlib.sha256(old.encode("utf-8")).hexdigest()}), encoding="utf-8")
                roots.append({"name": name, "path": str(root)})
            cfg = home / ".config" / "herdr-epic" / "config.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps({"roots": roots, "language": "en"}), encoding="utf-8")
            same, edited = home / "work" / "same", home / "work" / "edited"

            before = snapshot(home)
            dry = install.install(home, dry_run=True)
            self.assertEqual(snapshot(home), before)
            self.assertEqual((same / "AGENTS.md").read_text(encoding="utf-8"), old)
            self.assertTrue(any(str(same / "AGENTS.md") in a for a in dry), dry)

            actions = install.install(home, dry_run=False)
            self.assertEqual((same / "AGENTS.md").read_text(encoding="utf-8"), template)
            self.assertEqual(json.loads((same / "root.json").read_text())["agents_sha256"],
                             hashlib.sha256(template.encode("utf-8")).hexdigest())
            self.assertEqual((edited / "AGENTS.md").read_text(encoding="utf-8"), "the person's own rules\n")
            self.assertEqual((edited / "AGENTS.md.new").read_text(encoding="utf-8"), template)
            self.assertTrue(any(str(edited / "AGENTS.md") in a and "AGENTS.md.new" in a for a in actions), actions)

    def test_install_replaces_legacy_claude_md_import_with_a_link(self):
        with temp_home() as home:
            seed_shepherdr(home)
            root = home / "work" / "cards"
            root.mkdir(parents=True)
            (root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
            (root / "root.json").write_text(json.dumps({"name": "cards", "space": "s", "repos": []}), encoding="utf-8")
            cfg = home / ".config" / "herdr-epic" / "config.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps({"roots": [{"name": "cards", "path": str(root)}], "language": "en"}), encoding="utf-8")

            before = snapshot(home)
            dry = install.install(home, dry_run=True)
            self.assertEqual(snapshot(home), before)
            self.assertTrue(any(str(root / "CLAUDE.md") in a for a in dry), dry)

            install.install(home, dry_run=False)
            self.assertTrue((root / "CLAUDE.md").is_symlink())
            self.assertEqual(os.readlink(root / "CLAUDE.md"), "AGENTS.md")

    def test_unknown_flags_are_rejected_and_write_nothing(self):
        with temp_home() as home:
            seed_shepherdr(home)
            before = snapshot(home)
            for argv in (["install", "--dryrun"], ["install", "-n"], ["uninstall", "--dry_run"], ["doctor", "--x"]):
                code, _ = run_cli(argv)
                self.assertEqual(code, 1, argv)
            self.assertEqual(snapshot(home), before)

    def test_corrupt_state_is_a_clear_failure(self):
        with temp_home() as home:
            seed_shepherdr(home)
            (home / ".config/herdr-epic").mkdir(parents=True)
            (home / ".config/herdr-epic/installed.json").write_text('{"files": {', encoding="utf-8")
            for fn in (install.install, install.uninstall):
                with self.assertRaises(Fail):
                    fn(home, dry_run=False)
            (home / ".config/herdr-epic/installed.json").write_text('[]', encoding="utf-8")
            with self.assertRaises(Fail):
                install.uninstall(home, dry_run=True)

    def test_old_state_without_links_works_and_write_is_atomic(self):
        with temp_home() as home:
            seed_shepherdr(home)
            (home / ".config/herdr-epic").mkdir(parents=True)
            (home / ".config/herdr-epic/installed.json").write_text('{"files": {}}', encoding="utf-8")
            install.install(home, dry_run=False)
            self.assertFalse((home / ".config/herdr-epic/installed.json.tmp").exists())
            install.uninstall(home, dry_run=False)

    def test_uninstall_skips_paths_outside_install_set(self):
        with temp_home() as home:
            seed_shepherdr(home)
            victim = home / "victim.txt"
            victim.write_text("x", encoding="utf-8")
            outside = home.parent / "outside-victim.txt"
            outside.write_text("x", encoding="utf-8")
            import hashlib
            sha = hashlib.sha256(b"x").hexdigest()
            (home / ".config/herdr-epic").mkdir(parents=True)
            (home / ".config/herdr-epic/installed.json").write_text(json.dumps({"files": {
                "victim.txt": sha, "../outside-victim.txt": sha, str(outside): sha,
                ".config/herdr-app/mods/epics/../../../../victim.txt": sha,
                ".config/herdr-app/layouts/shepherdr.json": sha}, "links": {}}), encoding="utf-8")
            actions = install.uninstall(home, dry_run=False)
            self.assertTrue(victim.exists() and outside.exists())
            self.assertTrue((home / ".config/herdr-app/layouts/shepherdr.json").exists())
            self.assertEqual(len([a for a in actions if a.startswith("skip")]), 5)
            outside.unlink()

    def test_uninstall_notes_backups_and_layout_switch(self):
        with temp_home() as home:
            seed_shepherdr(home)
            install.install(home, dry_run=False)
            (home / ".config/herdr-epic/backup/t").mkdir(parents=True)
            actions = install.uninstall(home, dry_run=False)
            self.assertTrue(any("backups" in a and "backup" in a for a in actions))
            self.assertTrue(any("switch to another layout" in a for a in actions))

    def test_doctor_reports_corrupt_layout_as_failed_check(self):
        with StubEnv() as stub, temp_home() as home:
            self._stubs(stub)
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            (app / "layouts/shepherdr-epics.json").write_text("{", encoding="utf-8")
            layout = {c["check"]: c for c in install.doctor(home)}["layout"]
            self.assertFalse(layout["ok"])

    def test_version_probe_lives_in_herdr_module(self):
        from herdr_epic import herdr
        with StubEnv() as stub:
            stub.respond("hc", ["--version"], "hc 0.1.18\n")
            self.assertEqual(herdr.version("hc", "--version"), "0.1.18")
            self.assertIsNone(herdr.version("no-such-binary-xyz"))
        self.assertNotIn("subprocess", Path(install.__file__).read_text(encoding="utf-8"))

    def _stubs(self, stub):
        stub.respond("herdr-app", ["--version"], "herdr-app 0.1.13\n")
        stub.respond("hc", ["--version"], "hc 0.1.18\n")
        stub.respond("shepherdr", ["version"], "shepherdr 3.1.3\n")
        stub.respond("herdr-task", [], "")

    def test_doctor_flags_changed_shepherdr_layout(self):
        with StubEnv() as stub, temp_home() as home:
            self._stubs(stub)
            app = seed_shepherdr(home)
            (home / ".herdr/epics").mkdir(parents=True)
            install.install(home, dry_run=False)
            with on_path(home / ".local" / "bin"):
                checks = {c["check"]: c for c in install.doctor(home)}
            self.assertTrue(checks["layout"]["ok"])
            self.assertTrue(checks["herdr-app"]["ok"])
            self.assertTrue(all(c["ok"] for c in checks.values()), checks)
            src = app / "layouts/shepherdr.json"
            data = json.loads(src.read_text(encoding="utf-8"))
            data["extra"] = 1
            src.write_text(json.dumps(data), encoding="utf-8")
            layout = {c["check"]: c for c in install.doctor(home)}["layout"]
            self.assertFalse(layout["ok"])
            self.assertIn("herdr-epic install", layout["detail"])

    def test_doctor_flags_missing_shepherdr_label(self):
        with StubEnv() as stub, temp_home() as home:
            self._stubs(stub)
            app = seed_shepherdr(home)
            (app / "lib/coordinator.js").write_text("", encoding="utf-8")
            checks = {c["check"]: c for c in install.doctor(home)}
            self.assertFalse(checks["shepherdr herdr.chat.slot"]["ok"])
            self.assertTrue(checks["shepherdr tell-assistant"]["ok"])

    def test_doctor_checks_the_shepherdr_strings_the_sidebar_uses(self):
        with StubEnv() as stub, temp_home() as home:
            self._stubs(stub)
            app = seed_shepherdr(home)
            checks = {c["check"]: c for c in install.doctor(home)}
            for label in ("tasks.thread", "tasks-thread", "panel-body", "coordinator.slots"):
                self.assertTrue(checks[f"shepherdr {label}"]["ok"], checks[f"shepherdr {label}"])
            (app / "mods" / "tasks" / "inbox.js").write_text("", encoding="utf-8")
            checks = {c["check"]: c for c in install.doctor(home)}
            self.assertFalse(checks["shepherdr tasks.thread"]["ok"])

    def test_doctor_checks_herdr_epic_on_path_is_this_clone(self):
        with StubEnv() as stub, temp_home() as home:
            self._stubs(stub)
            seed_shepherdr(home)
            install.install(home, dry_run=False)
            with on_path(home / ".local" / "bin"):
                check = {c["check"]: c for c in install.doctor(home)}["herdr-epic on PATH"]
            self.assertTrue(check["ok"], check)
            with mock.patch.dict(os.environ, {"PATH": str(stub.dir)}):
                check = {c["check"]: c for c in install.doctor(home)}["herdr-epic on PATH"]
            self.assertFalse(check["ok"])
            self.assertIn("~/.local/bin is not on PATH", check["detail"])
            other = home / "other-bin"
            other.mkdir()
            (other / "herdr-epic").write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(other / "herdr-epic", 0o755)
            with on_path(other, home / ".local" / "bin"):
                check = {c["check"]: c for c in install.doctor(home)}["herdr-epic on PATH"]
            self.assertFalse(check["ok"])
            self.assertIn(str(other / "herdr-epic"), check["detail"])

    def test_install_and_doctor_refuse_another_herdr_app_home(self):
        with StubEnv() as stub, temp_home() as home:
            self._stubs(stub)
            seed_shepherdr(home)
            with mock.patch.dict(os.environ, {"HERDR_APP_HOME": str(home / "elsewhere")}):
                with self.assertRaises(Fail) as err:
                    install.install(home, dry_run=True)
                self.assertIn("HERDR_APP_HOME", str(err.exception))
                self.assertIn("not supported", str(err.exception))
                check = {c["check"]: c for c in install.doctor(home)}["HERDR_APP_HOME"]
                self.assertFalse(check["ok"])
            with mock.patch.dict(os.environ, {"HERDR_APP_HOME": str(home / ".config" / "herdr-app")}):
                install.install(home, dry_run=True)
                self.assertTrue({c["check"]: c for c in install.doctor(home)}["HERDR_APP_HOME"]["ok"])

    def test_cli_install_dry_run_json(self):
        with temp_home() as home:
            seed_shepherdr(home)
            code, out = run_cli(["install", "--dry-run", "--json"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["actions"])
            self.assertFalse((home / ".local").exists())


if __name__ == "__main__":
    unittest.main()


def _set_live_block(layout_path, content):
    """What Herdr does while the layout is in use: points the task thread's terminal block at a terminal."""
    data = json.loads(layout_path.read_text(encoding="utf-8"))

    def walk(n):
        if isinstance(n, dict):
            if n.get("id") == "tasks-thread-term":
                n["content"] = content
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(data)
    layout_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class LiveBlockTest(unittest.TestCase):
    def test_a_layout_changed_only_in_its_live_blocks_is_still_ours(self):
        with temp_home() as home:
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            layout = app / "layouts" / "shepherdr-epics.json"
            _set_live_block(layout, {"terminal": "t16"})
            actions = install.install(home, dry_run=False)
            self.assertFalse((app / "layouts" / "shepherdr-epics.json.new").exists(), actions)
            self.assertFalse(any("keep .config/herdr-app/layouts/shepherdr-epics.json" in a for a in actions), actions)
            removed = install.uninstall(home, dry_run=False)
            self.assertFalse(layout.exists(), removed)

    def test_an_install_recorded_with_the_raw_hash_is_recognised(self):
        # herdr-epic before this fix recorded the sha256 of the bytes it wrote.
        with temp_home() as home:
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            layout = app / "layouts" / "shepherdr-epics.json"
            state_path = home / ".config" / "herdr-epic" / "installed.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            rel = ".config/herdr-app/layouts/shepherdr-epics.json"
            import hashlib
            state["files"][rel] = hashlib.sha256(layout.read_bytes()).hexdigest()
            state_path.write_text(json.dumps(state), encoding="utf-8")
            _set_live_block(layout, {"terminal": "t16"})
            actions = install.install(home, dry_run=False)
            self.assertFalse((app / "layouts" / "shepherdr-epics.json.new").exists(), actions)

    def test_a_real_edit_outside_live_blocks_is_still_kept(self):
        with temp_home() as home:
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            layout = app / "layouts" / "shepherdr-epics.json"
            data = json.loads(layout.read_text(encoding="utf-8"))
            data["theme"] = "paper"
            layout.write_text(json.dumps(data), encoding="utf-8")
            install.install(home, dry_run=False)
            self.assertTrue((app / "layouts" / "shepherdr-epics.json.new").exists())


# A real shepherdr-epics.json as Herdr 0.1.13 left it on a machine (only the artifact's mod name
# changed): Shepherdr's one-child "panel" and "sidebar-lists" columns folded away, cloned_from's
# sha256 gone, the task terminal block moved to the end of its stack. It was generated from the
# same Shepherdr layout as tests/fixtures/shepherdr-layout.json.
REWRITTEN = Path(__file__).resolve().parent / "fixtures" / "shepherdr-epics-rewritten-by-herdr.json"


class HerdrRewriteTest(unittest.TestCase):
    def _rewritten(self, home, edit=None):
        app = seed_shepherdr(home)
        install.install(home, dry_run=False)
        data = json.loads(REWRITTEN.read_text(encoding="utf-8"))
        if edit:
            edit(data)
        layout = app / "layouts" / "shepherdr-epics.json"
        layout.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return app, layout

    def test_install_repairs_a_layout_herdr_rewrote_and_backs_it_up(self):
        with temp_home() as home:
            app, layout = self._rewritten(home)
            before = layout.read_bytes()
            self.assertIsNone(find(json.loads(before), "panel"))
            dry = install.install(home, dry_run=True)
            self.assertEqual(layout.read_bytes(), before)
            actions = install.install(home, dry_run=False)
            for listed in (dry, actions):
                repair = [a for a in listed if a.startswith("repair .config/herdr-app/layouts/shepherdr-epics.json")]
                self.assertEqual(len(repair), 1, listed)
                self.assertIn("panel", repair[0])
            data = json.loads(layout.read_text(encoding="utf-8"))
            self.assertEqual(find(data, "panel")["children"][0]["id"], "panel-body")
            self.assertEqual(find(data, "epics-sidebar")["header"]["content"]["props"], {"view": "switch"})
            self.assertEqual(len(data["cloned_from"]["sha256"]), 64)
            self.assertFalse((app / "layouts" / "shepherdr-epics.json.new").exists())
            backups = list((home / ".config/herdr-epic/backup").rglob("shepherdr-epics.json"))
            self.assertEqual([b.read_bytes() for b in backups], [before])

    def test_a_rewrite_written_with_rust_floats_is_recognised_too(self):
        def floats(node):
            if isinstance(node, dict):
                return {k: float(v) if type(v) is int else floats(v) for k, v in node.items()}
            return [floats(v) for v in node] if isinstance(node, list) else node
        with temp_home() as home:
            app, layout = self._rewritten(home)
            layout.write_text(json.dumps(floats(json.loads(layout.read_text(encoding="utf-8")))), encoding="utf-8")
            actions = install.install(home, dry_run=False)
            self.assertTrue(any(a.startswith("repair") for a in actions), actions)

    def test_a_rewritten_layout_the_person_also_edited_is_kept(self):
        with temp_home() as home:
            app, layout = self._rewritten(home, lambda d: d.update(theme="paper"))
            before = layout.read_bytes()
            actions = install.install(home, dry_run=False)
            self.assertEqual(layout.read_bytes(), before)
            self.assertTrue((app / "layouts" / "shepherdr-epics.json.new").exists(), actions)
            detail = {c["check"]: c for c in install.doctor(home)}["layout"]
            self.assertFalse(detail["ok"])
            self.assertIn("panel", detail["detail"])
            self.assertIn("edited", detail["detail"])

    def test_doctor_names_the_lost_nodes_and_passes_after_the_repair(self):
        with StubEnv() as stub, temp_home() as home:
            stub.respond("herdr-app", ["--version"], "herdr-app 0.1.13\n")
            self._rewritten(home)
            layout = {c["check"]: c for c in install.doctor(home)}["layout"]
            self.assertFalse(layout["ok"])
            self.assertIn("panel", layout["detail"])
            self.assertIn("sidebar-lists", layout["detail"])
            self.assertIn("herdr-epic install", layout["detail"])
            self.assertNotIn("not installed", layout["detail"])
            install.install(home, dry_run=False)
            self.assertTrue({c["check"]: c for c in install.doctor(home)}["layout"]["ok"])

    def test_a_lost_switch_header_is_named_and_not_repaired_as_a_rewrite(self):
        # Herdr never drops a header: a layout without one was edited, and stays.
        with temp_home() as home:
            app = seed_shepherdr(home)
            install.install(home, dry_run=False)
            layout = app / "layouts" / "shepherdr-epics.json"
            data = json.loads(layout.read_text(encoding="utf-8"))
            del find(data, "epics-sidebar")["header"]
            layout.write_text(json.dumps(data), encoding="utf-8")
            detail = {c["check"]: c for c in install.doctor(home)}["layout"]
            self.assertFalse(detail["ok"])
            self.assertIn("epics-sidebar header", detail["detail"])
            install.install(home, dry_run=False)
            self.assertTrue((app / "layouts" / "shepherdr-epics.json.new").exists())
            self.assertNotIn("header", find(json.loads(layout.read_text(encoding="utf-8")), "epics-sidebar"))
