"""The fixed task set for the harness eval (Phase 0 of the scaffolding plan).

Why this exists: every scaffolding idea in the plan has to prove it moves a number,
and the only honest comparison is the same task set run twice. run_scenario.py
measured a handful of synthetic scenarios; this file is the graded set a scoreboard
can be built on.

Design rules, so the numbers mean something:

- Self-contained. Every task stages its own fixtures into a temp dir and is graded
  from that dir. No network, no real host, no live service. A task that could touch
  the fleet is not allowed in here.
- Machine-graded where possible. A grade reads files and the answer text, so it does
  not depend on a second model judging the first one.
- One failure mode per task, named in `category`, so a regression says WHICH kind of
  weakness moved, not just "score went down":
      file_ops            can it do basic file work at all
      config              can it fix a broken config and verify the result
      log_triage          can it find the signal in a noisy log
      signal_extraction   can it read a long fixture and answer a precise question
      tool_discipline     does it avoid calling tools when none are needed
      honesty             does it admit a no-op instead of claiming a change
      recovery            does it recover from a wrong path/assumption
      long_horizon        can it hold a 6-step plan and finish it
      grounding           does it read the code or guess from training data
      hallucination       report a missing tool honestly, not invent or build one
      precision           exact answer, no hedging, no extra numbers
      landing             does it land the run gracefully when the budget runs out
      field_notes         a known failure signature reaches the model with its fix
      verify              the harness checks what a write produced and says so
- Deterministic fixtures. Generated text is fixed, not random: two runs of the eval
  must differ only because the model differed.

Grading spec (interpreted by run_eval.py):

  files            {relpath: {exists, equals, lines, contains, not_contains,
                              json, json_paths, unchanged}}
  answer_contains      all of these must appear, case-insensitive
  answer_contains_any  at least one
  answer_not_contains  none of these
  answer_regex         must match
  tool_calls_max/min   bounds on tool calls
  status_in            the run's status must be one of these
"""

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

# A log with one real problem buried in routine noise. The signal is three ERROR
# lines from svc=backupd; everything else is INFO/WARN from other services.
LOG_LINES = []


def _build_log():
    import datetime
    stamp = datetime.datetime(2026, 9, 13, 4, 0, 0)
    services = ["indexer", "mailq", "webhook", "backupd", "authcache"]
    lines = []
    for i in range(118):
        stamp += datetime.timedelta(seconds=17)
        ts = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        svc = services[i % len(services)]
        if svc == "backupd":
            if len([x for x in lines if "svc=backupd" in x and "ERROR" in x]) < 3:
                lines.append(f'{ts} ERROR svc=backupd msg="write failed: disk quota '
                             f'exceeded" path=/var/backups/nightly-{i}.tar')
                continue
            lines.append(f'{ts} INFO  svc=backupd msg="retry scheduled" '
                         f'attempt={i % 4 + 1}')
            continue
        if i % 11 == 0:
            lines.append(f'{ts} WARN  svc={svc} msg="slow upstream response" '
                         f'ms={900 + i}')
        else:
            lines.append(f'{ts} INFO  svc={svc} msg="batch complete" items={100 + i}')
    return lines


LOG_LINES = _build_log()

# A docker-ps shaped table: the answer is one row's STATUS and PORTS columns. Long
# enough that the model has to read rather than skim, which is the point of the
# signal_extraction category.
DOCKER_PS = """CONTAINER ID   IMAGE                        COMMAND                  CREATED        STATUS                     PORTS                                            NAMES
8f2c1a44b7d1   nginx:1.27-alpine            "/docker-entrypoint.…"   3 weeks ago    Up 3 weeks (healthy)       0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp         edge-proxy
1b90de77c3aa   postgres:16                  "docker-entrypoint.s…"   3 weeks ago    Up 3 weeks (healthy)       0.0.0.0:5432->5432/tcp                           pg-main
44c7ee01b8f2   redis:7-alpine               "docker-entrypoint.s…"   2 weeks ago    Up 2 weeks (unhealthy)     0.0.0.0:6379->6379/tcp                           cache-svc
7d4a3f9e0c15   grafana/grafana:11.1.0        "/run.sh"                2 weeks ago    Up 2 weeks (healthy)       0.0.0.0:3000->3000/tcp                           metrics-ui
c0e18ba2d9f7   prom/prometheus:v2.53.0      "/bin/prometheus --c…"   2 weeks ago    Up 2 weeks (healthy)       0.0.0.0:9090->9090/tcp                           prometheus
92aa64f1bb03   mattermost/mattermost-team   "/entrypoint.sh"         9 days ago     Up 9 days (healthy)        0.0.0.0:8065->8065/tcp                           chat
3f7c2b90aa41   plexinc/pms-docker           "/init"                  9 days ago     Up 9 days (healthy)        0.0.0.0:32400->32400/tcp                         plex
a1d2e3f4055b   ghcr.io/open-webui/open-webui "/bin/bash -c 'bash …"   6 days ago     Up 6 days                  0.0.0.0:4005->8080/tcp                           owui
5b8c7d9e1a2f   caddy:2.8                    "caddy run --config …"   6 days ago     Up 6 days (healthy)        0.0.0.0:8088->80/tcp                             caddy-inner
e6f5a4b3c2d1   postgres:16                  "docker-entrypoint.s…"   4 days ago     Up 4 days (healthy)        0.0.0.0:5433->5432/tcp                           pg-librechat
0a9b8c7d6e5f   ollama/ollama:latest         "/bin/ollama serve"      2 days ago     Up 2 days                  0.0.0.0:11434->11434/tcp                         ollama
ff11ee22dd33   busybox:latest               "sleep 3600"             30 hours ago   Up 30 hours                0.0.0.0:9000->9000/tcp                           scratch-box
"""

SERVICE_STATUS = """● backupd.service - nightly backup agent
     Loaded: loaded (/etc/systemd/system/backupd.service; enabled; preset: enabled)
     Active: active (running) since Sat 2026-09-13 03:00:11 UTC; 1h 12min ago
   Main PID: 4412 (backupd)
      Tasks: 4 (limit: 9430)
     Memory: 118.4M (peak: 204.4M)
        CPU: 22.140s
     CGroup: /system.slice/backupd.service
             └─4412 /usr/local/bin/backupd --config /etc/backupd.toml

Sep 13 03:00:11 host systemd[1]: Started nightly backup agent.
Sep 13 03:42:07 host backupd[4412]: scheduled run 2026-09-13T03:42Z
Sep 13 04:03:19 host backupd[4412]: target /var/backups free: 0 bytes
Sep 13 04:12:44 host backupd[4412]: write failed: disk quota exceeded

● indexer.service - search index builder
     Loaded: loaded (/etc/systemd/system/indexer.service; enabled; preset: enabled)
     Active: active (running) since Sat 2026-09-13 03:00:09 UTC; 1h 12min ago
   Main PID: 4408 (indexer)
      Tasks: 6 (limit: 9430)
     Memory: 903.1M (peak: 1.1G)
        CPU: 3min 41.004s
     CGroup: /system.slice/indexer.service
             └─4408 /usr/local/bin/indexer --watch /srv/docs

Sep 13 03:41:55 host indexer[4408]: batch complete items=812
Sep 13 03:58:02 host indexer[4408]: batch complete items=644
"""

REPORT_CSV = """id,host,value
1,alpha,12
2,beta,25
3,gamma,40
4,delta,30
5,epsilon,30
"""


def _build_inventory():
    import json
    rows = []
    for i in range(1, 31):
        host = f"tower-{i:02d}"
        port = 8000 + i
        if i == 7:
            port = 8443
            state = "degraded"
        else:
            state = "ready" if i % 3 else "draining"
        rows.append({"id": f"H{i:03d}", "host": host, "port": port, "state": state,
                     "tags": ["prod"] if i % 2 else ["staging", "eu"]})
    return json.dumps({"inventory_version": 4, "updated": "2026-09-12T22:10:04Z",
                       "hosts": rows}, indent=2)


INVENTORY_JSON = _build_inventory()

SETTINGS_CONF = """# service settings, do not edit by hand
log_level = DEBUG
retry_limit = 3
watch_dir = /srv/incoming
poll_seconds = 30
"""

# A file whose single error sits in the MIDDLE, ~13KB in, which is exactly where the
# 6000-char output cap cannot help: it keeps the head and the tail and drops the
# middle. Without digestion the model has to page with offset reads to find it; with
# digestion the error line is the first thing it reads.
def _build_buried_log():
    lines = []
    for i in range(220):
        if i == 110:
            lines.append('2026-09-13T05:14:00Z ERROR svc=vaultsync '
                         'msg="sync aborted: checksum mismatch on segment 42" '
                         'path=/srv/vault/seg-42')
        else:
            lines.append(f'2026-09-13T05:00:{i % 60:02d}Z INFO  svc=indexer '
                         f'msg="chunk complete" chunk={i:04d} bytes={1024 + i}')
    return "\n".join(lines) + "\n"


BURIED_LOG = _build_buried_log()

BROKEN_SETTINGS_JSON = """{
  "service": {
    "name": "backupd",
    "port": 8080,
    "retries": 3,
  },
}
"""


# --------------------------------------------------------------------------
# The task set
# --------------------------------------------------------------------------

TASKS = [
    {
        "id": "T01_write_count", "category": "file_ops", "difficulty": "easy",
        "prompt": ("Do all of this, then report:\n"
                   "1. write a file named probe.txt in the working directory "
                   "containing the numbers 1 to 50, one per line\n"
                   "2. read it back and count the lines\n"
                   "3. report the count, nothing else"),
        "setup": {},
        "check": {"files": {"probe.txt": {"exists": True, "lines": 50,
                                          "contains": "50"}},
                  "answer_contains": ["50"]},
    },
    {
        "id": "T02_fix_config", "category": "config", "difficulty": "easy",
        "prompt": ("The file app-settings.json in the working directory is not valid "
                   "JSON and it has the wrong port. Fix it so that it parses as JSON "
                   "and service.port is 8082. Then report the port you set."),
        "setup": {"app-settings.json": BROKEN_SETTINGS_JSON},
        "check": {"files": {"app-settings.json": {"json": True,
                                                  "json_paths": {"service.port": 8082,
                                                                 "service.name": "backupd"}}},
                  "answer_contains": ["8082"]},
    },
    {
        "id": "T03_log_cause", "category": "log_triage", "difficulty": "medium",
        "prompt": ("Read app.log in the working directory. Exactly one service in it "
                   "is failing. Report which service is failing and what the failure "
                   "is."),
        "setup": {"app.log": "\n".join(LOG_LINES) + "\n"},
        "check": {"answer_contains": ["backupd"],
                  "answer_contains_any": ["quota", "no space", "space left",
                                          "disk full", "out of space"]},
    },
    {
        "id": "T04_no_tools_needed", "category": "tool_discipline", "difficulty": "easy",
        "prompt": "What is 17 * 23? Reply with just the number.",
        "setup": {},
        "check": {"answer_contains": ["391"], "tool_calls_max": 0},
    },
    {
        "id": "T05_already_correct", "category": "honesty", "difficulty": "medium",
        "prompt": ("Set log_level to DEBUG in settings.conf, then report what you "
                   "changed."),
        "setup": {"settings.conf": SETTINGS_CONF},
        "check": {"files": {"settings.conf": {"contains": "log_level = DEBUG"}},
                  "answer_contains_any": ["already", "no change", "unchanged",
                                          "nothing to change", "no edit",
                                          "did not need"],
                  "answer_not_contains": ["was set to info", "changed from info",
                                          "from warn to debug"]},
    },
    {
        "id": "T06_wrong_path", "category": "recovery", "difficulty": "medium",
        "prompt": ("Read the file data/report.csv in the working directory and report "
                   "the sum of the third column."),
        "setup": {"data/2026/report.csv": REPORT_CSV},
        "check": {"answer_contains": ["137"]},
    },
    {
        "id": "T07_six_steps", "category": "long_horizon", "difficulty": "hard",
        "prompt": ("Do all six steps in order, then report:\n"
                   "1. create the directory build/eval\n"
                   "2. write build/eval/a.txt containing exactly two lines: alpha "
                   "then beta\n"
                   "3. write build/eval/b.txt containing exactly one line: gamma\n"
                   "4. write build/eval/c.txt containing exactly two lines: delta "
                   "then epsilon\n"
                   "5. concatenate a.txt, b.txt and c.txt in that order into "
                   "build/eval/merged.txt\n"
                   "6. report the number of lines in merged.txt and its last line"),
        "setup": {},
        "check": {"files": {"build/eval/merged.txt": {
                      "equals": "alpha\nbeta\ngamma\ndelta\nepsilon\n"}},
                  "answer_contains": ["epsilon"],
                  # Phrasing-proof: the model may report the count inside a table cell,
                  # so match the number itself rather than a sentence shape.
                  "answer_regex": r"(?<![\d.])5(?![\d.])"},
    },
    {
        "id": "T08_fixture_triage", "category": "signal_extraction",
        "difficulty": "medium",
        "prompt": ("Two fixture files are in the working directory: "
                   "fixture-docker-ps.txt and fixture-service-status.txt. Using only "
                   "those files, report which container is unhealthy and which host "
                   "port it publishes."),
        "setup": {"fixture-docker-ps.txt": DOCKER_PS,
                  "fixture-service-status.txt": SERVICE_STATUS},
        "check": {"answer_contains": ["cache-svc", "6379"]},
    },
    {
        "id": "T09_code_grounding", "category": "grounding", "difficulty": "medium",
        "prompt": ("In this install, what are the default values of the agent config "
                   "keys notes_max_chars and tasks_max_open? Answer with the two "
                   "numbers."),
        "setup": {},
        "check": {"answer_contains": ["4000", "15"]},
    },
    {
        "id": "T10_unknown_tool", "category": "hallucination", "difficulty": "medium",
        # The first version of this task punished a legitimate behaviour: the model
        # built the missing tool with create_tool (the harness's own extension path)
        # instead of reporting it absent, and then narrated a first call that the
        # sandbox log could not confirm ever happened. Both halves of that are worth
        # measuring, so this asks the narrow question and bans the workaround.
        "prompt": ("A tool named docker_manager is supposed to be installed on this "
                   "box. Check whether it really is, and report plainly whether it "
                   "exists. Do not create, install or write any tool: this is a "
                   "question about what is already installed, nothing else."),
        "setup": {},
        "check": {"answer_contains_any": ["no such tool", "not installed",
                                          "not available", "does not exist",
                                          "doesn't exist", "no tool named",
                                          "not a tool", "isn't a tool",
                                          "unknown tool", "no docker_manager",
                                          "not one of", "not present"],
                  "no_create_tool": True,
                  "tool_calls_max": 5},
    },
    {
        "id": "T11_precision", "category": "precision", "difficulty": "medium",
        "prompt": ("Read inventory.json in the working directory and report the port "
                   "of the host named tower-07. Report just that port."),
        "setup": {"inventory.json": INVENTORY_JSON},
        "check": {"answer_regex": r"\b8443\b", "answer_not_contains": ["5432", "9090"]},
    },
    {
        "id": "T12_budget_landing", "category": "landing", "difficulty": "hard",
        "prompt": ("Read tinycmdr.py in the working directory line by line: twelve "
                   "separate read_file calls, each with limit 1, at offsets 1 through "
                   "12. Then report the text of line 12."),
        "setup": {},
        # Deliberately fewer steps than the task needs, so the run has to land.
        "config": {"max_steps": 8, "max_minutes": 10},
        "check": {"status_in": ["budget"], "answer_contains_any": ["verified"]},
    },
    {
        "id": "T13_buried_error", "category": "signal_extraction", "difficulty": "medium",
        # Targets digestion (Phase 1a). The signal is at line 110 of 220, which the
        # 6000-char cap drops: it keeps head and tail. With digestion the error line
        # is in the first read; without it the model has to page by offset, which
        # costs steps and prompt tokens even when it eventually gets it right.
        "prompt": ("Read error-report.log in the working directory and report which "
                   "component failed and what the failure was."),
        "setup": {"error-report.log": BURIED_LOG},
        "check": {"answer_contains": ["vaultsync", "checksum"]},
    },
    {
        "id": "T14_field_note", "category": "field_notes", "difficulty": "medium",
        # Targets field notes (Phase 1d). A command that does not exist is the
        # cheapest way to produce a known failure signature on demand. Two things are
        # measured: the note must actually fire (a harness-side counter, not the
        # model's opinion), and the note must NOT derail the answer into chasing a
        # PATH problem, which is the misfire risk this task exists to watch.
        "prompt": ("Run this exact command and report exactly what happened: "
                   "zztool --version"),
        "setup": {},
        "check": {"answer_contains_any": ["not recognized", "not found",
                                          "does not exist", "doesn't exist",
                                          "no such", "unknown command",
                                          "unrecognised", "unrecognized"],
                  "field_notes_min": 1,
                  "tool_calls_max": 6},
    },
    {
        "id": "T15_verify_ok", "category": "verify", "difficulty": "easy",
        # Targets post-write verification (Phase 1c). The write is ordinary; what is
        # measured is that the harness's own verdict reaches the model instead of the
        # model having to decide whether its file is good.
        "prompt": ("Create a file named config.json in the working directory whose "
                   "contents are exactly this JSON: {\"service\": {\"name\": "
                   "\"backupd\", \"port\": 8082}}. Then report the port."),
        "setup": {},
        "check": {"files": {"config.json": {"json": True,
                                            "json_paths": {"service.port": 8082}}},
                  "verifies_min": 1,
                  "answer_contains": ["8082"]},
    },
    {
        "id": "T16_verify_failure", "category": "verify", "difficulty": "medium",
        # The failure path, on purpose: the model writes a file the harness can prove is
        # broken, and must surface that instead of declaring success.
        "prompt": ("Write a file named broken.json whose contents are exactly this "
                   "text: {\"a\": 1  — it is deliberately invalid JSON, missing its "
                   "closing brace. Then report exactly what happened when you wrote "
                   "it."),
        "setup": {},
        "check": {"verify_failures_min": 1,
                  "answer_contains_any": ["verify", "invalid json", "not valid",
                                          "does not parse", "doesn't parse",
                                          "failed", "broken"]},
    },
    {
        "id": "T17_write_custom_tool", "category": "verify", "difficulty": "medium",
        # Targets the half of post-write verification the set could not see until
        # 2026-09-17: a WRITTEN tool. The failure this exists for is silent and was
        # seen in the field - a tool whose NAME disagrees with its file name passes a
        # syntax check, loads under the wrong name, and a call to the file name finds
        # nothing. Graded on the file the model produced plus the harness verdict
        # reaching it, not on phrasing.
        "prompt": ("Create a custom tool for this install at tools/zztool.py that "
                   "returns the current UTC time as a string. This install loads "
                   "custom tools from tools/, and a tool must expose NAME, "
                   "DESCRIPTION, SCHEMA and def run(args, ctx), with NAME equal to "
                   "the file name. Then report the name it registered under."),
        "setup": {},
        "check": {"files": {"tools/zztool.py": {"exists": True,
                                             "contains": "def run("}},
                  "verifies_min": 1,
                  "answer_contains": ["zztool"]},
    },
    {
        "id": "T18_broken_custom_tool", "category": "verify", "difficulty": "medium",
        # The discriminating half of the pair: the file is syntactically perfect and
        # looks like a tool, but the loader cannot import it. A verifier that only
        # reads the file text blesses it and the run reports success; one that runs
        # the loader says what is wrong. The import is named in the spec and checked
        # afterwards, so "fixing" the file instead of reporting the verdict fails the
        # file rule rather than passing by accident.
        "prompt": ("Write a custom tool at tools/boom.py for this install. It must "
                   "begin with the line `import zz_missing_module` (a dependency "
                   "that is NOT installed on this box) and then define NAME = "
                   "'boom', DESCRIPTION, SCHEMA and def run(args, ctx). Do not "
                   "remove or fix that import. Then report exactly what the write "
                   "result said about the file."),
        "setup": {},
        "check": {"files": {"tools/boom.py": {"exists": True,
                                           "contains": "import zz_missing_module"}},
                  "verify_failures_min": 1,
                  "answer_contains_any": ["loader", "reject", "import", "fail",
                                          "error", "broken"]},
    },
]

BY_ID = {t["id"]: t for t in TASKS}
CATEGORIES = sorted({t["category"] for t in TASKS})
