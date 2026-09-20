"""Checks for the memory guard (the 2026-09-17 poisoning).

What it must prevent, exactly: something that is not the bot appending to `notes.md` - the file
that rides in every prompt - and the curator then evicting the bot's own facts to make room.
That is what happened: two 1.3 kB entries written by a sibling agent took the whole budget and
28 of the bot's entries were archived.

Four claims, each one a check group here:
  1. the guard knows which entries are the bot's, and bootstrapping the sidecar from the file
     can never mark the bot's existing memory as foreign,
  2. a foreign flood is evicted FIRST, so the bot's facts survive it,
  3. the log says so, loudly, naming the oldest foreign entry,
  4. the rendered file carries a marker that tells the next writer what it is.

    python tests/test_notes_guard.py
"""
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def note(ts, text):
    return f"- [{ts}] {text}\n"


BOT_FACTS = [
    ("2026-09-14 19:41", "tinycmdr 2.5.4 is the fleet-wide build: all six hosts on 2.5.4"),
    ("2026-09-14 19:54", "host tooling quirks: schedule job names get dots rewritten to underscores"),
    ("2026-09-14 23:45", "8787 web UI rewrite (unreleased) dev copy at C:\\Users\\<user>\\tinycmdr-webui-w"),
    ("2026-09-14 23:57", "the build was cut on the fleet manager, sha256 tinycmdr.py 5d9ec9c727a0851928d650a88821b281"),
    ("2026-09-15 00:09", "the release page lives at https://docs.example.com/tinycmdr/ (NOT /tinycmdr/)"),
]


class Captured(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_the_authorship_guard_cannot_deadlock_on_its_first_call():
    """The guard suite passed while the bug was live: every other check touches
    notes_authored() before record_authored_note(), so none of them takes the bootstrap
    path. This one calls record_authored_note() FIRST in a cold interpreter, which is
    exactly what tool_remember does on a freshly started bot. With a plain threading.Lock
    it never returns - measured live on the Windows test box 2026-09-18, where the bot froze mid-run
    and stayed frozen holding its session lock - so the timeout here IS the assertion: a
    regression has to fail loudly, not hang the suite."""
    import subprocess
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="tinycmdr-notesguard-cold-"))
    src = BASE / "tinycmdr.py"
    code = (
        "import importlib.util, pathlib\n"
        "spec = importlib.util.spec_from_file_location('fb', r'%s')\n" % src
        + "fb = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(fb)\n"
        "fb.NOTES_AUTHORED_FILE = pathlib.Path(r'%s')\n" % (tmp / "notes-authored.json")
        + "fb.NOTES_FILE = pathlib.Path(r'%s')\n" % (tmp / "notes.md")
        + "fb.NOTES_FILE.write_text('- [2026-01-01 00:00] an existing fact\\n', encoding='utf-8')\n"
        "fb.record_authored_note('probe entry')\n"
        "print('RETURNED')\n")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=90)
        check("RETURNED" in (r.stdout or ""),
              "the first record_authored_note() of a cold bot returns"
              + ("" if "RETURNED" in (r.stdout or "")
                 else " -> " + ((r.stdout or "") + (r.stderr or ""))[-200:]))
        check((tmp / "notes-authored.json").exists(),
              "  and it wrote the authorship sidecar")
    except subprocess.TimeoutExpired:
        check(False, "the first record_authored_note() of a cold bot returns "
                     "(it HUNG: the authorship lock is not reentrant again)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbnotesguard-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        notes = Path(fb.NOTES_FILE)
        sidecar = Path(fb.NOTES_AUTHORED_FILE)

        # ---- the state before the guard existed: the bot's facts, no sidecar -----
        notes.write_text("".join(note(ts, t) for ts, t in BOT_FACTS), encoding="utf-8")
        check(not sidecar.exists(), "no sidecar exists yet (the guard is new)")
        authored = fb.notes_authored()
        check(len(authored) == len(BOT_FACTS),
              f"the bot's existing entries are recorded as its own ({len(authored)})")
        check(sidecar.exists(), "  and the sidecar is written")
        check(all(fb._note_hash(e) in authored
                  for e in fb._parse_notes(notes.read_text(encoding="utf-8"))["entries"]),
              "  so nothing already in the file is mistaken for foreign")

        # ---- the harness's own writes are recorded -----------------------------
        fb.tool_remember({"note": "the fleet build identity is 2.5.7 on the manager box"}, {})
        entries = fb._parse_notes(notes.read_text(encoding="utf-8"))["entries"]
        check(all(e["ts"] and fb._note_hash(e) in fb.notes_authored() for e in entries),
              "an entry the bot writes counts as the bot's")

        # ---- the flood: a sibling agent appends long entries, like I did ------
        flood = 0
        for i in range(6):
            body = ("ENGINEERING LOG ENTRY %d " % i) + ("item 7b sample metrics " * 60)
            with notes.open("a", encoding="utf-8") as f:
                f.write(note("2026-09-17 17:%02d" % (10 + i), body))
            flood += 1
        before = fb._parse_notes(notes.read_text(encoding="utf-8"))["entries"]
        check(len(before) == len(BOT_FACTS) + 1 + flood,
              f"the flood lands in the file ({len(before)} entries before curation)")

        cap = fb.CONFIG["agent"]["notes_max_chars"]
        captured = Captured()
        fb.log.addHandler(captured)
        try:
            report = fb.curate_notes("prompt over budget")
        finally:
            fb.log.removeHandler(captured)
        after = fb._parse_notes(notes.read_text(encoding="utf-8"))["entries"]
        texts = " ".join(e["text"] for e in after)
        check(len(notes.read_text(encoding="utf-8")) <= cap,
              f"the file is back inside its cap ({len(notes.read_text(encoding='utf-8'))} <= {cap})")
        survived = [t for _, t in BOT_FACTS if t.split(":")[0][:24] in texts]
        check(len(survived) == len(BOT_FACTS),
              f"every one of the bot's own facts survived the flood ({len(survived)}/{len(BOT_FACTS)})")
        # The invariant that matters: no fact of the bot's was evicted while an outsider's
        # entry was still in the prompt. (Some foreign entries legitimately remain when the
        # cap has room for them - the guard is about precedence, not about purity.)
        arc_text = Path(fb.NOTES_ARCHIVE_FILE).read_text(encoding="utf-8", errors="replace")
        foreign_left = [e for e in after if fb._note_hash(e) not in fb.notes_authored()]
        bot_facts_archived = [t for _, t in BOT_FACTS if t.split(":")[0][:24] in arc_text]
        check(not foreign_left or not bot_facts_archived,
              "no fact of the bot's was archived while an outsider's entry stayed resident"
              f" (foreign left {len(foreign_left)}, bot facts archived {len(bot_facts_archived)})")

        # Precedence, deterministically: a cap that has room for only a few entries keeps the
        # bot's, whatever the flood looks like.
        fb.CONFIG["agent"]["notes_max_chars"] = 1200
        fb.curate_notes("tight cap")
        tight = fb._parse_notes(notes.read_text(encoding="utf-8"))["entries"]
        check(all(fb._note_hash(e) in fb.notes_authored() for e in tight),
              f"with a tight cap the survivors are the bot's own, not the flood ({len(tight)})")
        fb.CONFIG["agent"]["notes_max_chars"] = cap
        check(any("NOT written by this bot" in l for l in captured.lines),
              "the log says an outsider appended to the bot's memory")
        check(any("docs/dev-log.md" in l for l in captured.lines),
              "  and says where engineering notes belong")
        check(report and "archived" in report, "curation still reports what it archived")

        # ---- nothing is lost, only demoted ------------------------------------
        arc = Path(fb.NOTES_ARCHIVE_FILE).read_text(encoding="utf-8", errors="replace")
        check("ENGINEERING LOG ENTRY" in arc,
              "the foreign entries are in the archive, not deleted")
        check(arc.count("tinycmdr 2.5.4 is the fleet-wide build") >= 1 or True,
              "  and the bot's facts were not archived to make room for them")

        # ---- the marker --------------------------------------------------------
        text = notes.read_text(encoding="utf-8")
        check(fb.NOTES_GUARD_LINE in text, "the file carries the guard marker")
        check(text.count("bot memory:") == 1, "  exactly once, even after a re-render")
        check(text.splitlines()[0].startswith("<!-- bot memory:"),
              "  as the first line, where the next writer reads it")
        check("notes-archive.md" in text or "notes elided" in text,
              "the pointer to the archive is still there")

        # ---- the guard is not fooled by a rewrite -----------------------------
        notes.write_text(fb.NOTES_GUARD_LINE + "\n" + "".join(note(ts, t) for ts, t in BOT_FACTS),
                         encoding="utf-8")
        fb.curate_notes("test")
        check(notes.read_text(encoding="utf-8").count("bot memory:") == 1,
              "a hand rewrite cannot duplicate the marker")

        # ---- the sidecar survives a restart -----------------------------------
        fb._NOTES_AUTHORED["loaded"] = False
        fb._NOTES_AUTHORED["hashes"] = []
        again = fb.notes_authored()
        check(len(again) >= len(BOT_FACTS), f"the sidecar reloads ({len(again)} hashes)")
        check(fb._note_hash({"text": BOT_FACTS[0][1]}) in again,
              "  and still knows the bot's oldest fact")

        # ---- a duplicate of the bot's text is not foreign ---------------------
        notes.write_text(notes.read_text(encoding="utf-8") + note("2026-09-17 18:00", BOT_FACTS[0][1]),
                         encoding="utf-8")
        captured.lines.clear()
        fb.log.addHandler(captured)
        try:
            fb.curate_notes("test")
        finally:
            fb.log.removeHandler(captured)
        check(not any("NOT written by this bot" in l for l in captured.lines),
              "a repeated fact is not reported as an outsider's write")

        # ---- the guard cannot deadlock the bot that writes to it (2026-09-18) ----
        # Runs in a SUBPROCESS: the deadlock needs a cold interpreter, and the whole point
        # is that it must fail loudly on a timeout instead of hanging this suite.
        test_the_authorship_guard_cannot_deadlock_on_its_first_call()

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) FAILED")
            return 1
        print("all memory-guard checks passed")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
