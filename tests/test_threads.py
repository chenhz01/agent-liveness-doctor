# tests/test_threads.py — hash-chain ledger: tamper MUST be caught,
# deletion MUST be caught, clean ledger MUST verify. (We shipped a first
# version where verify and append used different chain definitions and the
# clean ledger false-alarmed. These tests pin both directions.)

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threads  # noqa: E402


def fresh(tmp):
    return os.path.join(tmp, "t.jsonl")


class TestLedger(unittest.TestCase):
    def test_t1_add_check_verify_clean(self):
        p = fresh(tempfile.mkdtemp())
        threads.main(["--path", p, "add", "--to", "a@x.com", "--subject", "s1",
                      "--sent", "2026-09-26", "--expect-days", "5"])
        self.assertEqual(threads.main(["--path", p, "verify"]), threads.EXIT_OK)

    def test_t2_overdue_detected(self):
        p = fresh(tempfile.mkdtemp())
        threads.main(["--path", p, "add", "--to", "a@x.com", "--subject", "old",
                      "--sent", "2026-09-01", "--expect-days", "5"])
        self.assertEqual(threads.main(["--path", p, "check"]),
                         threads.EXIT_OVERDUE)

    def test_t3_directions_distinguished(self):
        p = fresh(tempfile.mkdtemp())
        threads.main(["--path", p, "add", "--to", "a@x.com", "--subject", "w",
                      "--direction", "outbound", "--sent", "2026-09-01"])
        threads.main(["--path", p, "add", "--to", "b@x.com", "--subject", "i",
                      "--direction", "inbound", "--sent", "2026-09-01"])
        recs, _ = threads._read(p)
        state = threads._fold(recs)
        dirs = sorted(r["direction"] for r in state.values())
        self.assertEqual(dirs, ["inbound", "outbound"])

    def test_t4_close_reduces_overdue(self):
        p = fresh(tempfile.mkdtemp())
        threads.main(["--path", p, "add", "--to", "a@x.com", "--subject", "x",
                      "--tid", "T-1", "--sent", "2026-09-01"])
        before = threads.main(["--path", p, "check"])
        self.assertEqual(before, threads.EXIT_OVERDUE)
        threads.main(["--path", p, "close", "T-1", "--reason", "done"])
        self.assertEqual(threads.main(["--path", p, "check"]), threads.EXIT_OK)

    def test_t5_tamper_caught(self):
        import json
        p = fresh(tempfile.mkdtemp())
        threads.main(["--path", p, "add", "--to", "a@x.com", "--subject", "x",
                      "--sent", "2026-09-26"])
        lines = open(p, encoding="utf-8").read().splitlines()
        r = json.loads(lines[0])
        r["to"] = "hacked@evil.com"
        lines[0] = json.dumps(r, ensure_ascii=False, sort_keys=True)
        open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        self.assertEqual(threads.main(["--path", p, "verify"]), threads.EXIT_BROKEN)

    def test_t6_deletion_caught(self):
        p = fresh(tempfile.mkdtemp())
        for i in range(3):
            threads.main(["--path", p, "add", "--to", "%d@x.com" % i,
                          "--subject", "s%d" % i, "--sent", "2026-09-26"])
        lines = open(p, encoding="utf-8").read().splitlines()
        open(p, "w", encoding="utf-8").write(
            "\n".join([lines[0], lines[2]]) + "\n")
        self.assertEqual(threads.main(["--path", p, "verify"]), threads.EXIT_BROKEN)

    def test_t7_corrupt_json_is_broken_not_silent(self):
        p = fresh(tempfile.mkdtemp())
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json\n")
        self.assertEqual(threads.main(["--path", p, "check"]),
                         threads.EXIT_BROKEN)

    def test_t8_missing_subcommand_is_failure(self):
        self.assertEqual(threads.main([]), threads.EXIT_FAIL)


if __name__ == "__main__":
    unittest.main()
