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
        """A sessions/ sidecar must not load as a conversation of its own.

        Three files live in that folder beside a conversation - the carry, the hint list
        and the parked question - and Path.stem turns each into a key of its own. The
        exclusion list named only the carry, and
        the hints file is a JSON LIST, so it also listed as a conversation in the
        `sessions` verb.
        """
        real = _fb.SESSIONS_DIR / "reload-probe.json"
        real.write_text('[{"role": "user", "content": "hi"}]', encoding="utf-8")
        sidecars = {suffix: _fb.SESSIONS_DIR / ("reload-probe" + suffix)
                    for suffix in (".carry.json", ".hints.json", ".question.json")}
        sidecars[".carry.json"].write_text('{"run": 1, "entries": []}', encoding="utf-8")
        sidecars[".hints.json"].write_text('["hint-a", "hint-b"]', encoding="utf-8")
        sidecars[".question.json"].write_text('{"question": "restart?", "options": []}',
                                              encoding="utf-8")
        try:
            fresh = _fb.Agent()
            self.assertIn("reload-probe", fresh.histories)      # the conversation loads
            for suffix in sidecars:
                self.assertNotIn("reload-probe" + suffix.split(".")[1],
                                 fresh.histories)
            rows = [r["key"] for r in _fb._cli_session_rows()]
            self.assertIn("reload-probe", rows)
            for phantom in ("reload-probe.carry", "reload-probe.hints",
                            "reload-probe.question"):
                self.assertNotIn(phantom, rows)
        finally:
            real.unlink(missing_ok=True)
            for p in sidecars.values():
                p.unlink(missing_ok=True)

    def test_every_session_file_goes_on_forget_and_reset(self):
        """Forgetting a conversation (delete or prune) and /new drop ALL of its files.

        _web_forget_files kept a hand-written suffix list that missed the hint list and
        the parked question, and AGENT.reset dropped only the transcript and the page
        log - so a deleted conversation left sidecars behind for ever and /new carried
        the old carry, hints, question and events into the fresh one
        (A-2026-10-08-118). One list now (_CONVERSATION_FILE_SUFFIXES)."""
        key = "forget-probe"
        suffixes = (".json", ".web.jsonl", ".carry.json", ".hints.json",
                    ".question.json", ".transcript.jsonl", ".events.jsonl")

        def make():
            _fb.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            for s in suffixes:
                (_fb.SESSIONS_DIR / (key + s)).write_text("x", encoding="utf-8")

        try:
            make()
            _fb._web_forget_files(key)
            self.assertEqual([s for s in suffixes
                              if (_fb.SESSIONS_DIR / (key + s)).exists()], [],
                             "the web door left sidecars behind")
            make()
            AGENT.reset(key)
            self.assertEqual([s for s in suffixes
                              if (_fb.SESSIONS_DIR / (key + s)).exists()], [],
                             "/new left sidecars behind")
        finally:
            for s in suffixes:
                (_fb.SESSIONS_DIR / (key + s)).unlink(missing_ok=True)

    def test_fork_copies_the_state_but_not_the_parked_question(self):
        """A fork carries the conversation's sidecars - and not a run's question.

        The fork's suffix list was hand-written too; it now walks the ONE list, so a
        sidecar added later cannot be silently left out, and the two exclusions (the
        history, copied above, and the parked question, which belongs to the run that
        asked it) are stated in the code."""
        src, dst = "fork-src-118", "fork-dst-118"
        _fb.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            (_fb.SESSIONS_DIR / (src + ".json")).write_text("[]", encoding="utf-8")
            for s in (".carry.json", ".hints.json", ".question.json",
                      ".transcript.jsonl", ".events.jsonl"):
                (_fb.SESSIONS_DIR / (src + s)).write_text("x", encoding="utf-8")
            made = AGENT.fork(src, dst)
            self.assertTrue(made)
            for s in (".json", ".carry.json", ".hints.json", ".transcript.jsonl",
                      ".events.jsonl"):
                self.assertTrue((_fb.SESSIONS_DIR / (made + s)).exists(),
                                "fork left out %s" % s)
            self.assertFalse((_fb.SESSIONS_DIR / (made + ".question.json")).exists(),
                             "a fork copied a RUN's parked question")
        finally:
            for s in (".json", ".web.jsonl", ".carry.json", ".hints.json",
                      ".question.json", ".transcript.jsonl", ".events.jsonl"):
                (_fb.SESSIONS_DIR / (src + s)).unlink(missing_ok=True)
                (_fb.SESSIONS_DIR / (dst + s)).unlink(missing_ok=True)

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
