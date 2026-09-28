# agent-liveness-doctor

**Your automation died three weeks ago. Nobody noticed.**

That's not a hypothetical. We ran an AI assistant with 44 scheduled jobs —
reply monitors, follow-up watchers, sentinels. One day we asked a simple
question: "why wasn't that email ever followed up?" The answer: **43 of the
44 jobs were paused. Zero notifications. Zero audit trail.** The rules
existed. The jobs existed. Nothing watched the watchers.

`agent-liveness-doctor` is the tool we built the same day. Two parts, one
blind spot each:

| Part | Question it answers | The failure it catches |
|---|---|---|
| `liveness` | Are my scheduled jobs *actually scheduled*? | Jobs that exist on paper but the scheduler will never fire. Status shows `PAUSED`, nothing alerts, damage is silent. |
| `threads` | Does anyone look back at what we sent out? | Outreach threads that die quietly — including the *reverse* direction everybody forgets: they replied and **you** owe the next move. |

> Monitoring usually covers failure shape #1 (ran-but-failed) and #2
> (heartbeat stopped). Shape #3 — *never scheduled at all* — is the blind
> spot, because every monitoring tool assumes its own scheduler is alive.
> This tool is the check on that assumption.

Zero dependencies. Stdlib Python only. Read-only.

## Quick start

```bash
# 1) liveness scan on any system that can export a JSON task list
python ald.py liveness --file tasks.json          # exit 2 = critical watchers dead

# 2) WorkBuddy desktop agent? Point it at your db (read-only)
python ald.py liveness --source workbuddy

# 3) thread ledger: register a sent thread, then sweep for overdue
python ald.py threads add --to "client@acme.com" --subject "proposal" \
    --direction outbound --sent 2026-09-28 --expect-days 5 \
    --promise "send revised quote"
python ald.py threads check                        # exit 1 = overdue exist
python ald.py threads verify                       # hash-chain integrity
```

A healthy run looks boring. Here is a sick one:

```text
total jobs     : 5
active         : 2
inactive       : 3   <- no notification was ever sent about these
critical dead  : 2   <- monitors/reply-watchers: silent damage
------------------------------------------------------------------
[P0] critical jobs currently dead:
  x inbox sentinel
      schedule=FREQ=HOURLY;INTERVAL=2 | last_run=no record | never_ran=yes
```

## The JSON format (bring your own agent)

Any scheduler, agent framework, or cron wrapper can export this:

```json
{
  "tasks": [
    {"name": "reply-monitor issue #4212", "status": "ACTIVE",
     "schedule_type": "recurring", "rrule": "FREQ=DAILY;BYHOUR=18;BYMINUTE=0",
     "last_run": "2026-09-26T18:00:11Z", "next_run": "2026-09-27T18:00:00Z"}
  ]
}
```

- `status`: `ACTIVE` / anything else counts as inactive (`PAUSED`, `DISABLED`…)
- `last_run`: ISO string, epoch seconds, or epoch milliseconds — all normalized
- names containing watcher words (`monitor`, `watch`, `follow-up`, `reply`,
  `heartbeat`, `sentinel`, plus their Chinese equivalents) are classified
  **critical** — their death is P0. Tune with your own patterns in code.

Exit codes: `0` healthy · `1` inactive jobs · `2` **critical** jobs dead ·
`3` tool failure. Tool failure never masquerades as success — a scanner
that exits 0 when it crashes is a lie detector that always says "truth".

## Threads: the direction everybody forgets

Most follow-up trackers answer "did they reply?". Half of real-world
neglect is the other direction — **they replied, and we never moved**:

```bash
python ald.py threads add --to "editor@journal.org" --subject "resubmit PR" \
    --direction inbound --sent 2026-09-09 --expect-days 7 \
    --promise "resubmit per CONTRIBUTING"
python ald.py threads check
```

```text
[OVERDUE] T-DellZhang-PR重投  <we owe action>
      Dell Zhang -> PR 重投未执行 | sent 2026-09-09 | due 2026-09-16  +12d
      · promise: resubmit per CONTRIBUTING
```

The ledger is append-only JSONL with a sha16 hash chain — editing or
deleting a past line breaks `verify`. We know because our first version
shipped with mismatched chain definitions and false-alarmed on clean
ledgers; the tests now pin both directions (tamper caught, deletion caught,
clean passes).

## The five-layer autopsy (method behind the tool)

When "something that should have happened didn't", "I forgot" is a symptom,
never a root cause. Walk the layers — at least one will be broken:

1. **Memory** — does the account of this exist anywhere, or only in heads?
2. **Rules** — does a written rule require it? Rules that cover only half a
   lifecycle (e.g. "promises we made" but not "replies we received") leak.
3. **Carrier** — is the automation *alive*? ← this tool
4. **Audit trail** — if something was paused/disabled, is there a record of
   who/why/when? No trail = treat as incident, not as intent.
5. **Alerting** — when it dies, who finds out? If the answer is "nobody",
   you have silent failure: not doing it wrong, but *wrong with no witness*.

## Honest limits

- **No cron-native adapter yet.** Cron has no last-run history by design;
  wrap your crontab with a JSON exporter (or run the commands through a
  tiny logger) and point `--source generic-json` at it. Contributions welcome.
- **No daemon, no UI.** This is a scan you run (cron it, CI it, or hook it
  into your agent's boot sequence). It reports; *you* decide. It will never
  auto-restart your jobs — bulk restarts hide the root cause.
- **Windows/local-time nuance.** Epoch timestamps render in **fixed UTC** (identical on every machine — the first CI run caught a local-time bug here); ISO strings render as-is.

## Tests & CI

18 mutation-flavored tests: bad fixtures must fail, clean fixtures must
pass, tampered ledgers must be caught, tool crash must never exit 0.

```bash
python -m unittest discover tests
```

## Collaboration

We build agent-reliability tooling in the open — the scanner you see here is
the free layer. **What's *not* in this repo** (stated plainly, no teaser):
our internal incident rules, the evidence-engine that tracks whether fixed
failures stay fixed, and host-specific deep integrations.

| Tier | What you get | How |
|---|---|---|
| 🌱 Free (this repo) | Liveness scanner, thread ledger, JSON schema, five-layer autopsy method | Clone and run |
| 🔑 Collaboration | Deep integration into *your* agent framework: custom adapters, boot-time gates, CI wiring, incident postmortems from real ops | Email **hcac4735@agent.qq.com** |
| 💎 Private | Never published — internal methodology and case files | — |

If this tool caught a dead job for you, an issue or PR saying *where* would
help the next person more than a star. Both are welcome.

## License

MIT — free to use, modify, and ship. Attribution appreciated, never required.
See [LICENSE](LICENSE).
