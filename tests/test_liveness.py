# tests/test_liveness.py — mutation-flavored tests: bad fixtures MUST fail,
# clean fixtures MUST pass. A check that always passes is as dangerous as
# one that always fails (we learned this the hard way).

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import liveness  # noqa: E402


def write_tasks(tmp, tasks):
    p = os.path.join(tmp, "tasks.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, ensure_ascii=False)
    return p


GOOD = [
    {"name": "reply-monitor x", "status": "ACTIVE",
     "schedule_type": "recurring", "rrule": "FREQ=DAILY;BYHOUR=18;BYMINUTE=0",
     "last_run": "2026-09-26T18:00:00Z"},
    {"name": "report builder", "status": "ACTIVE",
     "schedule_type": "recurring", "rrule": "FREQ=DAILY", "last_run": 1758901800000},
]


class TestClean(unittest.TestCase):
    def test_t1_all_active_passes(self):
        tmp = tempfile.mkdtemp()
        p = write_tasks(tmp, GOOD)
        tasks = liveness.load("generic-json", p)
        r = liveness.scan(tasks)
        self.assertEqual(liveness.verdict_code(r), liveness.EXIT_OK)

    def test_t2_epoch_ms_normalizes(self):
        tasks = liveness.load("generic-json", write_tasks(tempfile.mkdtemp(), GOOD))
        # 1758901800 s = 2025-09-26 15:50 UTC (tool renders epoch in fixed UTC,
        # not local tz — first CI run failed on local-time rendering)
        self.assertEqual(tasks[1]["last_run"], "2025-09-26 15:50")


class TestMutation(unittest.TestCase):
    def test_t3_paused_critical_detected(self):
        bad = GOOD + [{"name": "inbox sentinel", "status": "PAUSED",
                       "schedule_type": "recurring", "rrule": "FREQ=HOURLY",
                       "last_run": None}]
        tasks = liveness.load("generic-json",
                              write_tasks(tempfile.mkdtemp(), bad))
        r = liveness.scan(tasks)
        self.assertEqual(liveness.verdict_code(r), liveness.EXIT_CRITICAL)
        self.assertEqual(r["critical_paused"][0]["name"], "inbox sentinel")

    def test_t4_paused_noncritical_is_p1_not_p0(self):
        bad = GOOD + [{"name": "nightly cleanup", "status": "PAUSED",
                       "schedule_type": "recurring", "rrule": "FREQ=DAILY",
                       "last_run": "2026-09-01T00:00:00Z"}]
        r = liveness.scan(liveness.load(
            "generic-json", write_tasks(tempfile.mkdtemp(), bad)))
        self.assertEqual(liveness.verdict_code(r), liveness.EXIT_PAUSED)

    def test_t5_never_ran_flagged(self):
        bad = [{"name": "follow-up watcher", "status": "PAUSED",
                "schedule_type": "once", "scheduled_at": "2026-09-01T09:00",
                "last_run": None}]
        r = liveness.scan(liveness.load(
            "generic-json", write_tasks(tempfile.mkdtemp(), bad)))
        self.assertTrue(r["paused"][0]["never_ran"])

    def test_t6_word_boundary_not_required_but_safe(self):
        # "monitoring" and "reply-monitor" both classify; "monitory" also
        # matches substring rule — documented behavior, configurable via patterns.
        self.assertTrue(liveness.classify("Monitoring Bot"))
        self.assertFalse(liveness.classify("deploy website"))


class TestCli(unittest.TestCase):
    def test_t7_cli_exit_codes(self):
        tmp = tempfile.mkdtemp()
        p_ok = write_tasks(tmp, GOOD)
        self.assertEqual(liveness.main(["--source", "generic-json",
                                        "--file", p_ok]), liveness.EXIT_OK)
        p_bad = write_tasks(tmp, GOOD + [
            {"name": "inbox sentinel", "status": "PAUSED",
             "schedule_type": "recurring", "last_run": None}])
        self.assertEqual(liveness.main(["--source", "generic-json",
                                        "--file", p_bad]),
                         liveness.EXIT_CRITICAL)

    def test_t8_missing_file_is_explicit_failure_not_zero(self):
        self.assertEqual(liveness.main(["--source", "generic-json",
                                        "--file", "nope.json"]),
                         liveness.EXIT_FAIL)

    def test_t9_json_output_shape(self):
        import io, contextlib
        tmp = tempfile.mkdtemp()
        p = write_tasks(tmp, GOOD)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            liveness.main(["--source", "generic-json", "--file", p, "--json"])
        data = json.loads(buf.getvalue())
        self.assertTrue(data["ok"])
        self.assertIn("critical_paused", data)

    def test_t10_bad_schema_is_failure(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"wrong": 1}, f)
        self.assertEqual(liveness.main(["--source", "generic-json",
                                        "--file", p]), liveness.EXIT_FAIL)


if __name__ == "__main__":
    unittest.main()
