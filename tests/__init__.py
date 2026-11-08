import os
import tempfile

# Never read the developer's own files: herdr-epic's config (its language or roots would leak into
# assertions), Shepherdr's settings (its Assistant choice changes how orchestrators start) or the
# herdr-core templates. A test that needs one makes its own (temp_home, or the env var).
# This runs only when a test module imports the tests package, so every one does (test_isolation
# checks it): with `discover -s tests` the modules are top-level and nothing else would run it.
_tmp = tempfile.mkdtemp(prefix="herdr-epic-tests-")
ISOLATED_CONFIG = os.path.join(_tmp, "config.json")
os.environ["HERDR_EPIC_CONFIG"] = ISOLATED_CONFIG
os.environ["HERDR_TASK_SETTINGS"] = os.path.join(_tmp, "shepherdr.json")
os.environ["HERDR_TASK_TEMPLATES"] = os.path.join(_tmp, "templates")
# Tests run inside a real Herdr terminal: its HC_TERMINAL_ID would make every command resolve that
# terminal. A test that wants a caller sets it.
os.environ.pop("HC_TERMINAL_ID", None)
