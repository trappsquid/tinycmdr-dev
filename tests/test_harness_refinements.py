import unittest
import tempfile
import pathlib
import sys
import os
import atexit
import shutil

# Ensure repo root is on sys.path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import tinycmdr as _fb                                                   # noqa: E402
from tinycmdr import (
    AGENT,
    _carry_reset,
    _CARRY,
    _CARRY_LOCK,
    _carry_path,
    tool_edit_file,
    tool_execute_code
)

# --- this suite owns the files it grades ----------------------------------------------
# Importing the app sets BASE_DIR to the checkout, so a write through the app landed in the
# repo and the session carry file created sessions/ beside it (both named by run_all.py's
# leak report). Point every repo-root data file at a temp dir this suite removes on the way out.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import hermetic                                                          # noqa: E402

_SANDBOX = pathlib.Path(tempfile.mkdtemp(prefix="fbharness-"))
atexit.register(lambda: shutil.rmtree(_SANDBOX, ignore_errors=True))
hermetic.redirect_repo_files(_fb, _SANDBOX)

class TestHarnessRefinements(unittest.TestCase):

    def test_carry_reset_on_session_reset(self):
        """AGENT.reset must clear in-memory carry and remove .carry.json on disk."""
        key = "test-reset-carry-key"
        with _CARRY_LOCK:
            _CARRY[key] = {"run": 1, "entries": [{"tool": "read_file", "args": "foo", "out": "bar"}]}
        
        carry_file = _carry_path(key)
        carry_file.write_text('{"run": 1, "entries": []}', encoding="utf-8")
        self.assertTrue(carry_file.exists())
        self.assertIn(key, _CARRY)

        AGENT.reset(key)

        self.assertNotIn(key, _CARRY)
        self.assertFalse(carry_file.exists())

    def test_session_loader_skips_carry_sidecars(self):
        """A *carry.json sidecar in sessions/ must not load as a <key>.carry session."""
        sidecar = _fb.SESSIONS_DIR / "reload-probe.carry.json"
        sidecar.write_text('{"run": 1, "entries": []}', encoding="utf-8")
        try:
            fresh = _fb.Agent()
            self.assertNotIn("reload-probe.carry", fresh.histories)
        finally:
            sidecar.unlink(missing_ok=True)

    def test_reset_reclaims_but_never_steals_session_locks(self):
        """AGENT.reset drops the session's lock, but leaves one a worker still holds."""
        key = "test-reset-lock-key"
        AGENT._lock(key)                       # mint it, as a run would
        self.assertIn(key, AGENT.locks)
        AGENT.reset(key)
        self.assertNotIn(key, AGENT.locks)

        held_key = "test-reset-held-lock-key"
        held = AGENT._lock(held_key)
        held.acquire()
        try:
            AGENT.reset(held_key)
            self.assertIn(held_key, AGENT.locks)
        finally:
            held.release()

    def test_tool_edit_file_full_line_deletion_exact(self):
        """Deleting a full line via edit_file exact match must not leave an empty line."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".conf") as f:
            f.write("[section]\nkey1 = value1\ndelete_me = true\nkey2 = value2\n")
            f_path = pathlib.Path(f.name)

        try:
            res = tool_edit_file({
                "path": str(f_path),
                "old_string": "delete_me = true",
                "new_string": ""
            }, {})
            self.assertIn("OK: replaced 1 occurrence", res)
            content = f_path.read_text(encoding="utf-8")
            self.assertEqual(content, "[section]\nkey1 = value1\nkey2 = value2\n")
        finally:
            f_path.unlink(missing_ok=True)
            pathlib.Path(str(f_path) + ".bak").unlink(missing_ok=True)

    def test_tool_edit_file_deletion_fuzzy(self):
        """Deleting lines via edit_file fuzzy match must not leave an empty line."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".conf") as f:
            f.write("[section]\n    key1 = value1\n    delete_me = true\n    key2 = value2\n")
            f_path = pathlib.Path(f.name)

        try:
            # old_string has different indentation to force fuzzy match
            res = tool_edit_file({
                "path": str(f_path),
                "old_string": "delete_me = true",
                "new_string": ""
            }, {})
            self.assertIn("OK: replaced 1 occurrence", res)
            content = f_path.read_text(encoding="utf-8")
            self.assertEqual(content, "[section]\n    key1 = value1\n    key2 = value2\n")
        finally:
            f_path.unlink(missing_ok=True)
            pathlib.Path(str(f_path) + ".bak").unlink(missing_ok=True)

    def test_tool_execute_code_name_error_hint(self):
        """execute_code should provide a process isolation hint when NameError occurs."""
        res = tool_execute_code({"code": "print(unknown_var_xyz)"}, {})
        self.assertIn("exit_code=1", res)
        self.assertIn("NameError: name 'unknown_var_xyz' is not defined", res)
        self.assertIn("[HINT: execute_code runs each snippet in an isolated Python process", res)

if __name__ == "__main__":
    unittest.main()
