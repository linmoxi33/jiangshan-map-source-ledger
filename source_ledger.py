#!/usr/bin/env python3
"""Reconcile a jiangshan-map citation ledger offline.

Reads <root>/data/sources/{works,baseline,<dynasty>,journeys,persons}.json and
reports (a) ledger drift the upstream CI cannot see -- a record that now has a
citation but is still listed in baseline.json -- and (b) structural defects in
the reference files. No network, no third-party packages.

Exit codes: 0 clean, 1 findings (per --fail-on), 2 usage / unreadable input.
"""
import argparse
import json
import os
import re
import sys

KINDS = ("milestone", "event", "journey", "person")
VOL_LOC = re.compile(r"卷\s*(?:第\s*)?(\d+|[零〇一二三四五六七八九十百千]+)")
VOL_URL = re.compile(r"卷0*(\d+)")
RESERVED = ("works.json", "baseline.json")


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def record_universe(sources_dir):
    """Map key -> (filename, [refs]) for every cited record."""
    cited = {}
    for name in sorted(os.listdir(sources_dir)):
        if not name.endswith(".json") or name in RESERVED:
            continue
        path = os.path.join(sources_dir, name)
        data = read_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"{name}: top level must be an object")
        for key, refs in data.items():
            if not isinstance(refs, list) or not refs:
                raise ValueError(f"{name}: {key} must map to a non-empty list")
            cited.setdefault(key, []).append((name, refs))
    return cited


def shape(key):
    parts = key.split(":")
    if len(parts) < 2 or parts[0] not in KINDS:
        return None
    return parts[0], parts[1]


def analyse(root, max_refs=3, claimed_total=None):
    sources = os.path.join(root, "data", "sources")
    if not os.path.isdir(sources):
        raise FileNotFoundError(f"no data/sources under {root!r}")
    works = read_json(os.path.join(sources, "works.json"))
    baseline_path = os.path.join(sources, "baseline.json")
    baseline = read_json(baseline_path).get("unsourced", []) if os.path.exists(baseline_path) else []
    cited = record_universe(sources)

    findings = []

    def add(code, severity, file, key, detail):
        findings.append({"code": code, "severity": severity, "file": file, "key": key, "detail": detail})

    seen_base = set()
    for key in baseline:
        if key in seen_base:
            add("BASELINE_DUPLICATE", "error", "baseline.json", key, "key appears twice in unsourced[]")
        seen_base.add(key)
        if key in cited:
            add("STALE_BASELINE", "error", "baseline.json", key,
                f"record is cited in {cited[key][0][0]} but still listed as unsourced; "
                "coverage is under-reported until `audit_sources.js --update` is run")

    for key, entries in sorted(cited.items()):
        parsed = shape(key)
        if parsed is None:
            add("KEY_SHAPE", "warning", entries[0][0], key, "key is not <kind>:<id> with a known kind")
            for name, refs in entries:
                for ref in refs:
                    if ref.get("w") not in works:
                        add("UNKNOWN_WORK", "error", name, key, f"work {ref.get('w')!r} is not registered in works.json")
            continue
        for name, refs in entries:
            sigs = [(r.get("w"), r.get("loc", "")) for r in refs]
            if len(sigs) != len(set(sigs)):
                add("DUPLICATE_CITATION", "warning", name, key, "same (work, loc) is cited more than once")
            if len(refs) > max_refs:
                add("OVER_CITATION", "warning", name, key,
                    f"{len(refs)} references (soft limit {max_refs}: extra ones must complement or disagree)")
            for ref in refs:
                work = ref.get("w")
                loc = (ref.get("loc") or "").strip()
                url = ref.get("url") or ""
                if work not in works:
                    add("UNKNOWN_WORK", "error", name, key, f"work {work!r} is not registered in works.json")
                if not loc:
                    add("EMPTY_LOC", "error", name, key, "reference has no loc (chapter / section)")
                elif work in works and works[work].get("kind") == "primary" and not VOL_LOC.search(loc):
                    add("PRIMARY_LOC_NO_VOLUME", "warning", name, key,
                        f"primary source loc has no 卷 number: {loc[:40]!r}")
                m_loc, m_url = VOL_LOC.search(loc), VOL_URL.search(url)
                if m_loc and m_url and m_loc.group(1).isdigit() and int(m_loc.group(1)) != int(m_url.group(1)):
                    add("CHAPTER_MISMATCH", "error", name, key,
                        f"loc says 卷{m_loc.group(1)} but url points at 卷{m_url.group(1)} ({url})")

    stale = [k for k in baseline if k in cited]
    effective_baseline = [k for k in baseline if k not in cited]
    reported_total = len(cited) + len(baseline)
    reconciled_total = len(cited) + len(effective_baseline)

    buckets = {}
    for key in cited:
        parsed = shape(key)
        bucket = parsed[1] if parsed else "malformed"
        if parsed and parsed[0] in ("journey", "person"):
            bucket = parsed[0]
        b = buckets.setdefault(bucket, {"cited": 0, "unsourced": 0})
        b["cited"] += 1
    for key in effective_baseline:
        parsed = shape(key)
        bucket = parsed[1] if parsed else "malformed"
        if parsed and parsed[0] in ("journey", "person"):
            bucket = parsed[0]
        b = buckets.setdefault(bucket, {"cited": 0, "unsourced": 0})
        b["unsourced"] += 1
    by_dynasty = []
    for name in sorted(buckets):
        row = buckets[name]
        total = row["cited"] + row["unsourced"]
        by_dynasty.append({"dynasty": name, "cited": row["cited"], "unsourced": row["unsourced"],
                           "total": total,
                           "coverage_pct": round(100.0 * row["cited"] / total, 2) if total else 0.0})

    if claimed_total is not None and claimed_total != reconciled_total:
        add("TOTAL_DRIFT", "warning", "baseline.json", "-",
            f"claimed total {claimed_total} != reconciled total {reconciled_total} "
            f"(cited {len(cited)} + effective baseline {len(effective_baseline)})")

    findings.sort(key=lambda f: (f["file"], f["key"], f["code"]))
    errors = sum(1 for f in findings if f["severity"] == "error")
    return {
        "schema": 1,
        "root": os.path.abspath(root),
        "claimed_total": claimed_total,
        "records": {
            "cited": len(cited),
            "baseline_listed": len(baseline),
            "baseline_effective": len(effective_baseline),
            "stale_baseline": len(stale),
            "reported_total": reported_total,
            "reconciled_total": reconciled_total,
            "coverage_pct": round(100.0 * len(cited) / reconciled_total, 2) if reconciled_total else 0.0,
        },
        "by_dynasty": by_dynasty,
        "findings": findings,
        "summary": {"errors": errors, "warnings": len(findings) - errors},
    }


def render(report):
    r = report["records"]
    out = [
        "source-ledger reconcile",
        f"  cited records      : {r['cited']}",
        f"  baseline listed    : {r['baseline_listed']} (effective {r['baseline_effective']})",
        f"  reconciled total   : {r['reconciled_total']} (reported sum {r['reported_total']})",
        f"  coverage           : {r['coverage_pct']}%",
        "",
        f"  {'dynasty':<12}{'cited':>7}{'unsourced':>11}{'total':>7}{'cover':>8}",
    ]
    for row in report["by_dynasty"]:
        out.append(f"  {row['dynasty']:<12}{row['cited']:>7}{row['unsourced']:>11}"
                   f"{row['total']:>7}{row['coverage_pct']:>7}%")
    out.append("")
    if not report["findings"]:
        out.append("  no findings")
    for f in report["findings"]:
        out.append(f"  {f['severity'].upper():<7} {f['code']:<22} {f['file']}:{f['key']}")
        out.append(f"          {f['detail']}")
    out.append("")
    out.append(f"  {report['summary']['errors']} error(s), {report['summary']['warnings']} warning(s)")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="source-ledger",
                                 description="Offline reconciliation of a jiangshan-map citation ledger.")
    ap.add_argument("root", help="repository root that contains data/sources/")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    ap.add_argument("--fail-on", choices=("error", "warning", "never"), default="error",
                    help="exit 1 threshold (default: error)")
    ap.add_argument("--max-refs", type=int, default=3, help="soft per-record reference limit (default 3)")
    ap.add_argument("--claimed-total", type=int, default=None,
                    help="total record count quoted in a tracking issue; report drift if it differs")
    args = ap.parse_args(argv)
    if args.max_refs < 1:
        ap.error("--max-refs must be >= 1")
    try:
        report = analyse(args.root, max_refs=args.max_refs, claimed_total=args.claimed_total)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"source-ledger: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render(report))
    if args.fail_on == "never":
        return 0
    if report["summary"]["errors"]:
        return 1
    if args.fail_on == "warning" and report["summary"]["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())