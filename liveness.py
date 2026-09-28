#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liveness.py — task liveness scanning for agent/automation systems.

Born from a real failure: an assistant had 14 reply-monitoring jobs and every
single one was silently paused. The rules existed. The jobs existed. Nobody
noticed the jobs were dead until weeks later — because *nothing* watches the
watchers.

This module answers one question: **which of my scheduled jobs are actually
alive, and which ones only look alive?**

Three failure shapes are distinguished:

  1. ran-but-failed    -> out of scope here (use your own logs/alerts)
  2. heartbeat-stopped -> the job ran once, then went quiet
  3. never-scheduled   -> the job exists on paper but the scheduler will
                          never fire it  (THE blind spot this tool covers)

Data sources (adapters):

  * generic-json  - any system that can export:
        {"tasks": [{"name": "...", "status": "ACTIVE|PAUSED|...",
                    "schedule_type": "recurring|once",
                    "rrule": "FREQ=...|null", "scheduled_at": "ISO|null",
                    "last_run": "ISO|epoch|epoch-ms|null",
                    "next_run": "ISO|null"}]}
  * workbuddy     - WorkBuddy desktop agent (sqlite db, read-only)

Exit codes:
  0 = all healthy
  1 = some jobs paused/inactive
  2 = CRITICAL jobs (monitors, reply-watchers, sentinels) are inactive
  3 = tool failure (never silently return 0 on failure)
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

EXIT_OK, EXIT_PAUSED, EXIT_CRITICAL, EXIT_FAIL = 0, 1, 2, 3

# Jobs whose names match these patterns are "critical": when they die, the
# damage is silent (missed replies, missed follow-ups). Bilingual on purpose.
DEFAULT_CRITICAL_PATTERNS = (
    # zh
    "监控", "回信", "跟进", "回复", "巡检", "哨兵", "兑现", "预警", "到货", "收件箱",
    # en
    "monitor", "watch", "follow-up", "followup", "reply", "heartbeat",
    "patrol", "sentinel", "inbox",
)


def classify(name, patterns=None):
    """Return True if the job name looks like a critical watcher job."""
    low = (name or "").lower()
    for p in (patterns or DEFAULT_CRITICAL_PATTERNS):
        if p.lower() in low:
            return True
    return False


def _norm_ts(v):
    """Normalize epoch (s/ms), ISO strings, or None into a display string."""
    if v in (None, ""):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            v = int(s)
        else:
            return s
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v)
    if n <= 0:
        return None
    if n > 10_000_000_000:  # epoch ms
        n /= 1000.0
    try:
        # Fixed UTC (not local tz): identical output on dev box and CI —
        # first CI run failed exactly because local-time rendering is
        # machine-dependent. Epoch timestamps render in UTC.
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return None


# ────────────────────────── adapters ──────────────────────────

def load_generic_json(path):
    """Load the universal JSON task format. Raises with a clear message."""
    if not os.path.exists(path):
        raise FileNotFoundError("task file not found: %s" % path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        raise ValueError("JSON must contain a top-level 'tasks' list")
    out = []
    for t in tasks:
        out.append({
            "name": t.get("name", "(unnamed)"),
            "status": (t.get("status") or "ACTIVE").upper(),
            "schedule_type": t.get("schedule_type") or "recurring",
            "schedule_desc": t.get("rrule") or t.get("scheduled_at") or "",
            "last_run": _norm_ts(t.get("last_run")),
            "next_run": _norm_ts(t.get("next_run")),
            "raw_last": t.get("last_run"),
        })
    return out


def workbuddy_db_path():
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "workbuddy.db")


def load_workbuddy(path=None):
    """Read-only scan of a WorkBuddy desktop-agent sqlite db."""
    path = path or workbuddy_db_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            "WorkBuddy db not found at %s — use --source generic-json instead" % path)
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(automations)")
        cols = {r[1] for r in cur.fetchall()}
        need = {"name", "status", "schedule_type", "rrule", "scheduled_at",
                "last_run_at", "next_run_at", "deleted_at"}
        missing = need - cols
        if missing:
            raise RuntimeError("schema changed; missing columns: %s"
                               % ", ".join(sorted(missing)))
        cur.execute("""
            SELECT name, status, schedule_type, rrule, scheduled_at,
                   last_run_at, next_run_at
            FROM automations WHERE deleted_at IS NULL ORDER BY status, name
        """)
        out = []
        for (name, status, stype, rrule, sat, last, nxt) in cur.fetchall():
            out.append({
                "name": name,
                "status": (status or "").upper(),
                "schedule_type": stype,
                "schedule_desc": rrule or sat or "",
                "last_run": _norm_ts(last),
                "next_run": _norm_ts(nxt),
                "raw_last": last,
            })
        return out
    finally:
        con.close()


def load(source, path=None):
    if source == "workbuddy":
        return load_workbuddy(path)
    return load_generic_json(path)


# ────────────────────────── analysis ──────────────────────────

def scan(tasks, patterns=None):
    """Classify every task; return a report dict."""
    items = []
    for t in tasks:
        items.append({
            "name": t["name"],
            "status": t["status"],
            "schedule": t.get("schedule_desc") or t.get("schedule_type", ""),
            "last_run": t.get("last_run"),
            "next_run": t.get("next_run"),
            "critical": classify(t["name"], patterns),
            "never_ran": t.get("raw_last") is None,
        })
    paused = [i for i in items if i["status"] != "ACTIVE"]
    critical_paused = [i for i in paused if i["critical"]]
    return {
        "total": len(items),
        "active": [i for i in items if i["status"] == "ACTIVE"],
        "paused": paused,
        "critical_paused": critical_paused,
        "items": items,
    }


def verdict_code(report):
    if report["critical_paused"]:
        return EXIT_CRITICAL
    if report["paused"]:
        return EXIT_PAUSED
    return EXIT_OK


def render(report):
    L = []
    L.append("=" * 66)
    L.append("Task Liveness Scan — who watches the watchers?")
    L.append("=" * 66)
    L.append("total jobs     : %d" % report["total"])
    L.append("active         : %d" % len(report["active"]))
    L.append("inactive       : %d   <- no notification was ever sent about these"
             % len(report["paused"]))
    L.append("critical dead  : %d   <- monitors/reply-watchers: silent damage"
             % len(report["critical_paused"]))
    L.append("-" * 66)
    if report["critical_paused"]:
        L.append("[P0] critical jobs currently dead:")
        for i in report["critical_paused"]:
            L.append("  x %-44s" % i["name"][:44])
            L.append("      schedule=%s | last_run=%s | never_ran=%s"
                     % (i["schedule"] or "?", i["last_run"] or "no record",
                        "yes" if i["never_ran"] else "no"))
    others = [i for i in report["paused"] if not i["critical"]]
    if others:
        L.append("")
        L.append("[P1] other inactive jobs (%d):" % len(others))
        for i in others:
            L.append("  o %s" % i["name"])
    if not report["paused"]:
        L.append("")
        L.append("OK: every job is ACTIVE.")
    L.append("-" * 66)
    code = verdict_code(report)
    L.append({
        EXIT_CRITICAL: "verdict: critical watchers are dead — report today, "
                       "but do NOT bulk-restart without a human decision.",
        EXIT_PAUSED:   "verdict: some jobs inactive — confirm whether the pause "
                       "was intentional; no audit trail = treat as incident.",
        EXIT_OK:       "verdict: healthy.",
    }[code])
    L.append("=" * 66)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Scan scheduled agent jobs for silent scheduler-level death.")
    ap.add_argument("--source", choices=("generic-json", "workbuddy"),
                    default="generic-json")
    ap.add_argument("--file", default="tasks.json", help="task JSON file")
    ap.add_argument("--db", default=None, help="override workbuddy db path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        tasks = load(args.source, args.db if args.source == "workbuddy" else args.file)
    except Exception as e:  # noqa: BLE001 — failures must be explicit
        msg = "tool failure: %s: %s" % (type(e).__name__, e)
        if args.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print("[EXPLICIT_FAILURE] %s" % msg, file=sys.stderr)
        return EXIT_FAIL
    report = scan(tasks)
    code = verdict_code(report)
    if args.json:
        print(json.dumps({
            "ok": True, "exit_code": code, "total": report["total"],
            "active": len(report["active"]), "paused": len(report["paused"]),
            "critical_paused": len(report["critical_paused"]),
            "critical_paused_names": [i["name"] for i in report["critical_paused"]],
        }, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
