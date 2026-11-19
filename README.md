# jiangshan-map-source-ledger

**A zero-dependency, offline reconciler for the citation ledger of [江山时序 / Jiangshan Atlas](https://github.com/DeanChensj/jiangshan-map).** It answers a question the atlas's own CI cannot: *is the published "unsourced" backlog still true?* Upstream's `audit_sources.js --check` only proves that every new record has a reference — it never notices a record that has since been cited but is still sitting in `baseline.json`, which silently depresses the coverage number the project reports to contributors.

`Upstream: https://github.com/DeanChensj/jiangshan-map`

## Why now

The atlas is a trending project (8.4k stars) with a crowd-sourced citation campaign: [issue #309](https://github.com/DeanChensj/jiangshan-map/issues/309) tracks progress against 1,454 records and asks volunteers to claim dynasties. On 2026-11-11 the maintainer reported **131 / 1,454** records cited. That denominator comes from `baseline.json`, a file the contributing guide says *"only shrinks"* ([data/sources/README.md#L15](https://github.com/DeanChensj/jiangshan-map/blob/main/data/sources/README.md#L15)) and that every contributor is told to regenerate with `--update` ([#L54](https://github.com/DeanChensj/jiangshan-map/blob/main/data/sources/README.md#L54)).

But the check that runs in CI is one-directional: `unsourced` records missing from the baseline are errors, while baseline entries that *already have* a citation are never compared ([scripts/audit_sources.js#L240-L246](https://github.com/DeanChensj/jiangshan-map/blob/main/scripts/audit_sources.js#L240-L246)). A forgotten `--update` therefore makes the ledger stale, double-counts a cited record, and under-reports coverage — with no failing build. This tool closes exactly that loop and nothing more.

## What it does

Given a checkout of the atlas, it reads `data/sources/*.json` and reports:

| Code | Severity | Meaning |
| --- | --- | --- |
| `STALE_BASELINE` | error | key is cited **and** still listed in `baseline.json` — coverage under-reported |
| `BASELINE_DUPLICATE` | error | the same key appears twice in `unsourced[]` |
| `UNKNOWN_WORK` | error | `w` is not registered in `works.json` |
| `EMPTY_LOC` | error | reference has no chapter / section |
| `CHAPTER_MISMATCH` | error | `卷N` in `loc` disagrees with `卷M` in the wikisource `url` (static, no network) |
| `DUPLICATE_CITATION` | warning | same `(work, loc)` cited twice for one record |
| `OVER_CITATION` | warning | more than `--max-refs` (default 3) references; the guide allows extras only when they complement or disagree |
| `PRIMARY_LOC_NO_VOLUME` | warning | a primary source's `loc` names no 卷 at all |
| `KEY_SHAPE` | warning | key is not `<kind>:<id>` with a known kind |
| `TOTAL_DRIFT` | warning | only with `--claimed-total`: the number quoted in the tracking issue no longer matches the reconciled ledger |

It also emits a per-dynasty coverage table computed from the two ledgers (`cited` / `effective baseline`), plus *reported* vs *reconciled* totals so the double count is visible.

### What it does not do

- It does not parse `js/map_data.js` or `js/lore_data.js`, so its denominator is the ledger's own universe (`cited + effective baseline`), not the full record universe. Upstream's script still owns that.
- It does not verify quotations against the source text; that needs the network and the [text checker](https://github.com/DeanChensj/jiangshan-map/blob/main/scripts/check_sources.js). Only the `卷` number is compared statically.
- It does not edit files, rewrite JSON, or run `--fix`. It is a read-only gate.

## Install / run

No dependencies, Python ≥ 3.8:

```bash
git clone --depth=1 https://github.com/linmoxi33/jiangshan-map-source-ledger.git
cd jiangshan-map-source-ledger
python3 source_ledger.py examples/in     # bundled real fixture, no network
```

Against a full checkout of the atlas:

```bash
python3 source_ledger.py /path/to/jiangshan-map
```

Against the repository at commit `853064e8` (2026-11-19), the real run was:

```text
cited records      : 374
baseline listed    : 1119 (effective 1119)
reconciled total   : 1493 (reported sum 1493)
coverage           : 25.05%
0 error(s), 44 warning(s)   # 36 OVER_CITATION + 8 PRIMARY_LOC_NO_VOLUME
```

(No stale entries existed at that commit — the tool exits 0. The failure mode it guards is latent, and the tests inject it: see `tests/test_ledger.py`.)

The bundled fixture in `examples/in/` is a real, unmodified copy of `works.json`, `baseline.json`, `tang.json`, `journeys.json` and `zhanguo.json` from that commit; `examples/out/report.json` is the exact output for it:

```bash
python3 source_ledger.py examples/in --json | python3 -c 'import json,sys; json.load(sys.stdin)'
# 106 cited, 1119 baseline, 1225 reconciled, 8.65% coverage, 0 errors, 15 warnings
```

### `--json` output (abridged)

```json
{
  "schema": 1,
  "records": {"cited": 106, "baseline_listed": 1119, "baseline_effective": 1119,
              "stale_baseline": 0, "reported_total": 1225, "reconciled_total": 1225,
              "coverage_pct": 8.65},
  "by_dynasty": [{"dynasty": "tang", "cited": 73, "unsourced": 0, "total": 73, "coverage_pct": 100.0}],
  "findings": [{"code": "OVER_CITATION", "severity": "warning", "file": "tang.json",
                "key": "milestone:tang:686", "detail": "7 references (soft limit 3: ...)"}],
  "summary": {"errors": 0, "warnings": 15}
}
```

### Options

| Flag | Default | Effect |
| --- | --- | --- |
| `--json` | off | emit only the JSON report on stdout |
| `--fail-on error\|warning\|never` | `error` | exit-1 threshold |
| `--max-refs N` | `3` | soft per-record reference limit |
| `--claimed-total N` | off | compare against a number quoted in an issue; warns on drift |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | no findings at the `--fail-on` threshold |
| `1` | findings at the threshold |
| `2` | usage error, missing `data/sources/`, or unreadable JSON |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

19 tests: 6 spawn the CLI (exit codes 0/1/2, `--json` parse, missing directory, stored-example parity), 4 assert the coverage arithmetic and per-dynasty buckets, 9 pin one rule each. No network, no third-party packages, no fixtures generated by an AI: every fixture is either a trimmed copy of the real upstream files or a minimal hand-written case for one rule.

## Limitations

- The denominator misses records that exist in `js/` but never entered either ledger (upstream forbids that, so it should be zero; this tool cannot prove it).
- `卷` comparison is numeric only; Chinese-numeral volumes (`卷六`) count as present but are not cross-checked against the URL.
- `OVER_CITATION` is a style warning, not a defect: 36 legitimate multi-source records trigger it at the default limit.
- Upstream may add the stale-baseline check later; if it does, this tool becomes redundant, which is the point.

## 本轮创新

First tool to treat the atlas's `baseline.json` as a **two-way ledger** rather than a one-way backlog: it computes *effective baseline* = listed − cited and exposes the double count that upstream's CI structurally cannot see (their loop only walks `unsourced`, `audit_sources.js#L240-L246`). Same commit, first run: 374 / 1493 = 25.05%, with the issue's 1,454 figure now provably stale (`--claimed-total 1454` warns). It is not a generic config linter — every rule comes from this repository's own data model and contribution guide.

## 与同类项目的差异

- `scripts/audit_sources.js` (upstream): record universe + one-way ratchet; no stale-baseline detection, no `loc`/URL cross-check.
- `scripts/check_sources.js` (upstream): needs network, checks chapter titles and dates against live pages; this tool is offline and only compares `卷` numbers.
- Generic JSON validators: cannot know that `unsourced[]` must shrink, that `w` must exist in `works.json`, or that keys are `<kind>:<dynasty>:<year>`.

## License

MIT. Upstream data files under `examples/in/` remain GPL-3.0, © DeanChensj and contributors; they are included unmodified as test input only.