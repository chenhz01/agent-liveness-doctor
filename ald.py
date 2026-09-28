#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ald.py — agent-liveness-doctor unified CLI.

Two halves of silent failure, one entry point:

  liveness : are my scheduled jobs actually scheduled? (scheduler-level death)
  threads  : does anyone look back at what we sent out? (thread-level neglect)

Examples:
  python ald.py liveness --source generic-json --file tasks.json
  python ald.py liveness --source workbuddy
  python ald.py threads --path threads.jsonl check
"""

import sys

import liveness
import threads


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "liveness":
        rest = argv[1:]
        if not any(a in rest for a in ("--source",)):
            rest = ["--source", "generic-json"] + rest
        return liveness.main(rest)
    if argv and argv[0] == "threads":
        return threads.main(argv[1:])
    print(__doc__)
    return 0 if not argv else 2


if __name__ == "__main__":
    sys.exit(main())
