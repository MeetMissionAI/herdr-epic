import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from herdr_epic import Fail
from herdr_epic import store
from tests import ISOLATED_CONFIG  # noqa: F401  (importing tests keeps the developer's config out)

_REPO = str(Path(__file__).resolve().parents[1])


def _cfg(*roots):
    return {
        "roots": [{"name": name, "path": str(path)} for name, path in roots],
        "language": "en",
    }


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_folder_name_replaces_separators_and_controls_only(self):
        self.assertEqual(store.folder_name("  Gift cards  "), "Gift cards")
        self.assertEqual(store.folder_name("..Gift cards.."), "Gift cards")
        self.assertEqual(store.folder_name('say "hi"'), 'say "hi"')
        self.assertEqual(store.folder_name("a/b\\c:d"), "a-b-c-d")
        self.assertEqual(store.folder_name("a//b"), "a--b")
        self.assertEqual(store.folder_name("a\nb\tc\x1fd\x7fe"), "a-b-c-d-e")
        self.assertEqual(store.folder_name("  .礼品卡.  "), "礼品卡")

    def test_create_writes_all_files(self):
        api = self.root / "shop-api"
        epic = store.create(self.root, "Gift cards", [{"name": "shop-api", "path": str(api), "base": None}])
        folder = self.root / "Gift cards"
        for name in ["epic.json", "epic.md", "decisions.md", "handoff.md"]:
            self.assertTrue((folder / name).exists(), name)
        self.assertRegex(epic["id"], r"^e-[0-9a-f]{4}$")
        self.assertEqual(Path(epic["folder"]).resolve(), folder.resolve())
        data = json.loads((folder / "epic.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["id"], epic["id"])
        self.assertEqual(data["title"], "Gift cards")
        self.assertIsNone(data["space"])
        self.assertIsNone(data["machine"])
        self.assertEqual(data["state"], "open")
        self.assertIsNone(data["closed"])
        self.assertEqual(data["repos"], [{"name": "shop-api", "path": str(api), "base": None}])
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["requests"], [])
        self.assertNotIn("folder", data)
        self.assertNotIn("root", data)
        created = datetime.fromisoformat(data["created"])
        self.assertIsNotNone(created.tzinfo)
        self.assertEqual((folder / "decisions.md").read_text(encoding="utf-8"), "")
        self.assertEqual((folder / "handoff.md").read_text(encoding="utf-8"), "")
        body = (folder / "epic.md").read_text(encoding="utf-8")
        for heading in [
            "Background",
            "Goal",
            "Scope",
            "Not doing",
            "Acceptance",
            "Related docs",
            "Summary (written when closed)",
        ]:
            self.assertIn(heading, body)
        self.assertIn("Gift cards", body)
        (self.root / "notes").mkdir()
        cfg = _cfg(("shop", self.root))
        listed = store.all_epics(cfg)
        self.assertEqual([item["id"] for item in listed], [epic["id"]])
        self.assertEqual(listed[0]["root"], "shop")
        self.assertEqual(store.find(cfg, epic["id"])["id"], epic["id"])
        self.assertEqual(store.find(cfg, "Gift cards")["folder"], epic["folder"])
        self.assertEqual(store.find(cfg, folder.name)["id"], epic["id"])
        self.assertEqual(store.find(cfg, epic["folder"])["id"], epic["id"])
        other = store.create(self.root, "Gift wrap", [])
        self.assertNotEqual(other["id"], epic["id"])

    def test_unicode_and_quotes_title_roundtrip(self):
        title = '礼品卡 "v2" / Tom\'s'
        epic = store.create(self.root, title, [])
        self.assertEqual(Path(epic["folder"]).name, '礼品卡 "v2" - Tom\'s')
        self.assertEqual(store.find(_cfg(("shop", self.root)), title)["id"], epic["id"])
        saved = json.loads((Path(epic["folder"]) / "epic.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["title"], title)

    def test_create_existing_folder_fails(self):
        store.create(self.root, "Gift cards", [])
        with self.assertRaises(Fail):
            store.create(self.root, "Gift cards", [])

    def test_blank_title_fails(self):
        with self.assertRaises(Fail):
            store.create(self.root, " ... ", [])

    def test_concurrent_updates_keep_both(self):
        epic = store.create(self.root, "Gift cards", [])
        folder = epic["folder"]
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from herdr_epic import store\n"
            "marker = sys.argv[3]\n"
            "def add(epic):\n"
            "    epic['requests'].append({'id': marker})\n"
            "    time.sleep(0.15)\n"
            "store.update(Path(sys.argv[2]), add)\n"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script, _REPO, folder, "r-%d" % i],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for i in range(8)
        ]
        failed = []
        for i, proc in enumerate(procs):
            _out, err = proc.communicate()
            if proc.returncode != 0:
                failed.append((i, proc.returncode, err))
        self.assertEqual(failed, [])
        loaded = store.load(Path(folder))
        self.assertEqual(len(loaded["requests"]), 8)
        self.assertEqual(
            sorted(item["id"] for item in loaded["requests"]),
            ["r-%d" % i for i in range(8)],
        )
        on_disk = json.loads((Path(folder) / "epic.json").read_text(encoding="utf-8"))
        self.assertNotIn("folder", on_disk)
        self.assertEqual(len(list((Path(folder)).glob(".epic.json.*.tmp"))), 0)

    def test_find_ambiguous_lists_candidates(self):
        other = self.root / "other-root"
        other.mkdir()
        first = store.create(self.root, "Gift cards", [])
        second = store.create(other, "Gift cards", [])
        cfg = _cfg(("shop", self.root), ("other", other))
        with self.assertRaises(Fail) as caught:
            store.find(cfg, "Gift cards")
        message = str(caught.exception)
        self.assertIn(first["id"], message)
        self.assertIn(second["id"], message)
        self.assertIn(first["folder"], message)
        self.assertIn(second["folder"], message)
        with self.assertRaises(Fail) as missing:
            store.find(cfg, "no such epic")
        self.assertIn("no such epic", str(missing.exception))
        self.assertEqual(store.find(cfg, first["id"])["folder"], first["folder"])
        self.assertEqual(store.find(cfg, second["folder"])["id"], second["id"])

    def test_by_space(self):
        epic = store.create(self.root, "Gift cards", [])
        cfg = _cfg(("shop", self.root))
        self.assertIsNone(store.by_space(cfg, "space-shop"))
        self.assertEqual(store.all_epics({"roots": [{"name": "gone", "path": str(self.root / "missing")}], "language": "en"}), [])

        def assign(item):
            item["space"] = "space-shop"

        updated = store.update(Path(epic["folder"]), assign)
        self.assertEqual(updated["space"], "space-shop")
        self.assertEqual(updated["folder"], epic["folder"])
        found = store.by_space(cfg, "space-shop")
        self.assertEqual(found["id"], epic["id"])
        self.assertEqual(found["root"], "shop")
        self.assertIsNone(store.by_space(cfg, "space-other"))
        on_disk = json.loads((Path(epic["folder"]) / "epic.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["space"], "space-shop")
        self.assertNotIn("folder", on_disk)
        self.assertNotIn("root", on_disk)

    def test_append_log_is_append_only_with_date(self):
        epic = store.create(self.root, "Gift cards", [])
        folder = Path(epic["folder"])
        store.append_log(folder, "decisions.md", "pick shop-api")
        first = (folder / "decisions.md").read_text(encoding="utf-8")
        self.assertRegex(first, r"^\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}\npick shop-api\n$")
        store.append_log(folder, "decisions.md", "keep the log")
        second = (folder / "decisions.md").read_text(encoding="utf-8")
        self.assertTrue(second.startswith(first))
        self.assertRegex(second, r"\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}\nkeep the log\n$")
        self.assertLess(second.index("pick shop-api"), second.index("keep the log"))
        self.assertEqual((folder / "handoff.md").read_text(encoding="utf-8"), "")
        store.append_log(folder, "handoff.md", "next: shop-ios")
        handoff = (folder / "handoff.md").read_text(encoding="utf-8")
        self.assertRegex(handoff, r"^\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}\nnext: shop-ios\n$")
        self.assertTrue(second.startswith(first))
        with self.assertRaises(Fail):
            store.append_log(folder, "epic.md", "nope")

    def test_corrupt_sibling_does_not_block_other_epics(self):
        good = store.create(self.root, "Gift cards", [])

        def assign(item):
            item["space"] = "space-shop"

        store.update(Path(good["folder"]), assign)
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "epic.json").write_text("{", encoding="utf-8")
        not_object = self.root / "not-object"
        not_object.mkdir()
        (not_object / "epic.json").write_text("[]", encoding="utf-8")
        cfg = _cfg(("shop", self.root))
        self.assertEqual(store.find(cfg, "Gift cards")["id"], good["id"])
        self.assertEqual(store.find(cfg, good["id"])["id"], good["id"])
        self.assertEqual(store.by_space(cfg, "space-shop")["id"], good["id"])
        self.assertEqual([item["id"] for item in store.all_epics(cfg)], [good["id"]])
        made = store.create(self.root, "Gift wrap", [])
        self.assertRegex(made["id"], r"^e-[0-9a-f]{4}$")
        self.assertNotEqual(made["id"], good["id"])
        with self.assertRaises(Fail):
            store.load(broken)
        with self.assertRaises(Fail):
            store.load(not_object)

    def test_unreadable_or_non_utf8_epic_json_is_skipped(self):
        good = store.create(self.root, "Gift cards", [])
        latin = self.root / "latin"
        latin.mkdir()
        (latin / "epic.json").write_bytes(b'{"title": "caf\xe9"}')
        locked = self.root / "locked"
        locked.mkdir()
        (locked / "epic.json").write_text("{}", encoding="utf-8")
        os.chmod(locked / "epic.json", 0)
        cfg = _cfg(("shop", self.root))
        self.assertEqual([item["id"] for item in store.all_epics(cfg)], [good["id"]])
        self.assertRegex(store.create(self.root, "Gift wrap", [])["id"], r"^e-[0-9a-f]{4}$")

    def test_find_tilde_key_is_title_not_home(self):
        title = "~user notes"
        epic = store.create(self.root, title, [])
        cfg = _cfg(("shop", self.root))
        with self.assertRaises(Fail) as caught:
            store.find(cfg, "~scratch")
        self.assertIn("~scratch", str(caught.exception))
        self.assertEqual(store.find(cfg, title)["id"], epic["id"])
