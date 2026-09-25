import unittest
import tempfile
import pathlib
import sys
import os

# Ensure repo root is on sys.path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from tinycmdr import (
    AGENT,
    _carry_reset,
    _CARRY,
    _CARRY_LOCK,
    _carry_path,
    tool_task,
    load_tasks,
    save_tasks,
    tool_edit_file,
    tool_execute_code
)

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

    def test_tool_task_no_open_tasks_done_notice(self):
        """When ledger has no open tasks, task action=done must return notice to report, not ERROR: use action=add."""
        t = load_tasks()
        orig_items = t["items"]
        try:
            # Set ledger with 1 done task
            t["items"] = [{"id": 99, "desc": "mock task", "status": "done", "note": "finished", "created": "", "updated": ""}]
            save_tasks(t)

            res = tool_task({"action": "done", "note": "everything completed"}, {})
            self.assertNotIn("ERROR: no open tasks in the ledger", res)
            self.assertIn("all tasks in the ledger are already closed", res)
            self.assertIn("Deliver your final report", res)
        finally:
            t["items"] = orig_items
            save_tasks(t)

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
