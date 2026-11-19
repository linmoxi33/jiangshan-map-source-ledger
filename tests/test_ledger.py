#!/usr/bin/env python3
"""Tests for source_ledger. Run: python3 -m unittest discover -s tests -v"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "source_ledger.py")
sys.path.insert(0, ROOT)

import source_ledger  # noqa: E402

WORKS = {"shiji": {"zh": "史记", "kind": "primary"}}


def write(root, name, payload):
    path = os.path.join(root, "data", "sources", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh, ensure_ascii=False)


def make_root(extra=None, baseline=None, works=None):
    root = tempfile.mkdtemp(prefix="ledger-")
    write(root, "works.json", works or WORKS)
    if baseline is not None:
        write(root, "baseline.json", {"unsourced": baseline})
    for name, payload in (extra or {}).items():
        write(root, name, payload)
    return root


def run(root, *args):
    return subprocess.run([sys.executable, TOOL, root, *args],
                          capture_output=True, text=True, timeout=30)


class CoverageTests(unittest.TestCase):
    def test_clean_ledger_is_rc0_and_half_coverage(self):
        root = make_root({"tang.json": {
            "milestone:tang:618": [{"w": "shiji", "loc": "卷1 高祖本纪"}],
            "event:tang:618b": [{"w": "shiji", "loc": "卷2 太宗本纪"}],
        }}, baseline=["milestone:tang:700", "event:tang:701"])
        report = source_ledger.analyse(root)
        self.assertEqual(report["records"]["cited"], 2)
        self.assertEqual(report["records"]["reconciled_total"], 4)
        self.assertEqual(report["records"]["coverage_pct"], 50.0)
        self.assertEqual(report["summary"]["errors"], 0)

    def test_stale_baseline_is_an_error_and_fixes_coverage(self):
        root = make_root({"tang.json": {
            "milestone:tang:618": [{"w": "shiji", "loc": "卷1 高祖本纪"}],
        }}, baseline=["milestone:tang:618", "milestone:tang:700"])
        report = source_ledger.analyse(root)
        codes = [f["code"] for f in report["findings"]]
        self.assertIn("STALE_BASELINE", codes)
        self.assertEqual(report["records"]["baseline_effective"], 1)
        self.assertEqual(report["records"]["reconciled_total"], 2)
        self.assertEqual(report["records"]["coverage_pct"], 50.0)

    def test_reported_total_double_counts_stale_entry(self):
        root = make_root({"tang.json": {"milestone:tang:1": [{"w": "shiji", "loc": "卷1 本纪"}]}},
                         baseline=["milestone:tang:1"])
        report = source_ledger.analyse(root)
        self.assertEqual(report["records"]["reported_total"], 2)
        self.assertEqual(report["records"]["reconciled_total"], 1)

    def test_by_dynasty_splits_journey_and_person(self):
        root = make_root({
            "tang.json": {"milestone:tang:618": [{"w": "shiji", "loc": "卷1 本纪"}]},
            "journeys.json": {"journey:xuanzang:629": [{"w": "shiji", "loc": "卷1 本纪"}]},
            "persons.json": {"person:libai": [{"w": "shiji", "loc": "卷1 本纪"}]},
        }, baseline=["event:tang:900"])
        rows = {r["dynasty"]: r for r in source_ledger.analyse(root)["by_dynasty"]}
        self.assertEqual(rows["tang"]["cited"], 1)
        self.assertEqual(rows["tang"]["unsourced"], 1)
        self.assertEqual(rows["journey"]["cited"], 1)
        self.assertEqual(rows["person"]["cited"], 1)


class StructuralTests(unittest.TestCase):
    def test_unknown_work_is_error(self):
        root = make_root({"tang.json": {"event:tang:1": [{"w": "nope", "loc": "卷1"}]}})
        codes = [f["code"] for f in source_ledger.analyse(root)["findings"]]
        self.assertIn("UNKNOWN_WORK", codes)

    def test_duplicate_citation_warns(self):
        ref = {"w": "shiji", "loc": "卷1 本纪"}
        root = make_root({"tang.json": {"event:tang:1": [ref, dict(ref)]}})
        self.assertIn("DUPLICATE_CITATION",
                      [f["code"] for f in source_ledger.analyse(root)["findings"]])

    def test_over_citation_honours_max_refs(self):
        refs = [{"w": "shiji", "loc": f"卷{i} 本纪"} for i in range(1, 6)]
        root = make_root({"tang.json": {"event:tang:1": refs}})
        codes = [f["code"] for f in source_ledger.analyse(root, max_refs=3)["findings"]]
        self.assertIn("OVER_CITATION", codes)
        codes6 = [f["code"] for f in source_ledger.analyse(root, max_refs=6)["findings"]]
        self.assertNotIn("OVER_CITATION", codes6)

    def test_chapter_mismatch_between_loc_and_url(self):
        root = make_root({"tang.json": {"event:tang:1": [{
            "w": "shiji", "loc": "卷29 河渠书",
            "url": "https://zh.wikisource.org/wiki/史記/卷110"}]}})
        entry = [f for f in source_ledger.analyse(root)["findings"] if f["code"] == "CHAPTER_MISMATCH"]
        self.assertEqual(len(entry), 1)
        self.assertIn("卷29", entry[0]["detail"])

    def test_empty_loc_is_error(self):
        root = make_root({"tang.json": {"event:tang:1": [{"w": "shiji", "loc": "  "}]}})
        self.assertIn("EMPTY_LOC", [f["code"] for f in source_ledger.analyse(root)["findings"]])

    def test_chinese_numeral_volume_counts_as_present(self):
        root = make_root({"tang.json": {"event:tang:1": [{"w": "shiji", "loc": "卷六 贞观十九年"}]}})
        codes = [f["code"] for f in source_ledger.analyse(root)["findings"]]
        self.assertNotIn("PRIMARY_LOC_NO_VOLUME", codes)

    def test_claimed_total_drift_warns(self):
        root = make_root({"tang.json": {"event:tang:1": [{"w": "shiji", "loc": "卷1 本纪"}]}},
                         baseline=[])
        codes = [f["code"] for f in source_ledger.analyse(root, claimed_total=99)["findings"]]
        self.assertIn("TOTAL_DRIFT", codes)
        codes_ok = [f["code"] for f in source_ledger.analyse(root, claimed_total=1)["findings"]]
        self.assertNotIn("TOTAL_DRIFT", codes_ok)

    def test_duplicate_baseline_key_is_error(self):
        root = make_root({"tang.json": {}}, baseline=["event:tang:1", "event:tang:1"])
        self.assertIn("BASELINE_DUPLICATE",
                      [f["code"] for f in source_ledger.analyse(root)["findings"]])

    def test_malformed_key_warns(self):
        root = make_root({"other.json": {"mystery-record": [{"w": "shiji", "loc": "卷1"}]}})
        self.assertIn("KEY_SHAPE", [f["code"] for f in source_ledger.analyse(root)["findings"]])


class CliBehaviourTests(unittest.TestCase):
    def test_json_stdout_is_machine_parseable(self):
        proc = run(os.path.join(ROOT, "examples", "in"), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schema"], 1)
        self.assertIn("records", payload)

    def test_error_finding_exits_1(self):
        root = make_root({"tang.json": {"event:tang:1": [{"w": "nope", "loc": "卷1"}]}})
        self.assertEqual(run(root).returncode, 1)

    def test_warning_only_exits_0_but_warning_threshold_exits_1(self):
        root = make_root({"tang.json": {"event:tang:1": [{"w": "shiji", "loc": "卷1"},
                                                          {"w": "shiji", "loc": "卷1"}]}})
        self.assertEqual(run(root).returncode, 0)
        self.assertEqual(run(root, "--fail-on", "warning").returncode, 1)
        self.assertEqual(run(root, "--fail-on", "never").returncode, 0)

    def test_missing_sources_dir_exits_2(self):
        self.assertEqual(run(tempfile.mkdtemp()).returncode, 2)

    def test_invalid_json_exits_2(self):
        root = make_root({"tang.json": "{not json"})
        self.assertEqual(run(root).returncode, 2)

    def test_example_report_matches_tool_output(self):
        proc = run(os.path.join(ROOT, "examples", "in"), "--json")
        with open(os.path.join(ROOT, "examples", "out", "report.json"), encoding="utf-8") as fh:
            stored = json.load(fh)
        fresh = json.loads(proc.stdout)
        for key in ("records", "by_dynasty", "summary"):
            self.assertEqual(stored[key], fresh[key])


if __name__ == "__main__":
    unittest.main()