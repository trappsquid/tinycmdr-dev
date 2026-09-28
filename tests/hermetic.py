"""Inputs a suite must STAGE, because a clean clone does not carry them.

Two gitignored files are load-bearing for suites, and both made a clean clone red
(measured 2026-09-26, `python tests/run_all.py`):

  maintenance/private_rules.py   the fleet's private inventory. maintenance/build-package.py
                                 refuses to import without it, so tests/test_config_example.py
                                 could not even reach its first check.
  field-notes.md                 the operator's own failure library. tests/test_digest.py
                                 read it from the repo root and died with FileNotFoundError.

Neither check was wrong; the suite just never staged the input the code looks for, so
"green on the author's box" and "green on a clone" were different claims. Everything here
writes into a temp dir (never the checkout — run_all.py reports any suite that does).

    python -c "import sys; sys.path.insert(0, 'tests'); import hermetic"
"""
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"

# Repo-root data files tinycmdr.py writes next to itself. A suite that imports the tree's
# own tinycmdr.py inherits BASE_DIR = the checkout, so its ledger, its sessions and its
# state land in the tree unless the suite moves them first (BUGREPORT T3).
REPO_DATA_FILES = (
    "NOTES_FILE", "NOTES_ARCHIVE_FILE", "TASKS_FILE", "TASKS_DOC", "TASKS_JOURNAL",
    "EXPERIMENTS_FILE", "SESSIONS_DIR", "UPLOADS_DIR", "JOBS_FILE", "GLOBAL_STATE_FILE",
    "NOTES_AUTHORED_FILE", "PROC_CENSUS_FILE",
    "LANE_STATE_FILE",           # logs/state.json: what the lane surfaces read
)

_staged = []


@atexit.register
def _cleanup():
    for path in _staged:
        shutil.rmtree(path, ignore_errors=True)


def _mktemp(prefix):
    path = Path(tempfile.mkdtemp(prefix=prefix))
    _staged.append(path)
    return path


def private_rules_on_path():
    """Stage maintenance/private_rules.example.py as private_rules.py, first on sys.path.

    maintenance/build-package.py does `from private_rules import ...` at import time and
    raises SystemExit without it. The example carries the same three names (PUBLIC_RULES,
    PUBLIC_FORBIDDEN, SECRET_LABELS) with placeholder values, which is all the config
    tiers check needs; the real inventory never has to exist for a clone to grade.
    Returns the staging dir (kept for the process, removed at exit).
    """
    stage = _mktemp("tinycmdr-private-rules-")
    shutil.copy2(REPO / "maintenance" / "private_rules.example.py", stage / "private_rules.py")
    if str(stage) not in sys.path:
        sys.path.insert(0, str(stage))
    return stage


def redirect_repo_files(fb, tmp):
    """Point every repo-root data file this build writes at `tmp`. Returns what moved.

    `fb` is the imported tinycmdr module; the names above are module globals holding
    Paths, so rebinding them is enough (the readers look them up by name at call time).
    Only the paths this build actually has are touched, so a renamed constant is a
    no-op rather than an AttributeError in a suite that is not about paths.
    """
    tmp = Path(tmp)
    moved = {}
    for name in REPO_DATA_FILES:
        cur = getattr(fb, name, None)
        if not isinstance(cur, Path):
            continue
        try:
            rel = cur.relative_to(fb.BASE_DIR)
        except ValueError:
            continue
        new = tmp / rel
        new.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith("_DIR") or cur.is_dir():
            new.mkdir(parents=True, exist_ok=True)   # the app assumes its dirs exist
        setattr(fb, name, new)
        moved[name] = new
    # tools-provenance.json is written BESIDE the tools dir, not in BASE_DIR, so rebinding
    # the names above cannot move it: mirror tools/ into the sandbox and point the registry
    # at the copy, so the record lands next to the copy while discovery still sees the same
    # files (test_checkin and test_events were named for this write by run_all.py).
    registry = getattr(fb, "REGISTRY", None)
    tools_dir = getattr(registry, "tools_dir", None)
    if (isinstance(tools_dir, Path) and tools_dir.is_dir()
            and tools_dir.parent == Path(fb.BASE_DIR)):
        shadow = tmp / "tools"
        shutil.copytree(tools_dir, shadow, dirs_exist_ok=True)
        registry.tools_dir = shadow
        moved["REGISTRY.tools_dir"] = shadow
    # The atlas is named by CONFIG, not by a module global, so it moves by config: plenty of
    # paths call ensure_atlas(), which writes BASE_DIR/atlas.md when it is missing, and
    # run_all.py named atlas.md for test_checkin (CI, ubuntu, 2026-09-27).
    cfg = getattr(fb, "CONFIG", None)
    cfg = cfg.get("agent") if hasattr(cfg, "get") else None
    if isinstance(cfg, dict):
        name = Path(cfg.get("atlas_file") or "atlas.md")
        if not name.is_absolute():
            cfg["atlas_file"] = str(tmp / name.name)
            moved["atlas_file"] = tmp / name.name
    return moved


def field_notes_fixture():
    """The field-notes library a clean clone can grade: tests/fixture-field-notes.md.

    A copy of the shape the app parses (## title / match: / scope: / note:), with one
    entry per signature the suite asserts and nothing else — the operator's real library
    is gitignored data and must not be a test dependency.
    """
    return TESTS / "fixture-field-notes.md"
