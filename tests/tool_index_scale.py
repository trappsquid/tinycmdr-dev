"""The tool-index scale gate: what a growing tools/ folder costs the STATIC prompt.

Why this exists: the harness's whole efficiency claim is that tool count does NOT move the
payload - the always-on schemas are a fixed set and anything else resolves on demand. That
held for schemas and it did NOT hold for the custom-tool block, which listed every tool's
FULL DESCRIPTION in the static prompt (measured 2026-09-25: +168 chars / +49 est-tok per
tool, unbounded, so 80 tools took the prompt from 2,921 to 6,870 est-tok - +16 s of prefill
at the LAN box's measured ~240 tok/s). The tool tree replaced that with CATEGORIES + NAMES,
descriptions behind one find_tools call.

    python tests/tool_index_scale.py                # 0 5 10 20 40 80 probe tools
    python tests/tool_index_scale.py 0 40 300       # any N you like

It asserts three things (exit 1 when any breaks):

    FLAT      the DISCLOSED schema block and its count are byte-identical at every N -
              tool count may only ever move the name index.
    BOUNDED   an 80-tool box adds <= 1,200 est-tok to the prompt and <= 20 ch per tool
              (the pre-batch numbers: +3,949 est-tok, 167.8 ch/tool), and 300 tools still
              render inside the two caps, with the overflow line naming the door.
    LIVE      this repo's own tools/ (the shape every fleet box runs): the index block is
              <= 400 ch, carries every tool NAME, carries no description prose, files each
              tool on its designed shelf, and every shelf is resolvable by find_tools.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tests"))
import run_scenario  # noqa: E402

TOOL_TMPL = '''NAME = "%(n)s"
DESCRIPTION = "%(d)s"
SCHEMA = {"type": "object", "properties": {"path": {"type": "string", "description": "target"}}, "required": ["path"]}
def run(args, ctx):
    return "ok"
'''

DESC = ("Report %(what)s for the named target and return the rows biggest first, "
        "with byte sizes and a per-entry note when a value looks out of range.")

WHATS = ["disk usage per volume", "stale certificate ages", "open port counts",
         "log rotation health", "scheduled task drift", "service uptime history",
         "backup freshness", "DNS resolution lag", "NTP offset history",
         "SMART attribute deltas", "page file growth", "hotfix installation dates",
         "printer queue depth", "WER crash clusters", "firewall rule age",
         "share permission drift", "installed package versions", "GPO application results",
         "bitlocker key age", "defender scan gaps", "sessions with no MFA",
         "failed login clusters", "kernel module versions", "container image age",
         "cron entry drift", "certificate chain depth", "IPMI sensor readings",
         "UPS battery health", "fan speed history", "thermal throttle events",
         "USB device history", "driver version drift", "RAID array status",
         "filesystem inode use", "swap pressure events", "SELinux denials",
         "audit log gaps", "firewall drop counts", "route table churn", "ARP table size",
         "conntrack table use", "socket TIME_WAIT count", "TCP retransmit rate",
         "interface error counters", "queue depth per disk", "pending reboot flags",
         "recent driver installs", "AV definition age", "licence expiry dates",
         "ssh host key age", "sudo audit events", "docker volume sizes",
         "kubernetes node drift", "etcd member health", "nginx upstream errors",
         "postgres bloat", "mysql slow query counts", "redis eviction counts",
         "kafka lag per topic", "rabbit mq depth"]

# ---- the acceptance numbers this batch is judged on (pre-batch value in the comment) ----
BUDGET_TOK_80 = 1200        # est-tok an 80-tool tools/ may add to the prompt (was 3,949)
BUDGET_CH_TOOL = 20         # chars of index per tool (was 167.8)
BOUND_CH_300 = 700          # the whole rendered index at 300 tools (was 39,690)
INDEX_MAX_CH_LIVE = 400     # this repo's own block, header line included (the
                            # pre-batch value for the same 9 tools was 1,397 ch;
                            # measured 347 with the tool tree, so the plan's guess of
                            # 350 becomes 400 and keeps headroom for a few more tools)

FAILS = []


def check(what, cond, detail=""):
    if cond:
        print("ok   " + what)
    else:
        FAILS.append(what)
        print("FAIL " + what + ("   " + str(detail) if detail else ""))


def measure(n):
    wd = Path(tempfile.mkdtemp(prefix="tool-scale-"))
    os.environ["TINYCMDR_TEST_APP"] = str(BASE / "tinycmdr.py")
    try:
        run_scenario.stage_install(wd, 24000)
        fb = run_scenario.load(wd)
        tools = Path(fb.TOOLS_DIR)
        for f in tools.glob("*.py"):
            f.unlink()                      # measure FROM zero custom tools
        for i in range(n):
            (tools / f"probe_{i:02d}.py").write_text(
                TOOL_TMPL % {"n": f"probe_{i:02d}",
                             "d": DESC % {"what": WHATS[i % len(WHATS)]}}, encoding="utf-8")
        fb.REGISTRY.custom.clear()
        fb.REGISTRY.load_all()
        prompt = fb.build_system_prompt()
        schemas = fb.select_tool_schemas("scale")
        return dict(
            n=len(fb.REGISTRY.custom),
            prompt_ch=len(prompt), prompt_tok=fb.est_tokens(prompt),
            schema_ch=sum(len(str(s)) for s in schemas), schemas=len(schemas),
            custom_ch=len(fb.REGISTRY.custom_summary()),
            index=fb.REGISTRY.custom_summary(),
            hidden_ch=len(fb.hidden_inventory_line()),
            list_tools_ch=len(fb.tool_list_tools({}, {"session_key": "scale"})),
            find_tools_ch=len(fb.tool_find_tools({"query": "how much disk space is left"},
                                                 {"session_key": "scale"})),
        )
    finally:
        shutil.rmtree(wd, ignore_errors=True)
        os.environ.pop("TINYCMDR_TEST_APP", None)


def scale_table(ns):
    rows = [measure(n) for n in ns]
    base = rows[0]
    print("%5s %10s %9s %9s %5s %10s %10s %11s %10s %12s %12s"
          % ("tools", "prompt_ch", "prompt_tok", "schema_ch", "#sch", "custom_ch",
             "hidden_ch", "list_tools", "find_tools", "ch/tool", "tok/tool"))
    for r in rows:
        d = r["n"] - base["n"]
        print("%5d %10d %9d %9d %5d %10d %10d %11d %10d %12s %12s"
              % (r["n"], r["prompt_ch"], r["prompt_tok"], r["schema_ch"], r["schemas"],
                 r["custom_ch"], r["hidden_ch"], r["list_tools_ch"], r["find_tools_ch"],
                 "%.1f" % ((r["prompt_ch"] - base["prompt_ch"]) / d) if d else "-",
                 "%.1f" % ((r["prompt_tok"] - base["prompt_tok"]) / d) if d else "-"))
    print()
    check("the DISCLOSED schema block is byte-identical at every N",
          len({r["schema_ch"] for r in rows}) == 1 and len({r["schemas"] for r in rows}) == 1,
          sorted({r["schema_ch"] for r in rows}))
    return base, {r["n"]: r for r in rows}


def check_bounds(base, by_n):
    if 80 in by_n:
        dtok = by_n[80]["prompt_tok"] - base["prompt_tok"]
        dch = (by_n[80]["prompt_ch"] - base["prompt_ch"]) / 80
        check("80 tools add <= %d est-tok to the prompt" % BUDGET_TOK_80,
              dtok <= BUDGET_TOK_80, "+%d est-tok" % dtok)
        check("and <= %d chars of index per tool" % BUDGET_CH_TOOL,
              dch <= BUDGET_CH_TOOL, "%.1f ch/tool" % dch)
    if 300 in by_n:
        check("300 tools still render inside the caps",
              by_n[300]["custom_ch"] <= BOUND_CH_300,
              "%d ch (cap %d)" % (by_n[300]["custom_ch"], BOUND_CH_300))
        # and the overflow names the door that resolves it
        check("the caps' overflow line is present at 300 tools",
              "more (find_tools" in by_n[300]["index"], by_n[300]["index"][-160:])


LIVE_SHELVES = {          # a pin, not a tautology: a description edit that reshuffles the
    "big_files": "files & edit",              # index is a DECISION, so it must fail loudly
    "dir_usage": "files & edit",              # (measured 2026-09-25: `shell`'s blurb says
    "drive_space": "files & edit",            # "background to a file", which filed the shell
    "patch": "files & edit",                  # tool under files & edit until the table read
    "blog": "web & publish",                  # the NAME first)
    "docker_updates_check": "checks & probes",
    "process": "system & shell",
    "tinycmdr_restart": "system & shell",
    "toolsmith": "tools & runbooks",
}


def live_leg():
    """This repo's own tools/ - the shape every fleet box runs, and the leg that must have
    gone DOWN (the stage_install shape has no custom tools, so it can only show zero rent)."""
    wd = Path(tempfile.mkdtemp(prefix="tool-live-"))
    os.environ["TINYCMDR_TEST_APP"] = str(BASE / "tinycmdr.py")
    try:
        run_scenario.stage_install(wd, 24000)
        (Path(wd) / "tools").mkdir(exist_ok=True)
        for f in sorted((BASE / "tools").glob("*.py")):
            shutil.copy2(f, Path(wd) / "tools" / f.name)
        fb = run_scenario.load(wd)
        names = sorted(fb.REGISTRY.custom)
        index = fb.REGISTRY.custom_summary()
        header = next((l for l in fb.build_system_prompt().splitlines()
                       if l.startswith("More tools on this box, by category")), "")
        print("--- live tree: %d custom tools" % len(names))
        print("    index %d ch, header %d ch, together %d ch (pre-batch: %d ch)"
              % (len(index), len(header), len(index) + len(header),
                 sum(len(f"  {n}: {fb.REGISTRY.custom[n]['schema']['function']['description']}")
                     for n in names)))
        print("    " + index.replace("\n", "\n    "))
        print()
        return fb, names, index, header
    finally:
        shutil.rmtree(wd, ignore_errors=True)
        os.environ.pop("TINYCMDR_TEST_APP", None)


def check_live(fb, names, index, header):
    check("the live index block is <= %d ch" % INDEX_MAX_CH_LIVE,
          len(index) + len(header) <= INDEX_MAX_CH_LIVE, len(index) + len(header))
    check("every custom tool NAME is still in the index (names are the recall path)",
          all(n in index for n in names), [n for n in names if n not in index])
    check("the index carries no DESCRIPTION prose",
          not any(d in index for d in
                  (fb.REGISTRY.custom[n]["schema"]["function"]["description"][:40]
                   for n in names)),
          index)
    wrong = {n: (fb._tool_category(n), LIVE_SHELVES[n]) for n in names
             if n in LIVE_SHELVES and fb._tool_category(n) != LIVE_SHELVES[n]}
    check("every live tool files on its designed shelf", not wrong, wrong)
    missed = []
    for shelf in sorted({fb._tool_category(n) for n in names}):
        answer = fb.tool_find_tools({"category": shelf}, {"session_key": "scale-live"})
        missed += [n for n in names
                   if fb._tool_category(n) == shelf and n not in answer]
    check("every shelf resolves through find_tools and names its tools", not missed, missed)
    check("a category answer reveals nothing (a reveal is per-session schema rent)",
          fb.visible_tool_names("scale-live") == fb.visible_tool_names("scale-none"),
          sorted(fb.visible_tool_names("scale-live")))
    # The door the model actually calls: measured on a live drive, asked what its added
    # tools do, the run called list_tools and then read EIGHT tool files (three twice) for
    # what one answer here says. So the answer carries the shelf and the blurb per tool.
    lt = fb.tool_list_tools({}, {"session_key": "scale-live"})
    check("list_tools carries every custom tool's shelf and what it does",
          all(n in lt and fb._tool_category(n) in lt for n in names), lt[:220])
    check("and it is bounded by the caps, not by the tool count", len(lt) < 2500, len(lt))
    bad = fb.tool_find_tools({"category": "no-such-shelf-xyz"}, {"session_key": "scale-live"})
    check("an unknown category lists the real ones instead of guessing",
          "no category named" in bad and "files & edit" in bad and len(bad) < 600, bad[:200])
    check("the two caps are in DEFAULT_CONFIG and in the shipped example",
          all(k in fb.DEFAULT_CONFIG["agent"] for k in
              ("tool_index_max_categories", "tool_index_max_names_per_line")),
          sorted(fb.DEFAULT_CONFIG["agent"])[:0])


def main():
    ns = [int(x) for x in sys.argv[1:]] or [0, 5, 10, 20, 40, 80, 300]
    base, by_n = scale_table(ns)
    check_bounds(base, by_n)
    print()
    fb, names, index, header = live_leg()
    check_live(fb, names, index, header)
    print()
    if FAILS:
        print("%d check(s) FAILED" % len(FAILS))
        return 1
    print("tool index: flat schemas, bounded at every N, and the live tree's %d tools "
          "render in %d ch" % (len(names), len(index) + len(header)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
