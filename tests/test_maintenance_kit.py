"""Every script in maintenance/ is classified somewhere, and no name is in two classes.

Three lists govern that folder and nothing kept them in agreement:

  * SHIPPED - build-package.py's ALLOWED_MAINTENANCE: what a package may carry (the
    restart helpers). Anything else in maintenance/ is refused at build time.
  * DROPPED - tinycmdr.py's _DEV_MAINTENANCE_DROP: what an update deletes from a tree
    that is both a checkout and an install.
  * HOST-OWNED - the files this fleet keeps for itself (private_rules.py, a roles file):
    never shipped, never deleted.

Measured 2026-10-07: the folder held 29 files and 10 of them were in no list at all, so a
mixed tree kept them after an update while the drop note claimed the kit was gone. The
lists are name-based on purpose (a rule like "anything not shipped" would delete a host's
own files), so the fix is this check rather than a derivation.

    python tests/test_maintenance_kit.py

Falsification: with the pre-fix 15-name drop list this suite names the ten unclassified
scripts (check-hygiene.py, package_assets.py, probe-web-*.py, stub-openai-endpoint.py,
wait-for-endpoint.py, ...) and exits 1.
"""
import ast
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MAINT = BASE / "maintenance"

# Never shipped, never dropped: this fleet's own inventory, plus any roles file a host
# keeps beside it (both are gitignored, so they exist only on a box that has one).
HOST_OWNED = {"private_rules.py", "where-roles.json", "where-roles.example.json"}

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % detail))
    if not ok:
        FAILS.append(what)


def literal_assignment(path, name):
    """The value of a module-level `<name> = <literal>` in `path`, read as a literal.

    Read from the source rather than by importing: tinycmdr.py imports the world, and
    build-package.py refuses to import without the private inventory. A tuple/set literal
    is what these two are, and the AST says so exactly.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("%s carries no %s assignment" % (path.name, name))


def main():
    shipped = set(literal_assignment(BASE / "maintenance" / "build-package.py",
                                     "ALLOWED_MAINTENANCE"))
    dropped = set(literal_assignment(BASE / "tinycmdr.py", "_DEV_MAINTENANCE_DROP"))
    check("the three restart helpers are the shipped set",
          shipped == {"restart-tinycmdr.sh", "restart-tinycmdr.ps1",
                      "restart-tinycmdr-macos.sh"}, sorted(shipped))

    on_disk = {p.name for p in MAINT.iterdir()
               if p.is_file() and p.suffix != ".pyc" and p.name != "__pycache__"}
    check("the folder holds scripts to classify", len(on_disk) > 10, len(on_disk))

    classes = {"shipped": shipped, "dropped": dropped, "host-owned": HOST_OWNED}
    unclassified = sorted(on_disk - set().union(*classes.values()))
    check("every file in maintenance/ is classified", not unclassified,
          "in no list: %s" % ", ".join(unclassified))

    for a, b in (("shipped", "dropped"), ("shipped", "host-owned"),
                 ("dropped", "host-owned")):
        both = sorted(classes[a] & classes[b])
        check("no name is both %s and %s" % (a, b), not both, ", ".join(both))

    # Only SHIPPED and DROPPED have to be present here. The host-owned names (private_rules.py,
    # a roles file) are gitignored, so a clean clone never has them: naming one is the point,
    # not drift. The first version of this check subtracted nothing and failed on its first run
    # in a clone that had no private_rules.py.
    stale = sorted((shipped | dropped) - on_disk)
    check("no shipped or dropped name is missing from the tree", not stale, ", ".join(stale))

    # ---- the public/private split (maintenance/product-manifest.json) ------------------
    # The product tree is generated from this file, so a file or a suite that is in neither list
    # would silently stop shipping (or silently start). Both directions are refused here.
    man_path = BASE / "maintenance" / "product-manifest.json"
    check("the product manifest is in the tree", man_path.is_file(), str(man_path))
    if man_path.is_file():
        man = json.loads(man_path.read_text(encoding="utf-8"))
        private = man.get("private_suites") or {}
        suites = {p.stem for p in (BASE / "tests").glob("test_*.py")}
        unlisted = sorted(suites - set(private))
        check("every suite is either shipped or declared private (with a reason)",
              True, "")                                   # shipped ones need no entry
        missing_reason = sorted(k for k, v in private.items() if not str(v).strip())
        check("every private suite says why it is private", not missing_reason,
              ", ".join(missing_reason))
        ghost = sorted(k for k in private if k not in suites)
        check("no private entry names a suite that is not there", not ghost, ", ".join(ghost))
        absent = sorted(r for r in man.get("extra", [])
                        if not (BASE / r).exists() and r != "field-notes.md")
        check("every extra the manifest ships exists here", not absent, ", ".join(absent))
        print("note  %d of %d suites ship publicly; %d stay here"
              % (len(suites - set(private)), len(suites), len(private)))
    print()

    if FAILS:
        print("\n%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("\nall maintenance-kit checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
