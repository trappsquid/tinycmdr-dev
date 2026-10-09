"""The must-agree contracts: each case DERIVES both sides, no hand lists.

Every page defect that reached a user today (2026-10-04) was one of these agreements
breaking silently: assets that never shipped, an id the test harness had not been told
about, an art URL the browser cached forever, a route table describing a file that no
longer existed, a shim branch swallowing a newer endpoint, an env var an installer wrote
that no code read. Each case below derives side A from one source and side B from
another, so adding something on one side without the other fails HERE - in the gate -
instead of on a fresh install.

The rule (docs/development.md 7): a reported bug lands as the fix PLUS the invariant
that grades its class. This file is where those invariants live; when a class has a
better home (test_webui_page's asset derivation, test_installer_unix's installed tree,
maintenance/check-package-assets.py for archives) it lives there and is not repeated.

    python tests/test_contracts.py
"""
import ast
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = (BASE / "tinycmdr.py").read_text(encoding="utf-8")
HARNESS = (BASE / "tests" / "webui_page_harness.js").read_text(encoding="utf-8")
CSS = (BASE / "assets" / "webui.css").read_text(encoding="utf-8")
DEV_DOC = (BASE / "docs" / "development.md").read_text(encoding="utf-8")
INSTALLERS = "\n".join(p.read_text(encoding="utf-8")
                       for p in sorted((BASE / "install").glob("install-tinycmdr*")))

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
    if not ok:
        FAILS.append(what)


def literal(name):
    """A module-level string literal (WEB_PAGE, WEB_THEME_CSS...) from tinycmdr.py."""
    for node in ast.walk(ast.parse(SRC)):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == name
                and isinstance(node.value, ast.Constant)):
            return node.value.value
    raise SystemExit("no %s string literal in tinycmdr.py" % name)


def fn_source(name):
    for node in ast.walk(ast.parse(SRC)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise SystemExit("no %s() in tinycmdr.py" % name)


def main():
    page = literal("WEB_PAGE")

    # ---- 1. the ids the page looks up vs the ids the test harness fabricates --------
    # A missing id makes getElementById return null and the page dies on its next write;
    # the harness's IDS list is the stand-in DOM. Equality both ways: a page id the
    # harness lacks breaks the suite's realism, and a harness id the page never looks up
    # is dead weight.
    page_ids = set(re.findall(r"getElementById\('([a-z0-9-]+)'\)", page))
    m = re.search(r"const IDS = \[(.*?)\];", HARNESS, re.S)
    harness_ids = set(re.findall(r"'([a-z0-9-]+)'", m.group(1))) if m else set()
    check("every id the page looks up exists in the test harness's DOM",
          page_ids <= harness_ids, sorted(page_ids - harness_ids))
    # ...and every OTHER id the harness fabricates is one its own code touches (freshDom
    # writes emptymark's src from the markup, for instance): an id nobody uses is dead
    # weight, and "the page looks it up" is not the only legitimate reason to have it.
    harness_used = set(re.findall(r"byId\.([a-z0-9-]+)", HARNESS)) \
        | set(re.findall(r"byId\['([a-z0-9-]+)'\]", HARNESS))
    check("...and every other harness id is one the harness's own code touches",
          harness_ids <= page_ids | harness_used, sorted(harness_ids - page_ids - harness_used))

    # ---- 2. placeholders vs the substitutions that fill them -------------------------
    # A {{TOKEN}} with no replace passes straight to the browser as literal text - the
    # old raw-template class. The replace sets are read from the functions that serve.
    for surface, text, fn in ((  # (name, served text, the function that serves it)
            ("WEB_PAGE", page, "_web_page_html"),
            ("assets/webui.css", CSS, "_web_page_css"),
            ("the manifest", "", "_web_manifest"))):
        body = fn_source(fn)
        filled = set(re.findall(r'replace\("\{\{([A-Z_]+)\}\}"', body))
        if surface == "assets/webui.css":
            have = set(re.findall(r"\{\{([A-Z_]+)\}\}", text))
        elif surface == "the manifest":
            have = set(re.findall(r"\{\{([A-Z_]+)\}\}", body))
        else:
            have = set(re.findall(r"\{\{([A-Z_]+)\}\}", text))
        check("%s: every placeholder has a substitution" % surface,
              have <= filled, sorted(have - filled))

    # ---- 3. every asset the page references is a row in the docs route table ---------
    # The table said /colonnade.svg after the file was deleted; this is the class.
    refs = set()
    for u in re.findall(r'(?:src|href)="(/[^"?#]+)', page):
        refs.add(u)
    if "/chibi.png" in page or "EMPTY_ART" in page:
        refs.add("/chibi.png")
    for u in re.findall(r"url\((/[^)?]+)", CSS):
        refs.add(u)
    refs = {r for r in refs if not r.startswith("/api/")}
    pats = []
    for ln in DEV_DOC.splitlines():
        if not ln.startswith("| `"):
            continue
        cell = ln.split("|")[1]
        for part in cell.split(","):
            part = part.strip().strip("`").strip()
            if part.startswith("/"):
                pats.append(re.compile("^" + re.escape(part).replace(r"\*", "[^`]*") + "$"))
    missing = [r for r in sorted(refs) if not any(p.match(r) for p in pats)]
    check("every asset the page references is a row in development.md's route table",
          not missing, missing)

    # ---- 4. the installers' env names are names the code actually reads --------------
    # An installer writing TINYCMDR_X that no code reads is a silent no-op (or a typo'd
    # rename half-landed). Direction: installers -> code.
    code_env = set(re.findall(r"TINYCMDR_[A-Z0-9_]+", SRC))
    # The docs describe the whole product, so their names must exist in SOME program in
    # the tree (the app, the installers, the shim/updater, maintenance) - not necessarily
    # in tinycmdr.py: TINYCMDR_SERVICE is the installers', TINYCMDR_WHERE_ROLES where.py's.
    programs = [SRC, INSTALLERS,
                (BASE / "update.sh").read_text(encoding="utf-8"),
                (BASE / "tinycmdr").read_text(encoding="utf-8"),
                (BASE / "install.sh").read_text(encoding="utf-8")]
    programs += [q.read_text(encoding="utf-8")
                 for q in sorted((BASE / "maintenance").glob("*.py"))]
    # ...and the maintenance SHELL scripts, which read their own overrides: release.sh takes
    # TINYCMDR_SKIP_TREE_CHECK, TINYCMDR_SKIP_CI_GATE and TINYCMDR_SKIP_PRODUCT_CI, and
    # pre-push.sh drives the gates. Leaving them out made the docs unable to name an override
    # that really exists (measured 2026-10-08, writing §7 of docs/development.md).
    programs += [q.read_text(encoding="utf-8")
                 for q in sorted((BASE / "maintenance").glob("*.sh"))]
    docs_text = "\n".join(p.read_text(encoding="utf-8")
                          for p in sorted((BASE / "docs").glob("*.md"))) \
        + (BASE / "README.md").read_text(encoding="utf-8")
    docs_env = set(re.findall(r"TINYCMDR_[A-Z0-9_]+", docs_text))
    known = set(re.findall(r"TINYCMDR_[A-Z0-9_]+", "\n".join(programs)))
    check("every TINYCMDR_* the docs name exists in some program in the tree",
          docs_env <= known, sorted(docs_env - known))
    # ...and every key an installer writes into .env is one the code reads (a written
    # key nobody reads is a silent no-op). The Windows installer's writes are graded by
    # tests/test_installer_windows.py; this pinches the two POSIX installers.
    written = {"TINYCMDR_" + w
               for w in re.findall(r"printf 'TINYCMDR_([A-Z0-9_]+)=", INSTALLERS)}
    check("every TINYCMDR_* an installer writes into .env is one the code reads",
          written <= code_env, sorted(written - code_env))

    # ---- 5. no route prefix shadows a later route (both routers) ---------------------
    # '/api/log' matched '/api/login' in the page harness because it was tested first -
    # the probe read as 200 and the token prompt bug stayed invisible. In a router, an
    # EARLIER prefix must never be a prefix of a LATER path.
    def shadows(prefixes):
        bad = []
        for i, a in enumerate(prefixes):
            for b in prefixes[i + 1:]:
                if a != b and b.startswith(a):
                    bad.append("%s before %s" % (a, b))
        return bad

    # Per HANDLER: do_GET and do_POST are separate dispatch chains, so a prefix only
    # shadows another in its own chain.
    chains, order = [], []
    for m in re.finditer(r'def (do_[A-Z]+)\(|self\.path\.startswith\("([^"]+)"\)', SRC):
        if m.group(1):
            chains.append((m.group(1), []))
        elif chains:
            chains[-1][1].append(m.group(2))
    bad = {"%s: %s" % (name, s) for name, pfx in chains for s in shadows(pfx)}
    order = sorted(bad)
    check("the server's route prefixes are ordered so none shadows a later one "
          "(each handler chain separately)", not bad, order[:4])
    shim_prefixes = re.findall(r"url\.indexOf\('([^']+)'\) === 0", HARNESS)
    check("the page harness's branches are ordered so none shadows a later one",
          not shadows(shim_prefixes), shadows(shim_prefixes))

    # ---- a starter tool ships in BOTH lists, or it ships nowhere -------------
    # `.gitignore` decides what git carries, SHIP/APP_FILES in build-package.py decide
    # what the archive contains and what the installers copy - the 1.0.68-1.0.70 assets
    # shipped in one hand list and not another, and a starter is the same shape of
    # promise. Both sides are derived, so a new starter cannot be forgotten in one of
    # them (tools/computer_use.py joined all three on 2026-10-05).
    _gi = (BASE / ".gitignore").read_text(encoding="utf-8")
    _tracked_tools = {"tools/%s" % t for t in re.findall(r"^!tools/(\S+)$", _gi, re.M)}
    _bp = (BASE / "maintenance" / "build-package.py").read_text(encoding="utf-8")
    _packed_tools = set(re.findall(r'"(tools/[^"/]+)"', _bp))
    check("every git-tracked tools/ file is in build-package's shipped lists",
          _tracked_tools and _tracked_tools <= _packed_tools,
          sorted(_tracked_tools - _packed_tools))
    check("...and build-package ships no tools/ file git does not track",
          _packed_tools <= _tracked_tools, sorted(_packed_tools - _tracked_tools))

    # ---- a release goes to the repo every installer and updater FETCHES from -------------
    # The source tree and the install surface are two repositories, and `gh` resolves one from
    # the tree it runs in - so an unflagged `gh release create` in the source repo publishes a
    # release nothing fetches from (measured 2026-10-07, the first cut after the split, before
    # release.sh named the repo). Side A is the repo release.sh publishes to; side B is the
    # repo the update path and the two installers download from. A new fetch path that names a
    # third repo fails here.
    _rel = (BASE / "maintenance" / "release.sh").read_text(encoding="utf-8")
    _m = re.search(r'RELEASE_REPO="\$\{TINYCMDR_RELEASE_REPO:-([^}"]+)\}"', _rel)
    _release_repo = _m.group(1) if _m else None
    _m = re.search(r'DEFAULT_UPDATE_URL = "https://github\.com/([^/"]+/[^/"]+)/', SRC)
    _update_repo = _m.group(1) if _m else None
    _fetch = {}
    _m = re.search(r'REPO="([^"]+)"', (BASE / "update.sh").read_text(encoding="utf-8"))
    _fetch["update.sh"] = _m.group(1) if _m else None
    _m = re.search(r'BASE="\$\{TINYCMDR_URL:-https://github\.com/([^/"]+/[^/"]+)/',
                   (BASE / "install.sh").read_text(encoding="utf-8"))
    _fetch["install.sh"] = _m.group(1) if _m else None
    check("release.sh names the repo a release is published to", bool(_release_repo),
          _release_repo)
    check("...and it is the repo the update path downloads from",
          bool(_update_repo) and _update_repo == _release_repo,
          "%s vs %s" % (_release_repo, _update_repo))
    check("...and the repo both installers download from",
          all(v == _release_repo for v in _fetch.values()),
          "%s vs %s" % (_release_repo, _fetch))

    # ---- the Windows tier is stated in ONE file, and it is complete ---------------------
    # tests/windows-tier.json is the single place this is declared: `must` (graded on Windows
    # in the dev CI on every push), `scheduled` (the rest of what can run there: nightly and on
    # demand), `excluded` (red on Windows, each with the reason it is not fixed), and
    # `not_applicable` (cannot run there at all, and says so itself). Both workflows name a
    # TIER - run_all.py --tier - instead of carrying a suite list, because two hand-kept lists
    # drift: a suite that only the product's workflow ran on Windows reached the install
    # surface untested (measured 2026-10-08, tests/test_lane_choice.py).
    _tier = json.loads((BASE / "tests" / "windows-tier.json").read_text(encoding="utf-8"))
    _buckets = {}
    for _key in ("must", "scheduled", "excluded", "not_applicable"):
        _val = _tier.get(_key)
        _buckets[_key] = set()
        _pats = _val if isinstance(_val, list) else sorted(_val or {})
        # `excluded` is the one bucket allowed to be EMPTY, and empty is the goal: a suite that
        # cannot run on a platform says so in its own output, not in a list here.
        if not _pats and _key != "excluded":
            check("the tier file declares %r" % _key, False, sorted(_tier))
        for _pat in _pats:
            _hits = {q.relative_to(BASE).as_posix() for q in BASE.glob(_pat)}
            check("tier %r names %s, which matches no suite" % (_key, _pat),
                  bool(_hits), sorted(_hits))
            _buckets[_key] |= _hits
    _all = {q.relative_to(BASE).as_posix() for q in (BASE / "tests").glob("test_*.py")}
    _declared = set().union(*_buckets.values())
    check("every suite in the tree is declared in the Windows tier exactly once",
          _declared == _all and sum(len(_buckets[k]) for k in _buckets) == len(_all),
          sorted(_all - _declared) + ["twice: %s" % n for n in sorted(_declared)
                                      if sum(n in _buckets[k] for k in _buckets) > 1])
    # `dev_only` is a QUALIFIER, not a fifth bucket: a suite the product does not ship, so the
    # install surface's job can read the same tier file without failing on a grader it has no
    # copy of. It must still be declared runnable above, or it is a name nothing grades.
    _dev_only = list(_tier.get("dev_only") or [])
    _dev_expanded = set()
    for _pat in _dev_only:
        _hits = {q.relative_to(BASE).as_posix() for q in BASE.glob(_pat)}
        check("dev_only names %s, which is not in this tree" % _pat, bool(_hits), sorted(_hits))
        _dev_expanded |= _hits
    check("...and every dev_only suite is still declared runnable here",
          _dev_expanded and _dev_expanded <= (_buckets["must"] | _buckets["scheduled"]),
          sorted(_dev_expanded - (_buckets["must"] | _buckets["scheduled"])))
    for _key in ("excluded", "not_applicable"):
        _reasons = _tier.get(_key) or {}
        if _key == "excluded" and not _reasons:
            continue
        check("every %s suite carries a reason" % _key,
              _reasons and all(str(v).strip() for v in _reasons.values()),
              sorted(k for k, v in _reasons.items() if not str(v).strip()))
    _wf_dev = (BASE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    _wf_prod = (BASE / "maintenance" / "product-files" / "tests.yml").read_text(
        encoding="utf-8")
    check("both Windows jobs name a TIER, not a suite list",
          "--tier must" in _wf_dev and "--tier windows" in _wf_prod
          and "--exclude tests/" not in _wf_prod,
          [_ln.strip() for _ln in _wf_prod.splitlines() if "--exclude" in _ln][:4])
    _items = json.loads((BASE / "STATUS.json").read_text(encoding="utf-8"))["items"]
    _tier_items = [it for it in _items
                   if it.get("id") == "windows-tier-on-the-install-surface"]
    check("the tier is described by exactly one STATUS.json item",
          len(_tier_items) == 1, [it.get("id") for it in _tier_items])
    if _tier_items:
        _item = _tier_items[0]
        _named = set(_item.get("windows_excluded") or [])
        _na = set(_item.get("windows_not_applicable") or [])
        check("the item names exactly the suites the tier excludes",
              _named == _buckets["excluded"], sorted(_named ^ _buckets["excluded"]))
        check("...and exactly the ones it cannot run here",
              _na == _buckets["not_applicable"], sorted(_na ^ _buckets["not_applicable"]))
        check("...and the two reasons do not overlap (red here vs cannot run here)",
              not (_named & _na), sorted(_named & _na))
        # A suite in the "cannot run here" list must SAY so: run_all.py counts a skip as red, so
        # excluding one is only honest when the suite itself declares the platform it cannot grade
        # (tests/test_update_script.py runs update.sh, a POSIX shell script). A name that fails on
        # Windows without declaring anything belongs in `excluded` instead.
        _undeclared = sorted(n for n in _na
                             if 'os.name != "posix"' not in (BASE / n).read_text(encoding="utf-8"))
        check("every 'cannot run here' suite declares its platform",
              _na and not _undeclared, _undeclared)
        _priv = set((json.loads((BASE / "maintenance" / "product-manifest.json")
                                .read_text(encoding="utf-8")).get("private_suites") or {}))
        _priv = {"tests/%s.py" % k for k in _priv}
        check("...and every one of them SHIPS (a private suite is not in the product at all)",
              not ((_named | _na) & _priv), sorted((_named | _na) & _priv))
        # An exclusion is live work, never a settled one: the item stays open while a name is
        # listed. With the list empty, the item is the record of what was hidden and why it came
        # back - and the Windows job is green with nothing excluded.
        check("an exclusion stays live work (the item is open while one is listed)",
              _item.get("state") == "open" if _named else True, _item.get("state"))

    print("\n%s" % ("all contract checks passed" if not FAILS
                    else "FAILED: %d" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
