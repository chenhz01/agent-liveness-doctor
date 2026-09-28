#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""threads.py — outreach thread ledger with due-date sweep.

The other half of silent failure. Liveness scans answer "is my machinery
alive?"; this answers "**does anyone look back at what we sent out?**"

Two directions — scanning only one loses half the failure modes:

  outbound : we sent, waiting on them.  Overdue -> nudge or close.
  inbound  : they replied / invited us, WE owe the next move.
             Overdue -> we broke a promise. (The most-ignored direction:
             "they answered on Sep 15 saying back after the wedding" and
             nobody ever followed up.)

Ledger: append-only JSONL, sha16 hash chain. Any edit or deletion of a
past line breaks the chain (`verify`).

Exit codes: 0 ok | 1 overdue exist | 2 ledger broken | 3 tool failure.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta

EXIT_OK, EXIT_OVERDUE, EXIT_BROKEN, EXIT_FAIL = 0, 1, 2, 3
OPEN_STATES = ("WAITING", "NUDGED")


def _sha16(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _parse_date(s):
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()


def _read(path):
    if not os.path.exists(path):
        return [], []
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    recs = []
    for i, ln in enumerate(lines):
        try:
            recs.append(json.loads(ln))
        except json.JSONDecodeError as e:
            raise RuntimeError("ledger line %d corrupted: %s" % (i + 1, e))
    return recs, lines


def _append(path, rec):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    _, lines = _read(path)
    rec = dict(rec)
    rec["prev"] = _sha16(lines[-1]) if lines else ""
    rec["sha16"] = _sha16(json.dumps(rec, ensure_ascii=False, sort_keys=True))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def _fold(recs):
    """Latest event per tid wins (append-only ledger, folded view)."""
    state = {}
    for r in recs:
        tid = r.get("tid")
        if not tid:
            continue
        cur = state.setdefault(tid, {})
        cur.update({k: v for k, v in r.items()
                    if k not in ("prev", "sha16", "event")})
        cur["_last_event"] = r.get("event", "add")
    return state


def _bucket(rec):
    if rec.get("status") in ("CLOSED", "REPLIED"):
        return "CLOSED", 0
    sent = _parse_date(rec["sent"])
    due = sent + timedelta(days=int(rec.get("expect_days", 5)))
    delta = (date.today() - due).days
    if delta > 0:
        return "OVERDUE", delta
    if delta >= -2:
        return "DUE_SOON", delta
    return "WAITING", delta


def cmd_add(args, path):
    sent = _parse_date(args.sent).isoformat()
    tid = args.tid or ("T-" + _sha16("%s|%s|%s" % (args.to, args.subject, sent)))
    _append(path, {
        "event": "add",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tid": tid, "to": args.to, "channel": args.channel,
        "subject": args.subject, "sent": sent,
        "expect_days": int(args.expect_days), "direction": args.direction,
        "promises": list(args.promise or []),
        "status": "WAITING", "note": args.note or "",
    })
    due = (_parse_date(sent) + timedelta(days=int(args.expect_days))).isoformat()
    print("registered %s -> %s (%s, %s) due %s" % (tid, args.to, args.channel,
                                                    args.direction, due))
    return EXIT_OK


def cmd_check(args, path):
    recs, _ = _read(path)
    state = _fold(recs)
    if not state:
        print("ledger empty (%s) — nothing to sweep." % path)
        return EXIT_OK
    rows = []
    for tid, r in state.items():
        if not args.all and r.get("status") not in OPEN_STATES:
            continue
        b, delta = _bucket(r)
        rows.append((b, delta, tid, r))
    order = {"OVERDUE": 0, "DUE_SOON": 1, "WAITING": 2, "CLOSED": 3}
    rows.sort(key=lambda x: (order.get(x[0], 9), -x[1]))
    overdue = [x for x in rows if x[0] == "OVERDUE"]
    icons = {"OVERDUE": "[OVERDUE]", "DUE_SOON": "[DUE-SOON]",
             "WAITING": "[waiting]", "CLOSED": "[closed]"}
    print("=" * 66)
    print("Thread sweep · %s | open=%d overdue=%d due-soon=%d"
          % (date.today().isoformat(), len(rows), len(overdue),
             sum(1 for x in rows if x[0] == "DUE_SOON")))
    print("-" * 66)
    for b, delta, tid, r in rows:
        if args.overdue_only and b != "OVERDUE":
            continue
        d = r.get("direction", "outbound")
        tag = "we owe action" if d == "inbound" else "awaiting their reply"
        print("%s %s  <%s>" % (icons[b], tid, tag))
        print("      %s -> %s | sent %s | due %s%s"
              % (r.get("to", "?"), r.get("subject", "?"), r.get("sent", "?"),
                 (_parse_date(r["sent"]) + timedelta(days=int(r.get("expect_days", 5)))).isoformat(),
                 ("  +%dd" % delta) if b == "OVERDUE" else ""))
        for p in r.get("promises", []) or []:
            print("      · promise: %s" % p)
    print("-" * 66)
    if overdue:
        ob = sum(1 for x in overdue if x[3].get("direction", "outbound") == "outbound")
        print("verdict: %d overdue — awaiting-reply %d (nudge/close), "
              "we-owe-action %d (respond now)." % (len(overdue), ob,
                                                    len(overdue) - ob))
    else:
        print("verdict: no overdue threads.")
    print("=" * 66)
    return EXIT_OVERDUE if overdue else EXIT_OK


def cmd_nice(args, path):
    _append(path, {"event": "nudge",
                   "ts": datetime.now().isoformat(timespec="seconds"),
                   "tid": args.tid, "status": "NUDGED"})
    print("nudged: %s" % args.tid)
    return EXIT_OK


def cmd_close(args, path):
    _append(path, {"event": "close",
                   "ts": datetime.now().isoformat(timespec="seconds"),
                   "tid": args.tid, "status": "CLOSED",
                   "close_reason": args.reason or ""})
    print("closed: %s (%s)" % (args.tid, args.reason or ""))
    return EXIT_OK


def cmd_verify(args, path):
    """Chain check. prev = sha16(prev raw line); sha16 = content fingerprint.

    NOTE: these two hashes use DIFFERENT inputs on purpose — prev guards
    line order/edits, sha16 guards this line's content. Getting them mixed
    up produces false chain-break alarms on clean ledgers (real bug we hit).
    """
    recs, lines = _read(path)
    if not lines:
        print("ledger empty, nothing to verify.")
        return EXIT_OK
    prev_link = ""
    for i, (ln, rec) in enumerate(zip(lines, recs)):
        if rec.get("prev", "") != prev_link:
            print("[BROKEN] line %d prev mismatch (expected %s, got %s)"
                  % (i + 1, prev_link or "(none)", rec.get("prev")))
            return EXIT_BROKEN
        own = rec.get("sha16")
        body = {k: v for k, v in rec.items() if k != "sha16"}
        if _sha16(json.dumps(body, ensure_ascii=False, sort_keys=True)) != own:
            print("[TAMPERED] line %d content fingerprint mismatch" % (i + 1))
            return EXIT_BROKEN
        prev_link = _sha16(ln)
    print("hash chain intact: %d lines verified." % len(lines))
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="Outreach thread ledger + due sweep")
    ap.add_argument("--path", default="threads.jsonl")
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("add")
    a.add_argument("--to", required=True)
    a.add_argument("--subject", required=True)
    a.add_argument("--channel", default="email")
    a.add_argument("--direction", choices=("outbound", "inbound"),
                   default="outbound")
    a.add_argument("--sent", default=date.today().isoformat())
    a.add_argument("--expect-days", type=int, default=5)
    a.add_argument("--promise", action="append", default=[])
    a.add_argument("--tid", default=None)
    a.add_argument("--note", default="")

    c = sub.add_parser("check")
    c.add_argument("--all", action="store_true")
    c.add_argument("--overdue-only", action="store_true")

    n = sub.add_parser("nudge")
    n.add_argument("tid")

    cl = sub.add_parser("close")
    cl.add_argument("tid")
    cl.add_argument("--reason", default="")

    sub.add_parser("list")
    sub.add_parser("verify")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return EXIT_FAIL
    try:
        fn = {"add": cmd_add, "check": cmd_check, "nudge": cmd_nice,
              "close": cmd_close, "verify": cmd_verify}.get(args.cmd)
        if fn is None:  # list
            recs, _ = _read(args.path)
            state = _fold(recs)
            print("%d threads: %s" % (len(state), args.path))
            for tid, r in state.items():
                b, _ = _bucket(r)
                print("  %-26s %-9s %s" % (tid, b, r.get("subject", "")[:24]))
            return EXIT_OK
        return fn(args, args.path)
    except RuntimeError as e:
        print("[EXPLICIT_FAILURE] %s" % e, file=sys.stderr)
        return EXIT_BROKEN
    except Exception as e:  # noqa: BLE001
        print("[EXPLICIT_FAILURE] %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
