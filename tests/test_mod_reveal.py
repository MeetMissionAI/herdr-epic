import copy
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.helpers import _ROOT
from herdr_epic import install

REVEAL = Path(_ROOT) / "mods" / "epics" / "reveal.js"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def find(node, node_id):
    if node.get("id") == node_id:
        return node
    return next(filter(None, (find(c, node_id) for c in node.get("children", []))), None)


def ids(node):
    return {node.get("id")}.union(*(ids(c) for c in node.get("children", [])))


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class RevealTest(unittest.TestCase):
    """mods/epics/reveal.js, run in node: the commands openTask sends to show Shepherdr's task view."""

    def steps(self, tree, node_id):
        script = (f"import {{ revealSteps }} from {json.dumps(REVEAL.as_uri())};"
                  "let s = ''; for await (const c of process.stdin) s += c;"
                  "const { tree, id } = JSON.parse(s); console.log(JSON.stringify(revealSteps(tree, id)));")
        out = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps({"tree": tree, "id": node_id}),
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def generated(self):
        return install.generate_layout(json.loads((FIXTURES / "shepherdr-layout.json").read_text(encoding="utf-8")))

    def test_with_the_panel_column_it_drills_down_and_shows_the_hidden_panel(self):
        tree = self.generated()
        find(tree, "panel")["hidden"] = True
        self.assertEqual(self.steps(tree, "tasks-thread-chat"), [
            ["layout.show", {"id": "panel"}],
            ["layout.select", {"stack": "panel-body", "child": "tasks"}],
            ["layout.select", {"stack": "tasks", "child": "tasks-thread"}],
            ["layout.select", {"stack": "tasks-thread-body", "child": "tasks-thread-chat"}],
        ])

    def test_without_the_panel_column_it_shows_what_holds_the_view_instead(self):
        tree = json.loads((FIXTURES / "shepherdr-epics-rewritten-by-herdr.json").read_text(encoding="utf-8"))
        self.assertIsNone(find(tree, "panel"))
        hidden = copy.deepcopy(tree)
        find(hidden, "panel-body")["hidden"] = True
        steps = self.steps(hidden, "tasks-thread-chat")
        self.assertEqual(steps[0], ["layout.show", {"id": "panel-body"}])
        self.assertEqual([s for s in steps if s[0] == "layout.select"], [
            ["layout.select", {"stack": "panel-body", "child": "tasks"}],
            ["layout.select", {"stack": "tasks", "child": "tasks-thread"}],
            ["layout.select", {"stack": "tasks-thread-body", "child": "tasks-thread-chat"}],
        ])
        # Shown already: nothing to show, and never a node the tree lacks.
        steps = self.steps(tree, "tasks-thread-chat")
        self.assertFalse([s for s in steps if s[0] == "layout.show"])
        named = {a.get("id") or a.get("stack") for _, a in steps} | {a.get("child") for _, a in steps}
        self.assertLessEqual(named - {None}, ids(tree))

    def test_a_node_the_tree_lacks_gives_no_commands(self):
        self.assertEqual(self.steps(self.generated(), "no-such-node"), [])


if __name__ == "__main__":
    unittest.main()
