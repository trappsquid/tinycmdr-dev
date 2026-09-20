"""Read a tinycmdr.log and report what it can be measured from, without touching the bot.

    python maintenance/fleet-metrics.py <tinycmdr.log> [--jsonl out.jsonl] [--since YYYY-MM-DD]

Non-invasive by design: it only reads the log the harness already writes, so it can run
against any host, including one mid-run, and it changes no behaviour.

WHAT IT CAN SEE
    model calls      local vs cloud, chunk counts, first-delta seconds, tok/s, duration
    tools            name, call count, result size (median / p95 / max)
    runs             "carry ... (run N)" markers, or "plan derived ... N step(s)"
    operator         steering messages, stalls, catch-up sweeps, restart notices
    log health       ERROR / WARNING lines by category
    rework proxies   an edit_file whose path is read or edited again soon after, and
                     identical tool calls repeated three or more times in a row

WHAT IT CANNOT SEE (and why the next log line matters)
    The harness logs a tool result as "-> N chars" - the SIZE, never the outcome. So this
    script cannot tell a successful edit from a failed one, a clean run from one that
    retried five times, or a verify FAILED from a verify OK. Failure rates need one line
    in the harness logging the first ~120 chars of the result; until then, treat the
    rework proxies below as signals, not as measurements.
"""
import collections
import json
import re
import statistics
import sys
import time

LINE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (?P<level>\w+) "
    r"(?:\[(?P<chan>[^\]]+)\] )?(?P<body>.*)$")
TOOL = re.compile(r"^(?P<tool>[a-z_]+)\((?P<args>.*)\) -> (?P<res>\d+) chars\s*$")
STREAM = re.compile(r"^stream (?P<url>\S+): (?P<chunks>\d+) chunk\(s\), "
                    r"first delta (?P<fd>[\d.]+|n/a)s, (?P<tps>[\d.]+|\?) tok/s "
                    r"\(server\), (?P<secs>\d+)s\s*$")
ROUTING = re.compile(r"^routing model (?P<model>\S+) to (?P<url>\S+)")
PLAN = re.compile(r"^plan derived from the request: (?P<steps>\d+) step\(s\)")
CARRY = re.compile(r"^carry: (?P<chars>\d+) chars of earlier tool results ride along "
                   r"\(run (?P<run>\d+)\)")
STEER = re.compile(r"^steering from (?P<who>\S+)")
STARTUP = re.compile(r"^posted startup notice to (?P<chan>\S+) \(downtime (?P<down>\d+)s\)")
STALL = re.compile(r"^stall: ")
CATCHUP = re.compile(r"^catch-up sweep")
PATHARG = re.compile(r"\"path\": \"([^\"]+)\"")


def med(xs):
    return statistics.median(xs) if xs else 0


def p95(xs):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * 0.95))]


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    path = argv[1]
    jsonl = None
    since = None
    for i, a in enumerate(argv):
        if a == "--jsonl" and i + 1 < len(argv):
            jsonl = argv[i + 1]
        if a == "--since" and i + 1 < len(argv):
            since = argv[i + 1]

    tools = collections.Counter()
    sizes = collections.defaultdict(list)
    local, cloud = [], []
    routing = collections.Counter()
    errors = collections.Counter()
    counts = collections.Counter()
    per_day = collections.Counter()
    calls = []                      # ordered (tool, path-or-args) for rework proxies
    events = []
    unparsed = 0
    first = last = None
    n = 0

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            n += 1
            line = raw.rstrip("\r\n")
            if since and line[:10] < since:
                continue
            m = LINE.match(line)
            if not m:
                unparsed += 1
                continue
            ts, level, chan, body = (m.group("ts"), m.group("level"),
                                     m.group("chan") or "", m.group("body"))
            first = first or ts
            last = ts
            per_day[ts[:10]] += 1
            ev = {"ts": ts, "level": level, "chan": chan}
            mt = TOOL.match(body)
            if mt:
                tool = mt.group("tool")
                size = int(mt.group("res"))
                tools[tool] += 1
                sizes[tool].append(size)
                path_arg = PATHARG.search(mt.group("args"))
                calls.append((tool, path_arg.group(1) if path_arg else mt.group("args")[:80]))
                ev.update(kind="tool", tool=tool, res=size,
                          path=path_arg.group(1) if path_arg else None)
            else:
                ms = STREAM.match(body)
                if ms:
                    url = ms.group("url")
                    rec = {"url": url, "chunks": int(ms.group("chunks")),
                           "fd": None if ms.group("fd") in ("n/a", "") else float(ms.group("fd")),
                           "tps": None if ms.group("tps") == "?" else float(ms.group("tps")),
                           "secs": int(ms.group("secs"))}
                    (local if ("8081" in url or "10.0.0." in url) else cloud).append(rec)
                    ev.update(kind="stream", **rec)
                else:
                    mr = ROUTING.match(body)
                    if mr:
                        routing[mr.group("model")] += 1
                        ev.update(kind="routing", model=mr.group("model"))
                    elif PLAN.match(body):
                        counts["plans"] += 1
                        ev.update(kind="plan", steps=int(PLAN.match(body).group("steps")))
                    elif CARRY.match(body):
                        counts["runs"] += 1
                        ev.update(kind="carry", run=int(CARRY.match(body).group("run")))
                    elif STEER.match(body):
                        counts["steering"] += 1
                        ev.update(kind="steering")
                    elif STARTUP.match(body):
                        counts["restarts"] += 1
                        ev.update(kind="startup")
                    elif STALL.match(body):
                        counts["stalls"] += 1
                        ev.update(kind="stall")
                    elif CATCHUP.match(body):
                        counts["catchup"] += 1
                        ev.update(kind="catchup")
                    elif level in ("ERROR", "WARNING"):
                        cat = ("websocket" if "websocket" in body or "WSMessage" in body else
                               "llm endpoint" if "LLM endpoint" in body or "8081" in body else
                               "other")
                        errors[cat] += 1
                        ev.update(kind="error", cat=cat, text=body[:120])
                    else:
                        ev.update(kind="info")
            events.append(ev)

    # rework proxies
    read_after_edit = 0
    for i, (tool, key) in enumerate(calls):
        if tool == "edit_file":
            window = calls[i + 1:i + 4]
            if any(t in ("read_file", "edit_file") and k == key for t, k in window):
                read_after_edit += 1
    repeats = 0
    run_len = 0
    for i in range(1, len(calls)):
        if calls[i] == calls[i - 1]:
            run_len += 1
            if run_len == 2:
                repeats += 1
        else:
            run_len = 0

    print("log        %s" % path)
    print("window     %s .. %s   (%d lines read, %d unparsed)"
          % (first, last, n, unparsed))
    print("model      local %d calls | cloud %d calls"
          % (len(local), len(cloud)))
    if local:
        print("  local    median first delta %.1fs, median %.1f tok/s, median %ds/call"
              % (med([r["fd"] for r in local if r["fd"]]),
                 med([r["tps"] for r in local if r["tps"]]),
                 med([r["secs"] for r in local])))
    if cloud:
        print("  cloud    median first delta %.1fs, median %ds/call"
              % (med([r["fd"] for r in cloud if r["fd"]]),
                 med([r["secs"] for r in cloud])))
    print("routing    %s" % (dict(routing.most_common(4)) or "none logged"))
    print("tools      %d results over %d distinct tools" % (sum(tools.values()), len(tools)))
    for tool, cnt in tools.most_common(8):
        print("  %-14s %5d calls   median %6d chars   p95 %7d   max %7d"
              % (tool, cnt, med(sizes[tool]), p95(sizes[tool]), max(sizes[tool])))
    print("runs       carry markers %d | plan lines %d" % (counts["runs"], counts["plans"]))
    print("operator   steering %d | stalls %d | restarts %d | catch-up %d"
          % (counts["steering"], counts["stalls"], counts["restarts"], counts["catchup"]))
    print("errors     %s" % (dict(errors) or "none"))
    print("rework     edit followed by re-read/re-edit of the same path: %d of %d edits"
          % (read_after_edit, tools.get("edit_file", 0)))
    print("           identical call repeated 3+ times in a row: %d places" % repeats)
    print("per day    %s" % " ".join("%s:%d" % kv for kv in sorted(per_day.items())[-5:]))

    if jsonl:
        with open(jsonl, "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        print("jsonl      %d events -> %s" % (len(events), jsonl))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
